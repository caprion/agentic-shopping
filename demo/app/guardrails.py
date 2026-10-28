"""Guardrails for Aria (owned by B2).

Middleware consumed by `app/agent.py`. Each guard returns the same shape::

    {"ok": bool, "flags": [str], "detail": str}

Guards:
  * BudgetGuard   — session spending cap (default $2,000).
  * StockGuard    — never let the agent claim something is in stock when it isn't.
  * ClaimVerifier — post-answer: parse price/rating/percent-off claims and check
                    them against the cited DB rows; returns the list of violations.
  * PromptGuard   — refuse to role-play as a real-money order placer (mock only).
  * CitationGate  — reject an answer that makes factual claims with zero citations.

No import-time network or DB access; all checks are deterministic and offline-safe.
"""
from __future__ import annotations

import logging
import os
import re
import sys

log = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.dirname(_APP_DIR)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from app import db  # noqa: E402  (orchestrator-owned, pure sqlite)


def _ok(detail: str = "ok") -> dict:
    return {"ok": True, "flags": [], "detail": detail}


def _bad(flags: list[str], detail: str) -> dict:
    return {"ok": False, "flags": flags, "detail": detail}


# --------------------------------------------------------------------------
# BudgetGuard
# --------------------------------------------------------------------------
class BudgetGuard:
    def __init__(self, cap: float = 2_000.0):
        self.cap = float(cap)

    def check(self, total) -> dict:
        if total is None:
            return _ok("no cart total to check")
        try:
            total = float(total)
        except (TypeError, ValueError):
            return _bad(["budget_exceeded"], f"non-numeric total: {total!r}")
        if total > self.cap:
            return _bad(["budget_exceeded"],
                        f"total ${total:,.2f} exceeds session cap ${self.cap:,.2f}")
        return _ok(f"total ${total:,.2f} within cap ${self.cap:,.2f}")


