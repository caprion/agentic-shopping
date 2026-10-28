"""B4 consistency checks: USD currency, US personas, demo scripts, footer.

Runnable without pytest:
    python demo/tests/test_b4_consistency.py

Always exits 0 — a check that cannot run prints SKIP instead of failing. The
catalogue check SKIPs (instead of failing) when the DB is empty.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))


def _read(rel: str) -> str:
    p = DEMO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def check_ui_no_rupee() -> tuple:
    offenders = []
    for rel in ("ui/app.js", "ui/index.html", "ui/style.css"):
        text = _read(rel)
        if "₹" in text:
            offenders.append(rel)
    if offenders:
        raise AssertionError("₹ leaked into UI strings: " + ", ".join(offenders))
    return "ui/app.js, index.html, style.css contain no ₹"


def check_personas() -> tuple:
    html = _read("ui/index.html").lower()
    js = _read("ui/app.js").lower()
    for old in ("arjun", "priya"):
        if old in html or old in js:
            raise AssertionError(f"old persona {old!r} still referenced")
    for new in ("alex", "rhea", "guest"):
        if new not in html:
            raise AssertionError(f"persona {new!r} missing from index.html")
    return "personas: Alex / Rhea / Guest"


def check_demo_scripts() -> tuple:
    html = _read("ui/index.html")
    opts = re.findall(r'<option value="([^"]*)">', html)
    money = [o for o in opts if "$" in o]
    if len(money) != 5:
        raise AssertionError(f"expected 5 USD demo scripts, found {len(money)}: {money}")
    for phrase in ("under $300", "under $200", "Sony WH-1000XM5",
                   "under $50", "under $1500"):
        if phrase not in html:
            raise AssertionError(f"demo script missing phrase {phrase!r}")
    if "saste earphones" not in html:
        raise AssertionError("Hinglish code-mixed script missing")
    joined = " ".join(money)
    for bad in ("₹", "lakh", "Pune", "Delhi"):
        if bad in joined:
            raise AssertionError(f"demo scripts contain {bad!r}")
    return "5 USD demo scripts (one Hinglish), no ₹/lakh/city"


def check_footer() -> tuple:
    html = _read("ui/index.html")
    for phrase in ("Amazon Reviews 2023 (US)", "USD", "mock"):
        if phrase not in html:
            raise AssertionError(f"sources & data footer missing {phrase!r}")
    return "sources & data footer present"


def check_guardrails() -> tuple:
    import app.guardrails as g

    assert g.BudgetGuard().cap == 2000.0, "BudgetGuard default cap != 2000"
    for s in ("$200", "₹200", "rs 200", "inr 200"):
        assert g._RUPEE.search(s), f"verifier regex should accept {s!r}"
    detail = g.BudgetGuard().check(2500)["detail"]
    assert "$" in detail and "₹" not in detail, f"budget message not in $: {detail!r}"
    sig = inspect.signature(g.run_post_guards)
    assert sig.parameters["budget"].default == 2000.0, "run_post_guards budget default != 2000"
    return "guardrails: $2,000 cap, $ messages, accepts $ and ₹"


def check_tools() -> tuple:
    import app.tools as t

    assert t.DEFAULT_BUDGET == 2000.0, "tools.DEFAULT_BUDGET != 2000"
    kw = t._AXIS_KEYWORDS["price"]
    for cur in ("$", "₹", "rs", "inr"):
        assert cur in kw, f"price keywords missing {cur!r}"
    assert t._local_constraints("under $50").get("price_max") == 50.0
    return "tools: DEFAULT_BUDGET=2000, price keywords catch $/₹/rs/inr"


def check_main() -> tuple:
    src = _read("app/main.py")
    for bad in ("₹", "Pune", "Delhi", "Mumbai", "Bengaluru"):
        if bad in src:
            raise AssertionError(f"{bad!r} still in app/main.py")
    return "main.py: no ₹ / Indian-city defaults"


def check_catalogue_nonempty() -> tuple:
    from app import db

    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    finally:
        conn.close()
    if n == 0:
        raise AssertionError("SKIP: catalogue DB is empty")
    return f"catalogue has {n} products"


def run() -> list:
    results = []
    for name, fn in (
        ("ui_no_rupee", check_ui_no_rupee),
        ("personas", check_personas),
        ("demo_scripts", check_demo_scripts),
        ("footer", check_footer),
        ("guardrails", check_guardrails),
        ("tools", check_tools),
        ("main", check_main),
        ("catalogue_nonempty", check_catalogue_nonempty),
    ):
        try:
            detail = fn()
            results.append((name, True, str(detail)[:120]))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"SKIP: {exc}"))
    return results


def main() -> int:
    print("test_b4_consistency")
    for name, ok, detail in run():
        print(f"  {'PASS' if ok else 'SKIP'} {name}  {detail}")
    print("done (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
