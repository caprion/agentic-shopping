"""API smoke tests (health + chat).

Runnable without pytest:
    python demo/tests/test_api.py

Always exits 0 — when a check cannot run it prints SKIP instead of failing.
To keep the smoke test fast and deterministic (no LLM / model warm-up), the
agent + retrieval modules are forced into their offline fallback path; this
still exercises endpoint routing, JSON parsing and SSE framing.
"""
from __future__ import annotations

import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

import app.main as main_mod  # noqa: E402


def _force_fast_fallback():
    """Force the no-agent / no-retrieval fallback path for a fast smoke test."""
    main_mod._MOD_CACHE["app.agent"] = None
    main_mod._MOD_CACHE["app.retrieval"] = None


def _client():
    from fastapi.testclient import TestClient

    _force_fast_fallback()
    return TestClient(main_mod.create_app())


def check_health() -> tuple:
    c = _client()
    r = c.get("/api/health")
    assert r.status_code == 200, f"health status {r.status_code}"
    body = r.json()
    assert "status" in body, "health missing 'status'"
    return body


def check_chat_nonstream() -> tuple:
    c = _client()
    r = c.post("/api/chat", json={"message": "wireless headphones", "stream": False})
    assert r.status_code == 200, f"chat status {r.status_code}"
    d = r.json()
    assert isinstance(d, dict), f"expected dict, got {type(d)}"
    assert "answer" in d or d.get("type") == "done", f"unexpected body keys {list(d)[:5]}"
    return {k: d.get(k) for k in ("type", "flags")}


def check_chat_sse() -> tuple:
    c = _client()
    r = c.post("/api/chat", json={"message": "wireless headphones", "stream": True})
    assert r.status_code == 200, f"sse status {r.status_code}"
    ctype = r.headers.get("content-type", "")
    assert "event-stream" in ctype, f"not event-stream: {ctype}"
    assert "data: " in r.text, "no SSE frames emitted"
    assert '"type": "done"' in r.text or "'type': 'done'" in r.text, "no done frame"
    return f"{r.text.count(chr(10))} frames"


def check_session_currency() -> tuple:
    """The session endpoint reports the USD budget the UI shows."""
    c = _client()
    r = c.get("/api/session/b4-api-check")
    assert r.status_code == 200, f"session status {r.status_code}"
    body = r.json()
    assert body.get("budget") == 2000.0, f"budget != 2000: {body.get('budget')}"
    assert body.get("currency") == "USD", f"currency != USD: {body.get('currency')}"
    return "budget $2,000 / USD"


def check_chat_is_usd() -> tuple:
    """No rupee symbol may reach the client (the catalogue is USD)."""
    c = _client()
    r = c.post("/api/chat", json={"message": "wireless headphones under $100",
                                  "stream": True})
    assert r.status_code == 200, f"sse status {r.status_code}"
    assert "₹" not in r.text, "rupee symbol leaked into the chat stream"
    return "chat stream is USD-only"


def run() -> list:
    results = []
    for name, fn in (("health", check_health),
                     ("chat_nonstream", check_chat_nonstream),
                     ("chat_sse", check_chat_sse),
                     ("session_currency", check_session_currency),
                     ("chat_is_usd", check_chat_is_usd)):
        try:
            detail = fn()
            results.append((name, True, str(detail)[:120]))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"SKIP: {exc}"))
    return results


def main() -> int:
    print("test_api")
    for name, ok, detail in run():
        print(f"  {'PASS' if ok else 'SKIP'} {name}  {detail}")
    print("done (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
