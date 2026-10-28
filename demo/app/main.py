"""Aria agentic-shopping — FastAPI serving layer (builder B3).

Owns the HTTP API, the static UI (``demo/ui/``) and the app factory.  B1/B2
modules (``embeddings``, ``retrieval``, ``agent``, ``tools``, ...) are imported
**lazily inside request handlers** so this module imports cleanly even before
those modules land, and every optional dependency (faster-whisper, CLIP,
pytesseract) degrades gracefully instead of crashing.

Run:
    python -m app.main --selftest      # imports + health-logic check, exits 0
    python demo/run.py                 # uvicorn on :8000 (single worker)
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

DEMO_ROOT = Path(__file__).resolve().parent.parent          # .../demo
UI_DIR = DEMO_ROOT / "ui"
REPO_ROOT = DEMO_ROOT.parent                                # .../agentic-shopping

# Make ``import app.*`` work regardless of how we were launched.
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))


# --------------------------------------------------------------------------- #
# Lazy module loading (B1/B2 may still be under construction)
# --------------------------------------------------------------------------- #
_MOD_CACHE: Dict[str, Any] = {}


def _try_import(name: str) -> Any:
    """Import a module by name, caching ``None`` on failure. Never raises."""
    if name not in _MOD_CACHE:
        import importlib

        try:
            _MOD_CACHE[name] = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - offline / not-yet-landed module
            log.warning("optional import %s failed: %s", name, exc)
            _MOD_CACHE[name] = None
    return _MOD_CACHE[name]


def _db() -> Any:
    return _try_import("app.db")


# --------------------------------------------------------------------------- #
# Currency policy — the catalogue is Amazon US, so everything we *display* is
# USD.  (Users may still type a rupee amount such as "3000 rs"; the parser
# accepts it, the UI never shows it.)
# --------------------------------------------------------------------------- #
DEFAULT_BUDGET = 2_000.0  # $, session cap (same default as guardrails/tools)

_RS_WORD = re.compile(r"(?<![A-Za-z])(?:rs\.?|inr)\s*(\d)", re.I)


def _to_usd(text: str) -> str:
    """Normalise rupee spellings in generated text to the USD display format.

    Only the currency *symbol* changes — amounts are never rewritten — so no
    number the model produced is altered.
    """
    if not text:
        return text
    out = str(text).replace("₹", "$")
    out = _RS_WORD.sub(lambda m: "$" + m.group(1), out)
    return out


# --------------------------------------------------------------------------- #
# Agent system prompt overlay (app/agent.py is owned by another builder; the
# serving layer applies the USD policy without touching that file).
# --------------------------------------------------------------------------- #
_AGENT_PROMPT_PATCHES = (
    ("for an e-commerce marketplace",
     "for the US Amazon marketplace (catalogue prices are in USD)"),
    ("the $2,000 session budget", "the $2,000 session budget"),
)


def _agent_module() -> Any:
    """Import app.agent and apply the USD policy to its system prompt."""
    agent = _try_import("app.agent")
    if agent is None or getattr(agent, "_aria_usd_policy", False):
        return agent
    try:
        prompt = getattr(agent, "SYSTEM_PROMPT", "") or ""
        for old, new in _AGENT_PROMPT_PATCHES:
            prompt = prompt.replace(old, new)
        agent.SYSTEM_PROMPT = prompt
        agent._aria_usd_policy = True
    except Exception as exc:  # noqa: BLE001 - never break the serving path
        log.warning("could not apply the USD policy to the agent prompt: %s", exc)
    return agent


# --------------------------------------------------------------------------- #
# Connection + schema helpers
# --------------------------------------------------------------------------- #
_SCHEMA_INITED = False


def _connect():
    db = _db()
    if db is None:  # pragma: no cover - db.py is always present in this repo
        raise RuntimeError("app.db unavailable")
    conn = db.connect()
    global _SCHEMA_INITED
    if not _SCHEMA_INITED:
        try:
            db.init_schema(conn)
        except Exception as exc:  # noqa: BLE001
            log.warning("schema init failed (continuing): %s", exc)
        _SCHEMA_INITED = True
    return conn


# --------------------------------------------------------------------------- #
# Session state (canonical shape consumed by the UI transparency panel)
# --------------------------------------------------------------------------- #
DEFAULT_STATE: Dict[str, Any] = {
    "constraints": {},          # parsed constraints from retrieval.parse_constraints
    "free_text": "",
    "cart": [],                 # [{product_id, qty, title, price, unit_price}]
    "last_hits": [],            # [{product_id, title, price, rating, score}]
    "product_ids": [],
    "recent_citations": [],
    "flags": [],
    "latency_ms": None,
    "tokens_in": 0,
    "tokens_out": 0,
}


def _load_state(conn, session_id: str) -> Dict[str, Any]:
    """Read and normalise a session's persisted state (never raises)."""
    state = dict(DEFAULT_STATE)
    try:
        row = conn.execute(
            "SELECT state FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row and row["state"]:
            raw = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
            if isinstance(raw, dict):
                state.update(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("state load failed for %s: %s", session_id, exc)
    return state


def _save_state(conn, session_id: str, state: Dict[str, Any]) -> None:
    try:
        conn.execute(
            "UPDATE sessions SET state=?, updated_ts=? WHERE session_id=?",
            (json.dumps(state, ensure_ascii=False), time.time(), session_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("state save failed for %s: %s", session_id, exc)


def _ensure_session(conn, session_id: str, user_id: Optional[str]) -> Dict[str, Any]:
    now = time.time()
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sessions(session_id,user_id,state,created_ts,updated_ts)"
            " VALUES(?,?,?,?,?)",
            (session_id, user_id, json.dumps({}), now, now),
        )
        conn.commit()
        return {"session_id": session_id, "user_id": user_id, "state": {},
                "created_ts": now, "updated_ts": now}
    d = dict(row)
    d["state"] = _load_state(conn, session_id)
    return d


# --------------------------------------------------------------------------- #
# Product helpers (retrieval-first, degrade to raw db)
# --------------------------------------------------------------------------- #
def _product_summary(conn, product_id: str) -> Optional[Dict[str, Any]]:
    ret = _try_import("app.retrieval")
    if ret is not None and hasattr(ret, "get_product"):
        try:
            p = ret.get_product(conn, product_id)
            if p:
                return p
        except Exception as exc:  # noqa: BLE001
            log.debug("retrieval.get_product failed for %s: %s", product_id, exc)
    db = _db()
    row = db.product_row(conn, product_id)
    return db.product_dict(row) if row else None


def _reviews(conn, product_id: str, limit: int = 5,
             min_rating: Optional[float] = None) -> list:
    ret = _try_import("app.retrieval")
    if ret is not None and hasattr(ret, "get_reviews"):
        try:
            return ret.get_reviews(conn, product_id, limit=limit,
                                   min_rating=min_rating) or []
        except Exception as exc:  # noqa: BLE001
            log.debug("retrieval.get_reviews failed: %s", exc)
    q = "SELECT * FROM reviews WHERE product_id=? "
    args: list = [product_id]
    if min_rating is not None:
        q += "AND rating>=? "
        args.append(min_rating)
    q += "ORDER BY helpful DESC, rating DESC LIMIT ?"
    args.append(limit)
    try:
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    except Exception:  # noqa: BLE001 - table may not exist yet
        return []


def _variants(conn, product_id: str) -> list:
    try:
        rows = conn.execute(
            "SELECT * FROM product_variants WHERE product_id=?", (product_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["attributes"] = _jload(d.get("attributes"), {})
            out.append(d)
        return out
    except Exception:  # noqa: BLE001
        return []


def _product_citations(product_id: str, product: Optional[dict]) -> list:
    if not product:
        return []
    cites = []
    for field in ("price", "rating", "brand", "title"):
        val = product.get(field)
        if val is not None:
            cites.append({
                "product_id": product_id,
                "field": field,
                "quote": str(product.get("title") or "")[:80],
                "value": val,
            })
    return cites


def _jload(s: Any, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s) if isinstance(s, str) else s
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# Cart / checkout (mock commerce, direct DB writes)
# --------------------------------------------------------------------------- #
def _open_cart(conn, session_id: str, user_id: Optional[str]) -> Dict[str, Any]:
    now = time.time()
    row = conn.execute(
        "SELECT * FROM carts WHERE session_id=? AND status='open' ORDER BY created_ts DESC",
        (session_id,),
    ).fetchone()
    if row is None:
        cart_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO carts(cart_id,session_id,user_id,status,created_ts,updated_ts)"
            " VALUES(?,?,?,?,?,?)",
            (cart_id, session_id, user_id, "open", now, now),
        )
        conn.commit()
        return {"cart_id": cart_id, "session_id": session_id, "user_id": user_id,
                "status": "open", "created_ts": now, "updated_ts": now}
    return dict(row)


def _cart_view(conn, session_id: str) -> Dict[str, Any]:
    cart = _open_cart(conn, session_id, None)
    items, total = [], 0.0
    try:
        rows = conn.execute(
            "SELECT ci.*, p.title, p.price, p.price_mrp, p.rating, p.image_url, p.brand"
            " FROM cart_items ci LEFT JOIN products p ON p.product_id=ci.product_id"
            " WHERE ci.cart_id=?",
            (cart["cart_id"],),
        ).fetchall()
        for r in rows:
            d = dict(r)
            unit = d.get("unit_price") or d.get("price") or 0
            d["unit_price"] = unit
            d["line_total"] = round(unit * (d.get("qty") or 1), 2)
            items.append(d)
            total += d["line_total"]
    except Exception as exc:  # noqa: BLE001
        log.warning("cart view failed: %s", exc)
    return {"cart_id": cart["cart_id"], "items": items,
            "total": round(total, 2), "count": len(items)}


_METRO = {"new york", "los angeles", "chicago", "houston", "phoenix",
          "philadelphia", "san antonio", "san diego", "dallas", "austin",
          "seattle", "boston", "miami", "denver", "san francisco", "atlanta"}


def _eta_days(ship_to: str) -> int:
    city = (ship_to or "").lower()
    for m in _METRO:
        if m in city:
            return 2
    return 5


# --------------------------------------------------------------------------- #
# Agent streaming (lazy import of app.agent)
# --------------------------------------------------------------------------- #
def _sse(event: Dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _usd_event(event: Any) -> Any:
    """Apply the USD display policy to one agent event (never raises)."""
    if not isinstance(event, dict):
        return event
    out = dict(event)
    try:
        if isinstance(out.get("text"), str):
            out["text"] = _to_usd(out["text"])
        if isinstance(out.get("answer"), str):
            out["answer"] = _to_usd(out["answer"])
    except Exception as exc:  # noqa: BLE001
        log.debug("usd normalisation failed: %s", exc)
    return out


def _empty_done(answer: str = "", flags: Optional[list] = None) -> Dict[str, Any]:
    return {"type": "done", "answer": answer, "citations": [], "product_ids": [],
            "flags": flags or [], "latency_ms": 0, "tokens_in": 0, "tokens_out": 0}


def _parse_constraints(message: str) -> Dict[str, Any]:
    """Parsed constraints for the user message (USD amounts, deterministic).

    Uses B1's ``retrieval.parse_constraints`` when available (it understands
    Hinglish/vernacular phrasing), and falls back to the offline parser in
    ``app.tools`` so the panel is never empty for a simple price query.
    """
    out: Dict[str, Any] = {}
    ret = _try_import("app.retrieval")
    if ret is not None and hasattr(ret, "parse_constraints"):
        try:
            out = ret.parse_constraints(message) or {}
        except Exception as exc:  # noqa: BLE001
            log.debug("parse_constraints failed: %s", exc)
    if not out or not out.get("price_max") and not out.get("price_min"):
        tools = _try_import("app.tools")
        if tools is not None and hasattr(tools, "_local_constraints"):
            try:
                fallback = tools._local_constraints(message) or {}
                for k, v in fallback.items():
                    if v is not None and not out.get(k):
                        out[k] = v
            except Exception as exc:  # noqa: BLE001
                log.debug("local constraint parse failed: %s", exc)
    if not out.get("attributes"):
        out.pop("attributes", None)
    return out


def _store_session_meta(conn, session_id: str, message: str,
                        done: Optional[Dict[str, Any]] = None,
                        tools: Optional[list] = None) -> Dict[str, Any]:
    """Persist the parsed constraints + turn metrics for a session so the UI
    transparency panel can render them (min $x / max $x, latency, tokens).

    Stored in a B4-owned side table: ``app.agent`` re-persists ``sessions.state``
    after the turn ends, which would otherwise clobber a write to that column.
    Never raises.
    """
    constraints = _parse_constraints(message)
    payload: Dict[str, Any] = {"constraints": constraints}
    if done:
        payload.update({
            "latency_ms": done.get("latency_ms"),
            "tokens_in": done.get("tokens_in") or 0,
            "tokens_out": done.get("tokens_out") or 0,
            "flags": done.get("flags") or [],
            "citations": len(done.get("citations") or []),
            "product_ids": done.get("product_ids") or [],
        })
    if tools:
        payload["tools"] = list(tools)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS b4_session_meta ("
            " session_id TEXT PRIMARY KEY, payload TEXT, updated_ts REAL)")
        conn.execute(
            "INSERT OR REPLACE INTO b4_session_meta(session_id, payload, updated_ts)"
            " VALUES(?,?,?)",
            (session_id, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("session meta persist failed for %s: %s", session_id, exc)
    return payload


def _load_session_meta(conn, session_id: str) -> Dict[str, Any]:
    """Read back the metadata stored by :func:`_store_session_meta`."""
    try:
        row = conn.execute(
            "SELECT payload FROM b4_session_meta WHERE session_id=?",
            (session_id,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.debug("session meta load failed for %s: %s", session_id, exc)
        return {}
    if not row:
        return {}
    data = _jload(row["payload"], {})
    return data if isinstance(data, dict) else {}


def _fallback_turn(conn, session_id: str, message: str) -> Iterator[Dict[str, Any]]:
    """Deterministic fallback when app.agent is not importable yet."""
    ret = _try_import("app.retrieval")
    product_ids: list = []
    if ret is not None and hasattr(ret, "hybrid_search"):
        try:
            hits = ret.hybrid_search(conn, message, k=3)
            product_ids = [h.product_id for h in hits]
        except Exception:  # noqa: BLE001
            product_ids = []
    answer = (
        "I found a few matches for your request. "
        "(Running in offline fallback mode — the reasoning agent is not wired up yet.)"
    )
    yield {"type": "tool", "name": "search_products",
           "args": {"query": message, "k": 3}}
    yield {"type": "done", "answer": answer, "citations": [],
           "product_ids": product_ids, "flags": ["agent_unavailable"],
           "latency_ms": 0, "tokens_in": 0, "tokens_out": 0}


def _run_agent_stream(conn, session_id: str, user_id: Optional[str],
                      message: str) -> Iterator[Dict[str, Any]]:
    agent = _agent_module()
    if agent is None or not hasattr(agent, "run_turn"):
        yield from _fallback_turn(conn, session_id, message)
        return
    try:
        result = agent.run_turn(conn, session_id, user_id, message, stream=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("run_turn raised")
        yield _empty_done(f"Agent error: {exc}", flags=["agent_error"])
        return
    # B2 convention: stream=True returns {"type": "stream", "events": <gen>}.
    # Be defensive against the other plausible shapes too.
    gen = None
    if isinstance(result, dict) and "events" in result:
        gen = result["events"]
    elif isinstance(result, dict):
        yield result
        return
    else:
        gen = result
    try:
        for event in gen:
            if isinstance(event, dict):
                yield event
    except Exception as exc:  # noqa: BLE001
        log.exception("stream iteration failed")
        yield _empty_done(f"Stream error: {exc}", flags=["stream_error"])


def _run_agent_sync(conn, session_id: str, user_id: Optional[str],
                    message: str) -> Dict[str, Any]:
    agent = _agent_module()
    if agent is None or not hasattr(agent, "run_turn"):
        final: Dict[str, Any] = {}
        for ev in _fallback_turn(conn, session_id, message):
            final = ev
        return final or _empty_done("no events")
    try:
        result = agent.run_turn(conn, session_id, user_id, message, stream=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("run_turn (sync) raised")
        return _empty_done(f"Agent error: {exc}", flags=["agent_error"])
    if isinstance(result, dict):
        # B2 attaches the full event trace under "events"; strip it for a
        # clean single JSON object.
        if result.get("type") == "done":
            result = dict(result)
            result.pop("events", None)
        return result
    # a generator was returned despite stream=False — drain to the final event
    final = {}
    for ev in result:
        if isinstance(ev, dict):
            final = ev
    if isinstance(final, dict):
        final.pop("events", None)
    return final or _empty_done("no events")


# --------------------------------------------------------------------------- #
# Optional transcription / vision helpers
# --------------------------------------------------------------------------- #
_WHISPER = None


def _whisper_model():
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER
    try:
        from faster_whisper import WhisperModel

        _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001
        log.warning("faster-whisper unavailable: %s", exc)
        _WHISPER = False
    return _WHISPER


def _transcribe(audio_bytes: bytes) -> tuple:
    model = _whisper_model()
    if not model:
        return None, "whisper not installed (install faster-whisper or openai-whisper)"
    try:
        segments, _info = model.transcribe(io.BytesIO(audio_bytes))
        text = " ".join(getattr(s, "text", "") for s in segments).strip()
        return (text or None), None
    except Exception as exc:  # noqa: BLE001
        return None, f"transcription failed: {exc}"


def _clip_encode(pil_image) -> tuple:
    emb = _try_import("app.embeddings")
    if emb is None:
        return None, "app.embeddings unavailable"
    if hasattr(emb, "encode_images"):
        try:
            vecs = emb.encode_images([pil_image])
            if vecs is not None and len(vecs):
                return vecs[0], None
        except Exception as exc:  # noqa: BLE001
            log.debug("encode_images failed: %s", exc)
    try:
        model = emb.get_clip_model()
        if model is not None and hasattr(model, "encode"):
            vec = model.encode([pil_image])
            if vec is not None and len(vec):
                return vec[0], None
    except Exception as exc:  # noqa: BLE001
        log.debug("clip_model.encode failed: %s", exc)
    return None, "CLIP image encoder unavailable"


def _ocr(pil_image) -> Optional[str]:
    try:
        import pytesseract

        text = pytesseract.image_to_string(pil_image)
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001
        log.debug("OCR unavailable: %s", exc)
        return None


def _decode_image(image_base64: str):
    try:
        raw = base64.b64decode(image_base64)
        from PIL import Image

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img, None
    except Exception as exc:  # noqa: BLE001
        return None, f"could not decode image: {exc}"


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    app = FastAPI(title="Aria", description="Agentic shopping prototype", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------- health
    @app.get("/api/health")
    async def health():
        ok, n_products = False, 0
        try:
            conn = _connect()
            n_products = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
            ok = True
            conn.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("health check failed: %s", exc)
        return {"status": "ok" if ok else "degraded", "db": ok,
                "products": n_products, "time": time.time()}

    # --------------------------------------------------------------- chat
    @app.post("/api/chat")
    async def chat(request: Request):
        body = await _body(request)
        message = (body.get("message") or "").strip()
        if not message:
            return JSONResponse(status_code=400, content={"error": "message is required"})
        session_id = body.get("session_id") or uuid.uuid4().hex
        user_id = body.get("user_id")
        stream = body.get("stream", True)

        conn = _connect()
        _ensure_session(conn, session_id, user_id)

        if not stream:
            result = _usd_event(_run_agent_sync(conn, session_id, user_id, message))
            meta = _store_session_meta(conn, session_id, message,
                                       result if isinstance(result, dict) else None)
            if isinstance(result, dict) and meta.get("constraints"):
                result["constraints"] = meta["constraints"]
            conn.close()
            return result

        def gen() -> Iterator[str]:
            tools: list = []
            try:
                for event in _run_agent_stream(conn, session_id, user_id, message):
                    ev = _usd_event(event)
                    if isinstance(ev, dict):
                        if ev.get("type") == "tool" and ev.get("name"):
                            tools.append(ev["name"])
                        elif ev.get("type") == "done":
                            # Persist constraints + turn metrics *before* the
                            # client sees "done" so the transparency panel can
                            # render them.
                            meta = _store_session_meta(
                                conn, session_id, message, ev, tools)
                            if meta.get("constraints"):
                                ev["constraints"] = meta["constraints"]
                    yield _sse(ev)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no",
                     "Connection": "keep-alive"},
        )

    # -------------------------------------------------------------- voice
    @app.post("/api/voice")
    async def voice(request: Request):
        body = await _body(request)
        session_id = body.get("session_id") or uuid.uuid4().hex
        transcript = (body.get("transcript") or "").strip() or None
        hint = None
        audio = body.get("audio_base64")
        if not transcript and audio:
            try:
                audio_bytes = base64.b64decode(audio)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse(status_code=400,
                                    content={"transcript": None, "hint": f"bad audio: {exc}"})
            transcript, hint = _transcribe(audio_bytes)
        if not transcript and not hint:
            hint = "no speech recognised; pass client-side 'transcript' instead"
        return {"session_id": session_id, "transcript": transcript, "hint": hint}

    # -------------------------------------------------------------- image
    @app.post("/api/image")
    async def image(request: Request):
        body = await _body(request)
        session_id = body.get("session_id") or uuid.uuid4().hex
        image_base64 = body.get("image_base64")
        if not image_base64:
            return JSONResponse(status_code=400,
                                content={"error": "image_base64 is required"})
        img, err = _decode_image(image_base64)
        if img is None:
            return JSONResponse(status_code=400, content={"error": err})

        conn = _connect()
        _ensure_session(conn, session_id, None)
        hints: list = []
        similar: list = []
        ocr_text = _ocr(img)
        if ocr_text:
            hints.append("ocr")
        else:
            hints.append("ocr_unavailable")

        vec, vec_err = _clip_encode(img)
        if vec is not None:
            emb = _try_import("app.embeddings")
            try:
                hits = emb.search_vectors(vec, k=8)
                for pid, score in hits:
                    summary = _product_summary(conn, pid)
                    if summary:
                        similar.append({"product": summary, "score": round(float(score), 4)})
            except Exception as exc:  # noqa: BLE001
                hints.append(f"vector_search_failed:{exc}")
                log.debug("vector search failed: %s", exc)
        else:
            hints.append(f"clip_unavailable:{vec_err}")

        conn.close()
        return {"session_id": session_id, "ocr_text": ocr_text,
                "similar_products": similar, "hints": hints}

    # ------------------------------------------------------------ session
    @app.get("/api/session/{session_id}")
    async def session(session_id: str):
        conn = _connect()
        try:
            _ensure_session(conn, session_id, None)
            state = _load_state(conn, session_id)
            cart = _cart_view(conn, session_id)
            meta = _load_session_meta(conn, session_id)
        finally:
            conn.close()
        try:
            budget = float(state.get("budget") or DEFAULT_BUDGET)
        except (TypeError, ValueError):
            budget = DEFAULT_BUDGET
        merged = dict(state.get("constraints") or {})
        merged.update(meta.get("constraints") or {})
        latency = state.get("latency_ms")
        if latency is None:
            latency = meta.get("latency_ms")
        tokens_in = state.get("tokens_in") or meta.get("tokens_in") or 0
        tokens_out = state.get("tokens_out") or meta.get("tokens_out") or 0
        flags = list(state.get("flags") or []) or list(meta.get("flags") or [])
        citations = state.get("recent_citations") or []
        n_citations = len(citations) or meta.get("citations") or 0
        product_ids = state.get("product_ids") or meta.get("product_ids") or []
        return {
            "session_id": session_id,
            "constraints": merged,
            "free_text": merged.get("free_text") or state.get("free_text") or "",
            "flags": flags,
            "latency_ms": latency,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "product_ids": product_ids,
            "recent_citations": citations,
            "tools": meta.get("tools") or [],
            "citations_count": n_citations,
            "last_hits": state.get("last_hits") or [],
            "cart": cart,
            "budget": budget,          # USD session cap ($2,000)
            "currency": "USD",
        }

    # --------------------------------------------------------------- cart
    @app.post("/api/cart")
    async def cart(request: Request):
        body = await _body(request)
        session_id = body.get("session_id")
        product_id = body.get("product_id")
        qty = int(body.get("qty", 1) or 1)
        if not session_id or not product_id:
            return JSONResponse(status_code=400,
                                content={"error": "session_id and product_id required"})
        conn = _connect()
        try:
            _ensure_session(conn, session_id, None)
            product = _product_summary(conn, product_id)
            if product is None:
                return JSONResponse(status_code=404,
                                    content={"error": "product not found"})
            cart = _open_cart(conn, session_id, None)
            unit_price = product.get("price") or 0.0
            existing = conn.execute(
                "SELECT * FROM cart_items WHERE cart_id=? AND product_id=?",
                (cart["cart_id"], product_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE cart_items SET qty=qty+?, unit_price=? WHERE cart_id=? AND product_id=?",
                    (qty, unit_price, cart["cart_id"], product_id),
                )
            else:
                conn.execute(
                    "INSERT INTO cart_items(cart_id,product_id,qty,unit_price,added_ts)"
                    " VALUES(?,?,?,?,?)",
                    (cart["cart_id"], product_id, qty, unit_price, time.time()),
                )
            conn.execute(
                "UPDATE carts SET updated_ts=? WHERE cart_id=?",
                (time.time(), cart["cart_id"]),
            )
            conn.commit()
            view = _cart_view(conn, session_id)
            return {"ok": True, **view}
        finally:
            conn.close()

    # ----------------------------------------------------------- checkout
    @app.post("/api/checkout")
    async def checkout(request: Request):
        body = await _body(request)
        session_id = body.get("session_id")
        ship_to = body.get("ship_to") or "Home, US (mock)"
        if not session_id:
            return JSONResponse(status_code=400, content={"error": "session_id required"})
        conn = _connect()
        try:
            _ensure_session(conn, session_id, None)
            state = _load_state(conn, session_id)
            try:
                budget = float(state.get("budget") or DEFAULT_BUDGET)
            except (TypeError, ValueError):
                budget = DEFAULT_BUDGET
            view = _cart_view(conn, session_id)
            if not view["items"]:
                return JSONResponse(status_code=400, content={"error": "cart is empty"})
            if view["total"] > budget:
                return JSONResponse(status_code=400, content={
                    "error": (f"Cart total ${view['total']:,.2f} exceeds the session "
                              f"budget of ${budget:,.2f} — remove items or raise the cap"),
                    "total": view["total"], "budget": budget, "status": "refused"})
            order_id = "AR-" + uuid.uuid4().hex[:12].upper()
            eta = _eta_days(ship_to)
            cart = _open_cart(conn, session_id, None)
            conn.execute(
                "INSERT INTO orders(order_id,session_id,user_id,total,status,ship_to,"
                "eta_days,created_ts) VALUES(?,?,?,?,?,?,?,?)",
                (order_id, session_id, None, view["total"], "confirmed", ship_to,
                 eta, time.time()),
            )
            for it in view["items"]:
                conn.execute(
                    "INSERT INTO order_items(order_id,product_id,qty,unit_price)"
                    " VALUES(?,?,?,?)",
                    (order_id, it["product_id"], it["qty"], it["unit_price"]),
                )
            conn.execute(
                "UPDATE carts SET status='checked_out', updated_ts=? WHERE cart_id=?",
                (time.time(), cart["cart_id"]),
            )
            conn.commit()
            return {"order_id": order_id, "total": view["total"], "eta_days": eta,
                    "status": "confirmed", "ship_to": ship_to,
                    "budget": budget, "currency": "USD",
                    "note": "mock order — no payment taken",
                    "items": view["items"]}
        finally:
            conn.close()

    # ------------------------------------------------------------ product
    def _product_detail_response(conn, product_id: str) -> dict:
        product = _product_summary(conn, product_id)
        if product is None:
            return {}
        return {
            "product": product,
            "variants": _variants(conn, product_id),
            "reviews": _reviews(conn, product_id, limit=5),
            "citations": _product_citations(product_id, product),
        }

    @app.get("/api/product/{product_id}")
    async def product(product_id: str):
        conn = _connect()
        try:
            detail = _product_detail_response(conn, product_id)
        finally:
            conn.close()
        if not detail:
            return JSONResponse(status_code=404, content={"error": "product not found"})
        return detail

    # alias to match the CONTRACT's ``/api/products/{product_id}`` spelling too
    @app.get("/api/products/{product_id}")
    async def product_alias(product_id: str):
        return await product(product_id)

    # --------------------------------------------------------------- UI
    if UI_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

        @app.get("/")
        async def index():
            from fastapi.responses import FileResponse

            return FileResponse(str(UI_DIR / "index.html"))

    return app


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    import importlib

    print("app.main --selftest")
    # 1) imports
    for mod in ("app.db", "fastapi"):
        try:
            importlib.import_module(mod)
            print(f"  import {mod}: OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  import {mod}: FAIL ({exc})")
            return 1

    # 2) app factory
    try:
        app = create_app()
        routes = sorted({r.path for r in app.routes if hasattr(r, "path")})
        print("  create_app: OK")
        for r in routes:
            if r.startswith("/api"):
                print(f"    route {r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  create_app: FAIL ({exc})")
        return 1

    # 3) health logic
    try:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        conn.close()
        print(f"  health logic: OK (products={n})")
    except Exception as exc:  # noqa: BLE001
        print(f"  health logic: FAIL ({exc})")
        return 1

    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
