"""Provider-agnostic LLM client for Aria.

Owned by B2 (agent & tools). Imports cleanly with no network and no DB.

Providers (read API keys from env, never logged):
  * deepseek  -> Anthropic-compatible  https://api.deepseek.com/anthropic   (DEEPSEEK_API_KEY)
  * minimax   -> Anthropic-compatible  https://api.minimax.io/anthropic     (MINIMAX_API_KEY)
  * openai    -> chat completions      https://api.openai.com/v1            (OPENAI_API_KEY)

Messages use a neutral, OpenAI-flavoured shape throughout the agent:

    {"role":"system", "content": str}
    {"role":"user",   "content": str}
    {"role":"assistant", "content": str|None, "tool_calls":[{"id","name","arguments":dict}]}
    {"role":"tool", "tool_call_id": str, "name": str, "content": str}

`complete()` returns the contract dict and, on total provider failure after 2
retries with backoff, falls back to `MockLLM` (flags get ``llm_fallback``).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

log = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.dirname(_APP_DIR)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

DEEPSEEK_BASE = "https://api.deepseek.com/anthropic"
MINIMAX_BASE = "https://api.minimax.io/anthropic"
OPENAI_BASE = "https://api.openai.com/v1"

DEFAULT_MODELS = {
    "deepseek": os.environ.get("ARIA_DEEPSEEK_MODEL", "deepseek-chat"),
    "minimax": os.environ.get("ARIA_MINIMAX_MODEL", "MiniMax-M1"),
    "openai": os.environ.get("ARIA_OPENAI_MODEL", "gpt-4o-mini"),
}

_RETRY_BACKOFF = 0.75  # seconds, doubled each retry
_TIMEOUT = (10.0, 25.0)  # (connect, read) seconds
MAX_RETRIES = 2  # retries *after* the first attempt


def _pick_provider(provider: str | None) -> str:
    if provider:
        return provider.lower()
    # auto-detect from available keys
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("MINIMAX_API_KEY"):
        return "minimax"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "deepseek"  # will fail -> fall back to MockLLM


def _api_key(provider: str) -> str | None:
    return {
        "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
        "minimax": os.environ.get("MINIMAX_API_KEY"),
        "openai": os.environ.get("OPENAI_API_KEY"),
    }.get(provider)


# --------------------------------------------------------------------------
# Message / tool conversion
# --------------------------------------------------------------------------
def _tool_input_schema(tool: dict) -> dict:
    return tool.get("parameters") or tool.get("input_schema") or {
        "type": "object", "properties": {}}


def _to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            system_parts.append(str(content))
            continue
        if role == "user":
            out.append({"role": "user", "content": str(content)})
            continue
        if role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in m.get("tool_calls") or []:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or tc.get("name"),
                    "name": tc["name"],
                    "input": tc.get("arguments") or {},
                })
            out.append({"role": "assistant",
                        "content": blocks if blocks else (str(content or ""))})
            continue
        if role == "tool":
            out.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or m.get("name"),
                "content": str(m.get("content", "")),
            }]})
    return ("\n\n".join(system_parts) if system_parts else ""), out


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and (m.get("tool_calls")):
            out.append({
                "role": "assistant",
                "content": m.get("content"),
                "tool_calls": [{
                    "id": tc.get("id") or tc.get("name"),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("arguments") or {},
                                                ensure_ascii=False),
                    },
                } for tc in m["tool_calls"]],
            })
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id") or m.get("name"),
                "content": str(m.get("content", "")),
            })
        else:
            out.append({"role": role, "content": str(m.get("content", ""))})
    return out


def _to_anthropic_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [{
        "name": t["name"],
        "description": t.get("description", ""),
        "input_schema": _tool_input_schema(t),
    } for t in tools]


def _to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": _tool_input_schema(t),
        },
    } for t in tools]


def _count_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(str(text).split()))


# --------------------------------------------------------------------------
# LLMClient
# --------------------------------------------------------------------------
class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = _pick_provider(provider)
        self.model = model or DEFAULT_MODELS.get(self.provider, "deepseek-chat")
        self._session = None
        self._dead = False  # once the provider fails, skip future network calls
        try:
            import requests  # noqa: F401
            self._requests = requests
        except Exception as exc:  # pragma: no cover
            self._requests = None
            log.warning("requests unavailable (%s); LLM will fall back to MockLLM",
                        exc)

    # -- provider calls -----------------------------------------------------
    def _call_provider(self, messages, tools, temperature, max_tokens) -> dict:
        if self.provider in ("deepseek", "minimax"):
            return self._call_anthropic(messages, tools, temperature, max_tokens)
        if self.provider == "openai":
            return self._call_openai(messages, tools, temperature, max_tokens)
        raise ValueError(f"unknown provider: {self.provider}")

    def _call_anthropic(self, messages, tools, temperature, max_tokens) -> dict:
        key = _api_key(self.provider)
        if not key:
            raise RuntimeError(f"missing {self.provider.upper()}_API_KEY")
        if self._requests is None:
            raise RuntimeError("requests not available")
        base = DEEPSEEK_BASE if self.provider == "deepseek" else MINIMAX_BASE
        system, amessages = _to_anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": amessages,
        }
        if system:
            body["system"] = system
        atools = _to_anthropic_tools(tools)
        if atools:
            body["tools"] = atools
        resp = self._requests.post(
            base.rstrip("/") + "/v1/messages",
            json=body,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return self._parse_anthropic(data)

    @staticmethod
    def _parse_anthropic(data: dict) -> dict:
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": block.get("input") or {},
                })
        usage = data.get("usage") or {}
        return {
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "tokens_in": int(usage.get("input_tokens") or 0),
            "tokens_out": int(usage.get("output_tokens") or 0),
            "model": data.get("model", ""),
            "finish_reason": data.get("stop_reason", "end_turn"),
        }

    def _call_openai(self, messages, tools, temperature, max_tokens) -> dict:
        key = _api_key("openai")
        if not key:
            raise RuntimeError("missing OPENAI_API_KEY")
        if self._requests is None:
            raise RuntimeError("requests not available")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "temperature": temperature,
        }
        # newer OpenAI models prefer max_completion_tokens; fall back gracefully
        body["max_tokens"] = max_tokens
        otools = _to_openai_tools(tools)
        if otools:
            body["tools"] = otools
            body["tool_choice"] = "auto"
        resp = self._requests.post(
            OPENAI_BASE.rstrip("/") + "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return self._parse_openai(data)

    @staticmethod
    def _parse_openai(data: dict) -> dict:
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        tool_calls: list[dict] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or "{}"
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {"_raw": args}
            tool_calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""),
                               "arguments": args})
        usage = data.get("usage") or {}
        return {
            "text": msg.get("content") or "",
            "tool_calls": tool_calls,
            "tokens_in": int(usage.get("prompt_tokens") or 0),
            "tokens_out": int(usage.get("completion_tokens") or 0),
            "model": data.get("model", ""),
            "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason",
                                                                  "stop"),
        }

    # -- public API ---------------------------------------------------------
    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 temperature: float = 0.2, max_tokens: int = 1200) -> dict:
        if self._dead:
            return self._fallback(messages, tools, temperature, max_tokens)
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                res = self._call_provider(messages, tools, temperature, max_tokens)
                res.setdefault("flags", [])
                return res
            except Exception as exc:  # noqa: BLE001 - broad by design
                last_err = exc
                log.warning("LLM call failed (attempt %d/%d, provider=%s): %s",
                            attempt + 1, MAX_RETRIES + 1, self.provider, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF * (2 ** attempt))
        log.error("LLM exhausted retries (%s); falling back to MockLLM",
                  last_err)
        self._dead = True
        return self._fallback(messages, tools, temperature, max_tokens)

    def _fallback(self, messages, tools, temperature, max_tokens) -> dict:
        mock = MockLLM(provider="mock")
        res = mock.complete(messages, tools, temperature, max_tokens)
        flags = list(res.get("flags") or [])
        if "llm_fallback" not in flags:
            flags.append("llm_fallback")
        res["flags"] = flags
        return res


# --------------------------------------------------------------------------
# MockLLM — deterministic, offline, grounded
# --------------------------------------------------------------------------
_ASIN_RE = re.compile(r"\b[A-Z0-9]{10}\b")
_RUPEE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)


def _short(s: Any, n: int = 220) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _collect_facts(tool_results: list[dict]):
    """Flatten tool observations into (products, reviews, external, misc)."""
    products: list[dict] = []
    reviews: list[dict] = []
    external: list[dict] = []
    misc: list[str] = []

    def _is_product(d: dict) -> bool:
        return isinstance(d, dict) and ("title" in d) and (
            "price" in d or "rating" in d)

    def _visit(obj: Any) -> None:
        if isinstance(obj, dict):
            if _is_product(obj):
                products.append(obj)
                return
            if "review_id" in obj and isinstance(obj, dict):
                reviews.append(obj)
                return
            for v in obj.values():
                _visit(v)
        elif isinstance(obj, list):
            for v in obj:
                _visit(v)

    for tr in tool_results:
        try:
            obs = json.loads(tr["content"]) if isinstance(tr.get("content"), str) \
                else tr.get("content")
        except (ValueError, TypeError):
            obs = tr.get("content")
        if not isinstance(obs, dict):
            continue
        if obs.get("ok") is False:
            misc.append(f"(tool error: {_short(obs.get('error'))})")
            continue
        result = obs.get("result")
        _visit(result)
        # external search results
        if isinstance(result, dict):
            for r in result.get("results") or []:
                if isinstance(r, dict) and "url" in r:
                    external.append(r)
        # cart / order / price-history summaries
        if isinstance(result, dict) and result.get("status") and \
                ("order" in str(result) or "cart" in str(result) or
                 result.get("total") is not None):
            misc.append(json.dumps(result, ensure_ascii=False)[:400])

    # de-dup products by id
    seen: set[str] = set()
    uniq: list[dict] = []
    for p in products:
        pid = p.get("id") or p.get("product_id")
        key = str(pid)
        if key and key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq, reviews, external, misc


def _grounded_answer(tool_results: list[dict], query: str) -> str:
    products, reviews, external, misc = _collect_facts(tool_results)
    lines: list[str] = []
    if not products and not reviews and not external:
        lines.append("I couldn't retrieve catalogue facts for your request"
                     + (f" (“{_short(query, 80)}”)." if query else "."))
        if misc:
            lines.append(" " + " ".join(misc))
        return "\n".join(lines)

    if products:
        lines.append(f"I found {len(products)} matching product(s) in the catalogue:")
        for i, p in enumerate(products[:5], 1):
            title = p.get("title") or p.get("name") or "Unnamed product"
            brand = p.get("brand") or ""
            price = p.get("price")
            mrp = p.get("mrp") or p.get("price_mrp")
            rating = p.get("rating")
            rc = p.get("rating_count")
            bits = [f"[p{i}] {title}"]
            if brand:
                bits.append(f"by {brand}")
            if price is not None:
                bits.append(f"${price}")
                if mrp is not None and float(mrp) > float(price):
                    bits.append(f"(list price ${mrp})")
            if rating is not None:
                bits.append(f"rated {rating}★")
                if rc is not None:
                    bits.append(f"({rc} ratings)")
            if p.get("reason"):
                bits.append(f"— {_short(p.get('reason'), 80)}")
            lines.append("  " + " · ".join(bits))
        lines.append("Trade-off: the top result may not be the cheapest — "
                     "compare price vs rating before deciding.")
    if reviews:
        r = reviews[0]
        quote = _short(r.get("quote") or r.get("text"), 140)
        rid = r.get("review_id")
        lines.append(f"One review [r{rid}]: “{quote}” "
                     f"(rated {r.get('rating')}★).")
    if external:
        lines.append("External results (not from the catalogue):")
        for e in external[:3]:
            lines.append(f"  · {_short(e.get('title'), 90)} — {e.get('url')}")
    if misc:
        lines.append("Details: " + " | ".join(_short(m, 120) for m in misc))
    return "\n".join(lines)


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _extract_product_id(text: str) -> str | None:
    m = _ASIN_RE.search(text or "")
    return m.group(0) if m else None


def _extract_product_ids(text: str) -> list[str]:
    return list(dict.fromkeys(_ASIN_RE.findall(text or "")))


class MockLLM(LLMClient):
    """Deterministic offline fallback.

    First turn: emits one tool call (search_products) for the user's query.
    After tool results are present in the message history, produces a grounded
    answer that copies title / price / rating verbatim from the tool JSON.
    Never invents numbers.
    """

    def __init__(self, provider: str | None = "mock", model: str | None = None):
        super().__init__(provider="mock", model=model or "mock")

    def _find_tool(self, tools: list[dict] | None, name: str):
        if not tools:
            return None
        for t in tools:
            if t.get("name") == name:
                return t
        return None

    def _guess_args(self, name: str, text: str) -> dict:
        if name in ("search_products", "web_search_external"):
            return {"query": text or " ", "k": 10} if name == "search_products" \
                else {"query": text or " "}
        if name in ("get_product", "get_reviews", "similar_products",
                    "get_price_history", "add_to_cart"):
            args: dict[str, Any] = {}
            pid = _extract_product_id(text)
            if pid:
                args["product_id"] = pid
            if name == "get_reviews":
                args.setdefault("limit", 5)
            if name == "add_to_cart":
                args.setdefault("qty", 1)
            return args
        if name == "compare_products":
            return {"product_ids": _extract_product_ids(text), "query": text}
        if name == "view_cart":
            return {}
        if name == "checkout_cart":
            return {"ship_to": "self"}
        if name == "track_order":
            return {"order_id": ""}
        return {"query": text}

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 temperature: float = 0.2, max_tokens: int = 1200) -> dict:
        tools = tools or []
        tool_results = [m for m in messages if m.get("role") == "tool"]
        query = _last_user_text(messages)
        base = {
            "model": "mock",
            "finish_reason": "stop",
            "flags": ["mock_llm"],
        }
        if not tool_results and tools:
            # choose search_products when available, else the first tool
            tool = self._find_tool(tools, "search_products") or tools[0]
            name = tool.get("name", "search_products")
            args = self._guess_args(name, query)
            base["text"] = ""
            base["tool_calls"] = [{"id": f"mock_{name}", "name": name,
                                   "arguments": args}]
            base["finish_reason"] = "tool_use"
            base["tokens_in"] = sum(_count_tokens(m.get("content"))
                                    for m in messages)
            base["tokens_out"] = _count_tokens(json.dumps(args))
            return base
        text = _grounded_answer(tool_results, query)
        base["text"] = text
        base["tool_calls"] = []
        base["tokens_in"] = sum(_count_tokens(m.get("content")) for m in messages)
        base["tokens_out"] = _count_tokens(text)
        return base


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _selftest() -> None:
    mock = MockLLM()
    messages = [{"role": "system", "content": "You are a shopping assistant."},
                {"role": "user",
                 "content": "noise cancelling headphones under 5000 with 4.5 stars"}]
    tools = [{"name": "search_products", "description": "search",
              "parameters": {"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]}}]
    first = mock.complete(messages, tools=tools)
    print("=== turn 1 (tool call) ===")
    print(json.dumps(first, ensure_ascii=False, indent=2))
    # simulate a tool observation
    obs = {"ok": True,
           "result": [{"id": "B0TEST1234", "title": "Sony WH-1000XM5",
                       "brand": "Sony", "price": 4999.0, "mrp": 7999.0,
                       "rating": 4.6, "rating_count": 1200,
                       "reason": "noise cancelling, under budget"}],
           "citations": [], "error": None}
    messages.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": "mock_search_products",
                                     "name": "search_products",
                                     "arguments": first["tool_calls"][0]["arguments"]}]})
    messages.append({"role": "tool", "tool_call_id": "mock_search_products",
                     "name": "search_products",
                     "content": json.dumps(obs, ensure_ascii=False)})
    second = mock.complete(messages, tools=tools)
    print("\n=== turn 2 (grounded answer) ===")
    print(second["text"])
    print("\nflags:", second["flags"])
    assert "4999" in second["text"] and "4.6" in second["text"] \
        and "Sony" in second["text"], "MockLLM must copy facts verbatim"
    print("\nselftest OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if "--selftest" in sys.argv:
        _selftest()
