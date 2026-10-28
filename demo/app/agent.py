"""ReAct agent loop for Aria (owned by B2).

`run_turn` is the entry point B3's FastAPI layer calls. It runs a bounded
ReAct loop (max 8 tool calls, 60s wall clock) over the LLM + tools, streams
events, persists session state into `sessions.state` and writes a row per step
into `traces`, then runs the guardrail verification pass before `done`.

Event schema (exactly what B3 must consume):

    {"type":"token",    "text": str}
    {"type":"tool",     "name": str, "args": dict, "tool_call_id": str}
    {"type":"citation", "citation": {"product_id","field"|"review_id","value","quote","marker"}}
    {"type":"done",     "answer","citations","product_ids","flags",
                        "latency_ms","tokens_in","tokens_out"}

The generator is resumable: state is persisted in a ``finally`` block, so a
consumer that stops early still flushes session state and commits (no leaks).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid

log = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.dirname(_APP_DIR)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from app import db  # noqa: E402
from app import llm as llm_mod  # noqa: E402
from app import tools as tools_mod  # noqa: E402
from app import guardrails as gr  # noqa: E402

LLMClient = llm_mod.LLMClient
MockLLM = llm_mod.MockLLM
TOOLS = tools_mod.TOOLS
run_tool = tools_mod.run_tool
DEFAULT_BUDGET = tools_mod.DEFAULT_BUDGET

MAX_TOOL_CALLS = 8
WALL_CLOCK_S = 60.0
MEMORY_TURNS = 6

SYSTEM_PROMPT = (
    "You are Aria, a careful shopping assistant for an e-commerce marketplace "
    "prototype. Rules you MUST follow:\n"
    "1. Answer ONLY from tool results — never invent a price, rating, discount, "
    "stock status, or review.\n"
    "2. Cite every factual claim inline as [p1], [p2], ... for products and "
    "[r12] for reviews. These markers map to the citations returned with your "
    "answer.\n"
    "3. Be concise. Always state trade-offs (e.g. price vs rating vs feature).\n"
    "4. If an item is NOT in the catalogue, use web_search_external and clearly "
    "label the result as an external result with its URL — never pretend it is "
    "in the catalogue.\n"
    "5. checkout_cart is a MOCK (no real payment) and may return "
    "status=refused if the total exceeds the $2,000 session budget — relay "
    "that clearly.\n"
    "6. get_price_history returns SIMULATED data — label it as simulated, never "
    "as real price history.\n"
    "7. If a tool returns ok:false, work with what you have and say what you "
    "could not do."
)


def _short(s, n=200) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


# --------------------------------------------------------------------------
# session state
# --------------------------------------------------------------------------
def _load_session(conn, session_id: str, user_id: str | None) -> dict:
    state: dict = {}
    try:
        row = conn.execute("SELECT state, created_ts FROM sessions WHERE session_id=?",
                           (session_id,)).fetchone()
        if row is not None:
            state = db.jload(row["state"], {}) or {}
            state["_created_ts"] = row["created_ts"]
    except Exception as exc:  # noqa: BLE001
        log.debug("session load failed (table missing?): %s", exc)
    state.setdefault("session_id", session_id)
    state.setdefault("user_id", user_id)
    state.setdefault("budget", DEFAULT_BUDGET)
    state.setdefault("constraints", {})
    state.setdefault("cart_id", None)
    state.setdefault("last_product_ids", [])
    state.setdefault("memory", [])
    state.setdefault("turns", 0)
    return state


def _persist_session(conn, session: dict) -> None:
    try:
        created = session.get("_created_ts")
        payload = {k: v for k, v in session.items() if not k.startswith("_")}
        now = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO sessions(session_id,user_id,state,created_ts,"
            "updated_ts) VALUES(?,?,?,?,?)",
            (session["session_id"], session.get("user_id"),
             db.jdump(payload), created or now, now))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("session persist failed: %s", exc)


def _write_trace(conn, session_id, turn_id, step, **kw) -> None:
    try:
        conn.execute(
            "INSERT INTO traces(session_id,turn_id,ts,step,tool,tool_args,result,"
            "tokens_in,tokens_out,latency_ms,model,citations,flags) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, turn_id, time.time(), step, kw.get("tool"),
             kw.get("tool_args"), kw.get("result"), kw.get("tokens_in"),
             kw.get("tokens_out"), kw.get("latency_ms"), kw.get("model"),
             kw.get("citations"), kw.get("flags")))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("trace write failed: %s", exc)


# --------------------------------------------------------------------------
# citations / markers
# --------------------------------------------------------------------------
def _mark_citation(c: dict, product_markers: dict, review_markers: dict) -> dict:
    c = dict(c)
    if "review_id" in c:
        c["marker"] = f"r{c['review_id']}"
        review_markers[c["review_id"]] = c["marker"]
    else:
        pid = c.get("product_id")
        if pid and pid not in product_markers:
            product_markers[pid] = f"p{len(product_markers) + 1}"
        c["marker"] = product_markers.get(pid, "p?")
    return c


def _chunk_text(text: str, n: int = 8) -> list[str]:
    words = text.split()
    if not words:
        return [text] if text else []
    return [" ".join(words[i:i + n]) for i in range(0, len(words), n)]


# --------------------------------------------------------------------------
# core loop
# --------------------------------------------------------------------------
def _events(conn, session_id: str, user_id: str | None, user_message: str):
    start = time.time()
    turn_id = uuid.uuid4().hex[:12]
    session = _load_session(conn, session_id, user_id)
    flags: list[str] = []
    all_citations: list[dict] = []
    product_markers: dict = {}
    review_markers: dict = {}
    product_ids: list[str] = []
    tokens_in = tokens_out = 0
    final_answer = ""
    last_cart_total = None
    if os.environ.get("ARIA_FORCE_MOCK") == "1":
        llm = MockLLM()
    else:
        llm = llm_mod.LLMClient()

    def remember_pid(pid):
        if pid and pid not in product_ids:
            product_ids.append(pid)

    try:
        prompt_res = gr.PromptGuard().check(user_message)
        if not prompt_res["ok"]:
            flags.extend(prompt_res["flags"])

        # build messages (system + short memory + current user turn)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in (session.get("memory") or [])[-MEMORY_TURNS:]:
            messages.append({"role": "user", "content": m.get("user", "")})
            messages.append({"role": "assistant",
                             "content": _short(m.get("assistant", ""))})
        messages.append({"role": "user", "content": user_message})

        tool_calls_made = 0
        while (time.time() - start) < WALL_CLOCK_S and tool_calls_made < MAX_TOOL_CALLS:
            res = llm.complete(messages, tools=TOOLS, temperature=0.2,
                               max_tokens=1200)
            tokens_in += int(res.get("tokens_in") or 0)
            tokens_out += int(res.get("tokens_out") or 0)
            flags.extend(res.get("flags") or [])
            _write_trace(conn, session_id, turn_id, "llm",
                         model=res.get("model"), tokens_in=res.get("tokens_in"),
                         tokens_out=res.get("tokens_out"),
                         result=_short(res.get("text")))
            tool_calls = res.get("tool_calls") or []

            if tool_calls:
                executed = 0
                for tc in tool_calls:
                    if executed + tool_calls_made >= MAX_TOOL_CALLS or \
                            (time.time() - start) >= WALL_CLOCK_S:
                        break
                    name = tc.get("name")
                    args = tc.get("arguments") or {}
                    tc_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                    yield {"type": "tool", "name": name, "args": args,
                           "tool_call_id": tc_id}
                    obs = run_tool(conn, name, args, session)
                    executed += 1
                    tool_calls_made += 1
                    _write_trace(conn, session_id, turn_id, "tool", tool=name,
                                 tool_args=db.jdump(args),
                                 result=_short(db.jdump(obs), 500),
                                 citations=db.jdump(obs.get("citations") or []))
                    for c in obs.get("citations") or []:
                        mc = _mark_citation(c, product_markers, review_markers)
                        all_citations.append(mc)
                        remember_pid(c.get("product_id"))
                        yield {"type": "citation", "citation": mc}
                    # keep the running cart total for the budget guard
                    if isinstance(obs.get("result"), dict) and \
                            "total" in obs["result"]:
                        last_cart_total = obs["result"]["total"]
                    # feed the observation back to the LLM
                    messages.append({"role": "assistant", "content": None,
                                     "tool_calls": [{"id": tc_id, "name": name,
                                                     "arguments": args}]})
                    messages.append({"role": "tool", "tool_call_id": tc_id,
                                     "name": name,
                                     "content": db.jdump(obs)})
                if executed:
                    continue
                break  # no tool could be executed (limits hit)
            else:
                final_answer = res.get("text") or ""
                break

        # guarantee a final answer even if the loop was cut short or the LLM
        # returned no text
        if not final_answer:
            final_answer = MockLLM().complete(messages, TOOLS).get("text") \
                or "Sorry, I could not complete that request."

        # ---- guardrail verification pass (before done) --------------------
        gr_flags: list[str] = []
        notes: list[str] = []
        if prompt_res and not prompt_res["ok"]:
            notes.append(prompt_res["detail"])
        stock_res = gr.StockGuard().check_many(conn, product_ids)
        if not stock_res["ok"]:
            gr_flags.extend(stock_res["flags"])
            notes.append(stock_res["detail"])
        post = gr.run_post_guards(conn, final_answer, all_citations,
                                  cart_total=last_cart_total,
                                  budget=float(session.get("budget", DEFAULT_BUDGET)))
        gr_flags.extend(post["flags"])
        if not post["ok"]:
            notes.append(post["detail"])
        _write_trace(conn, session_id, turn_id, "guardrail",
                     result=db.jdump(post.get("results")), flags=db.jdump(gr_flags))
        flags.extend(gr_flags)
        if notes:
            final_answer += "\n\n[Note: " + " ".join(notes) + "]"

        # ---- token stream + done -----------------------------------------
        for chunk in _chunk_text(final_answer):
            yield {"type": "token", "text": chunk}

        latency_ms = int((time.time() - start) * 1000)

        # persist session state + memory
        memory = (session.get("memory") or [])[-MEMORY_TURNS:]
        memory.append({"user": user_message, "assistant": final_answer})
        session["memory"] = memory[-MEMORY_TURNS:]
        session["turns"] = int(session.get("turns", 0)) + 1
        session["last_product_ids"] = product_ids
        if last_cart_total is not None:
            session["last_cart_total"] = last_cart_total
        _persist_session(conn, session)

        flags = sorted(set(flags))
        done = {
            "type": "done",
            "answer": final_answer,
            "citations": all_citations,
            "product_ids": product_ids,
            "flags": flags,
            "latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        _write_trace(conn, session_id, turn_id, "done",
                     result=_short(final_answer),
                     citations=db.jdump(all_citations),
                     flags=db.jdump(flags), latency_ms=latency_ms,
                     tokens_in=tokens_in, tokens_out=tokens_out)
        yield done
    finally:
        # flush session state even if the consumer stopped early
        try:
            _persist_session(conn, session)
        except Exception as exc:  # noqa: BLE001
            log.debug("final session flush failed: %s", exc)


def run_turn(conn, session_id: str, user_id: str | None, user_message: str,
             stream: bool = False) -> dict:
    gen = _events(conn, session_id, user_id, user_message)
    if stream:
        return {"type": "stream", "events": gen}
    events = list(gen)
    done = None
    for e in reversed(events):
        if e.get("type") == "done":
            done = dict(e)
            break
    if done is None:
        done = {"type": "done", "answer": "", "citations": [],
                "product_ids": [], "flags": ["no_answer"],
                "latency_ms": 0, "tokens_in": 0, "tokens_out": 0}
    done["events"] = events
    return done


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _selftest() -> None:
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "aria_agent_selftest.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = db.connect(tmp)
    db.init_schema(conn)
    conn.execute("INSERT INTO products(product_id,title,brand,category_main,"
                 "price,price_mrp,rating,rating_count,in_stock,popularity,"
                 "features) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 ("B0AGENT0001", "Sony WH-1000XM5 Noise Cancelling Headphones",
                  "Sony", "Headphones", 4999.0, 7999.0, 4.6, 1200, 1, 5.0,
                  json.dumps(["Active noise cancelling", "30h battery"])))
    conn.execute("INSERT INTO reviews(product_id,user_id,rating,title,text,"
                 "helpful) VALUES(?,?,?,?,?,?)",
                 ("B0AGENT0001", "u1", 5, "Great ANC",
                  "Superb noise cancelling and comfort for the price.", 12))
    conn.commit()

    # force offline MockLLM + SQL fallback for the selftest (no network, no
    # heavy model downloads — tests B2's own fallback paths end-to-end)
    os.environ["ARIA_FORCE_MOCK"] = "1"
    os.environ["ARIA_NO_RETRIEVAL"] = "1"
    res = run_turn(conn, "s_selftest", None,
                   "noise cancelling headphones under 5000 with 4.5 stars")

    print("=== answer ===")
    print(res["answer"])
    print("\n=== citations ===")
    for c in res["citations"]:
        print(f"  [{c.get('marker')}] {c.get('product_id')} "
              f"{c.get('field') or c.get('review_id')} = {c.get('value')}")
    print("\n=== flags ===", res["flags"])
    print("=== latency_ms ===", res["latency_ms"])
    print("=== tokens in/out ===", res["tokens_in"], "/", res["tokens_out"])
    print("=== product_ids ===", res["product_ids"])
    assert "4999" in res["answer"], "grounded price missing"
    conn.close()
    print("\nselftest OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