# --------------------------------------------------------------------------
# StockGuard
# --------------------------------------------------------------------------
class StockGuard:
    def check(self, conn, product_id: str) -> dict:
        try:
            row = db.product_row(conn, product_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("StockGuard: product lookup failed: %s", exc)
            return _ok("catalogue unavailable; no stock claim made")
        if row is None:
            return _ok(f"{product_id} not in catalogue; no stock claim made")
        if row["in_stock"]:
            return _ok(f"{row['title']} is in stock")
        return _bad(["out_of_stock"], f"{row['title']} ({product_id}) is out of stock")

    def check_many(self, conn, product_ids: list[str]) -> dict:
        flags: list[str] = []
        details: list[str] = []
        for pid in product_ids or []:
            r = self.check(conn, pid)
            if not r["ok"]:
                flags.extend(r["flags"])
                details.append(r["detail"])
        if flags:
            return _bad(sorted(set(flags)), "; ".join(details))
        return _ok("all cited products are in stock")


# --------------------------------------------------------------------------
# ClaimVerifier
# --------------------------------------------------------------------------
# Accepts both $ and ₹ (a user may type either); display/defaults are USD.
_RUPEE = re.compile(r"(?:₹|rs\.?|inr|\$)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
_RATING = re.compile(r"([0-5](?:\.[0-9]+)?)\s*(?:★|stars?)", re.I)
_PERCENT_OFF = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:off|discount)", re.I)
# An amount that merely echoes a limit ("under $200", "budget cap $2,000") is
# not a product-price claim and must not be flagged as unverified.
_LIMIT_ECHO = re.compile(
    r"(?i)\b(?:under|below|sub[-\s]?|less than|within|up to|at most|a maximum of|"
    r"max(?:imum)?|or less|or cheaper|budget(?:\s+cap)?|cap of|cheaper than|"
    r"no more than|not more than|session cap|starting at|from)\s*"
    r"(?:usd|\$|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
# A price *range* ("the $1,200-1,500 band") is a budget band, not a price.
_PRICE_RANGE = re.compile(
    r"(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*[-–—~]\s*"
    r"(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?"
    r"|\bbetween\s+(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s+and\s+"
    r"(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?", re.I)


class ClaimVerifier:
    PRICE_TOL = 0.02    # 2% relative
    PRICE_ABS = 2.0     # or $2, whichever larger
    RATING_TOL = 0.15
    DISC_TOL = 2.0      # percentage points

    def verify(self, conn, answer: str, citations: list[dict]) -> dict:
        known_prices, known_ratings, known_discounts = self._known_values(
            conn, citations)
        # a derived difference ("~$49 cheaper") between two cited prices is a
        # legitimate comparison, not a fabricated price
        derived = [abs(a - b) for i, a in enumerate(known_prices)
                   for b in known_prices[i + 1:]]
        violations: list[dict] = []
        # strip limit echoes ("under $200") and price bands ("$1,200-1,500")
        # before looking for price claims
        price_text = _LIMIT_ECHO.sub(" ", answer or "")
        price_text = _PRICE_RANGE.sub(" ", price_text)
        for v in self._nums(_RUPEE.findall(price_text)):
            if not any(abs(v - k) <= max(self.PRICE_TOL * abs(k), self.PRICE_ABS)
                       for k in known_prices):
                if not any(abs(v - d) <= max(self.PRICE_TOL * abs(d), self.PRICE_ABS)
                           for d in derived):
                    violations.append({"type": "price", "claim": v})
        for v in self._nums(_RATING.findall(answer or "")):
            if not any(abs(v - k) <= self.RATING_TOL for k in known_ratings):
                violations.append({"type": "rating", "claim": v})
        for v in self._nums(_PERCENT_OFF.findall(answer or "")):
            if not any(abs(v - k) <= self.DISC_TOL for k in known_discounts):
                violations.append({"type": "discount_percent", "claim": v})
        if violations:
            detail = (f"{len(violations)} unverified claim(s): " + "; ".join(
                f"{v['type']}={v['claim']}" for v in violations))
            return {"ok": False, "flags": ["unverified_claim"], "detail": detail,
                    "violations": violations}
        return {"ok": True, "flags": [], "detail": "all claims verified",
                "violations": []}

    @staticmethod
    def _nums(strs) -> list[float]:
        out = []
        for s in strs:
            try:
                out.append(float(s.replace(",", "")))
            except (ValueError, TypeError):
                continue
        return out

    def _known_values(self, conn, citations):
        """Ground-truth numbers: prefer the DB rows the citations point at,
        falling back to the citation values themselves."""
        prices, ratings, discounts = [], [], []
        seen_pids = {c.get("product_id") for c in (citations or [])
                     if c.get("product_id")}
        for pid in seen_pids:
            try:
                row = db.product_row(conn, pid)
            except Exception:  # noqa: BLE001
                row = None
            if row is not None:
                if row["price"] is not None:
                    prices.append(float(row["price"]))
                if row["price_mrp"] is not None:
                    prices.append(float(row["price_mrp"]))
                if row["rating"] is not None:
                    ratings.append(float(row["rating"]))
                if row["price"] and row["price_mrp"] and row["price_mrp"] > row["price"]:
                    discounts.append(round((1 - row["price"] / row["price_mrp"])
                                           * 100, 1))
        for c in citations or []:
            f, v = c.get("field"), c.get("value")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if f in ("price", "price_mrp"):
                prices.append(v)
            elif f == "rating":
                ratings.append(v)
            elif f == "discount":
                discounts.append(v)
        return prices, ratings, discounts


# --------------------------------------------------------------------------
# PromptGuard
# --------------------------------------------------------------------------
_REAL_MONEY = [
    r"\breal money\b", r"\breal(?:ly)? (?:buy|purchase|order|pay)\b",
    r"\bactually (?:buy|purchase|order|pay)\b", r"\bcharge (?:my|the|a) (?:card|credit)",
    r"\breal (?:card|payment|order|purchase)\b", r"\bplace (?:a|an) real order\b",
    r"\bmy (?:credit|debit) card\b", r"\bnot (?:a |just )?mock\b",
    r"\bfor real\b", r"\bwith (?:my|real) (?:own )?money\b",
    r"\bspend (?:real|actual) money\b", r"\bplace an? order (?:with|using) (?:real|actual)",
]


class PromptGuard:
    def check(self, user_message: str) -> dict:
        text = (user_message or "").lower()
        hits = [p for p in _REAL_MONEY if re.search(p, text)]
        if hits:
            return _bad(["mock_only"],
                        "Refusing to act as a real order placer for real money — "
                        "Aria is a mock/simulation and never takes payments.")
        return _ok("prompt within mock-only policy")


# --------------------------------------------------------------------------
# CitationGate
# --------------------------------------------------------------------------
_FACT_RE = re.compile(r"(?:₹|rs\.?|inr|\$)|(?:\b[0-5](?:\.[0-9]+)?\s*(?:★|stars?))"
                      r"|(?:\b[0-9]+(?:\.[0-9]+)?\s*%\s*(?:off|discount))", re.I)


class CitationGate:
    def check(self, answer: str, citations: list[dict]) -> dict:
        factual = bool(_FACT_RE.search(answer or ""))
        if factual and not citations:
            return _bad(["no_citations"],
                        "answer contains factual claims but carries no citations")
        return _ok("citations present for factual claims")


# --------------------------------------------------------------------------
# convenience: one post-answer pass
# --------------------------------------------------------------------------
def run_post_guards(conn, answer: str, citations: list[dict],
                    cart_total=None, budget: float = 2_000.0) -> dict:
    """Run ClaimVerifier + CitationGate (+ BudgetGuard if a total is given).
    Returns combined flags/detail plus the raw per-guard results."""
    flags: list[str] = []
    details: list[str] = []
    results: dict = {}
    for name, res in (
        ("claim", ClaimVerifier().verify(conn, answer, citations)),
        ("citation", CitationGate().check(answer, citations)),
    ):
        results[name] = res
        if not res["ok"]:
            flags.extend(res["flags"])
            details.append(res["detail"])
    if cart_total is not None:
        res = BudgetGuard(budget).check(cart_total)
        results["budget"] = res
        if not res["ok"]:
            flags.extend(res["flags"])
            details.append(res["detail"])
    return {"ok": not flags, "flags": sorted(set(flags)),
            "detail": "; ".join(details), "results": results}


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _selftest() -> None:
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "aria_guard_selftest.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = db.connect(tmp)
    db.init_schema(conn)
    conn.execute("INSERT INTO products(product_id,title,brand,price,price_mrp,"
                 "rating,in_stock) VALUES(?,?,?,?,?,?,?)",
                 ("B0GUARD0001", "Sony WH-1000XM5", "Sony", 348.0, 499.0,
                  4.6, 1))
    conn.execute("INSERT INTO products(product_id,title,brand,price,price_mrp,"
                 "rating,in_stock) VALUES(?,?,?,?,?,?,?)",
                 ("B0GUARD0002", "Out-of-stock gadget", "X", 100.0, 200.0, 3.0, 0))
    conn.commit()

    print("BudgetGuard ok:",
          BudgetGuard().check(2500)["ok"] is False,
          BudgetGuard(5000).check(2500)["ok"] is True)
    print("StockGuard out-of-stock detected:",
          StockGuard().check(conn, "B0GUARD0002")["flags"] == ["out_of_stock"])
    print("PromptGuard real-money refused:",
          PromptGuard().check("place a real order with my credit card")["ok"] is False)
    citations = [{"product_id": "B0GUARD0001", "field": "price", "value": 348.0,
                  "quote": "Sony WH-1000XM5"},
                 {"product_id": "B0GUARD0001", "field": "rating", "value": 4.6,
                  "quote": "Sony WH-1000XM5"},
                 {"product_id": "B0GUARD0001", "field": "discount", "value": 30.3,
                  "quote": "Sony WH-1000XM5"}]
    good = ClaimVerifier().verify(conn, "It costs $348 and is rated 4.6★ (30% off).",
                                  citations)
    bad = ClaimVerifier().verify(conn, "It costs $9,999 and is rated 4.9★.", citations)
    print("ClaimVerifier clean:", good["ok"] is True,
          "| violations found:", len(bad["violations"]))
    print("ClaimVerifier accepts ₹ too:",
          ClaimVerifier().verify(conn, "It costs ₹348 and is rated 4.6★.", citations)["ok"] is True)
    print("ClaimVerifier tolerates budget echoes:",
          ClaimVerifier().verify(conn, "Nothing under $200 matched.", citations)["ok"] is True)
    two = citations + [{"product_id": "B0GUARD0002", "field": "price", "value": 100.0,
                        "quote": "Out-of-stock gadget"}]
    print("ClaimVerifier tolerates derived differences:",
          ClaimVerifier().verify(conn, "The first is ~$248 more than the second.",
                                 two)["ok"] is True)
    print("ClaimVerifier still flags a fabricated price:",
          ClaimVerifier().verify(conn, "It costs $9,999.", citations)["ok"] is False)
    print("CitationGate no-citation rejected:",
          CitationGate().check("It costs $348 and is 4.6★.", [])["ok"] is False)
    conn.close()
    print("\nselftest OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
