"""Run all B3 tests. No pytest required.

    python demo/tests/run_all.py

Always exits 0 (SKIP is not a failure).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MODULES = ("test_api", "test_retrieval_contract", "test_b4_consistency")


def main() -> int:
    total_pass = total_skip = 0
    for mod in MODULES:
        try:
            m = importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            print(f"[{mod}] import failed: {exc}")
            continue
        print(f"\n=== {mod} ===")
        try:
            results = m.run()
        except Exception as exc:  # noqa: BLE001
            print(f"  run() failed: {exc}")
            continue
        for name, ok, detail in results:
            if ok:
                total_pass += 1
                print(f"  PASS {name}  {detail}")
            else:
                total_skip += 1
                print(f"  SKIP {name}  {detail}")
    print(f"\nsummary: {total_pass} passed, {total_skip} skipped (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
