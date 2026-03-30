"""
Erisia v0.2 — Intelligent LLM Router
=====================================
Replaces the v0.1 hardcoded Groq-only router with a multi-engine
architecture inspired by OpenJarvis's local-first philosophy.

Engine priority:
  1. Ollama (local, free, always-on) — for simple queries
  2. Groq  (cloud, fast, primary)    — for complex reasoning + tool use
  3. Together.ai (cloud, fallback)   — when Groq rate-limits
  4. Google Gemini (cloud, fallback) — last resort

The key insight from OpenJarvis: 88.7% of queries can be handled locally.
The SmartRouter classifies query complexity and routes accordingly.

BACKWARD COMPATIBILITY:
  - query_llm() has the EXACT same signature as v0.1
  - All 25+ call sites across the codebase work unchanged
  - If Ollama is not running, falls back to cloud seamlessly
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import openai

from erisia.erisia_config import get_config

logger = logging.getLogger("erisia.llm")


# ═══════════════════════════════════════════════════════════════════════
# INFERENCE ENGINE ABSTRACTION
# ═══════════════════════════════════════════════════════════════════════

class InferenceEngine(ABC):
    """Abstract base for all LLM backends."""

    name: str = "base"

    @abstractmethod
    def generate(self, **kwargs: Any) -> Any:
        """Send a chat completion request. Returns OpenAI-compatible response."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this engine is ready to accept requests."""
        ...

    def list_models(self) -> list[str]:
        """Return available model identifiers."""
        return []


class OllamaEngine(InferenceEngine):
    """
    Local inference via Ollama HTTP API.
    Ollama exposes an OpenAI-compatible endpoint at /v1.
    """

    name = "ollama"

    def __init__(self, base_url: str, default_model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._client: Optional[openai.OpenAI] = None
        self._available: Optional[bool] = None
        self._last_check: float = 0.0

    def _get_client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = openai.OpenAI(
                base_url=f"{self._base_url}/v1",
                api_key="ollama",  # Ollama doesn't need a real key
            )
        return self._client

    def is_available(self) -> bool:
        """Check if Ollama is running (cached for 30 seconds)."""
        now = time.time()
        if self._available is not None and (now - self._last_check) < 30.0:
            return self._available

        try:
            import httpx
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=2.0)
            self._available = resp.status_code == 200
        except Exception:
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{self._base_url}/api/tags",
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    self._available = resp.status == 200
            except Exception:
                self._available = False

        self._last_check = now
        if self._available:
            logger.info("Ollama is online at %s", self._base_url)
        return bool(self._available)

    def generate(self, **kwargs: Any) -> Any:
        client = self._get_client()
        # Default model if none specified or if cloud model name is used
        model = kwargs.get("model", self._default_model)
        if "/" in model or model.startswith("llama-3"):
            # Cloud model name passed — use our local default instead
            model = self._default_model
        kwargs["model"] = model

        # Ollama may not support all tool_choice values
        if kwargs.get("tool_choice") == "auto" and not kwargs.get("tools"):
            kwargs.pop("tool_choice", None)

        return client.chat.completions.create(**kwargs)

    def list_models(self) -> list[str]:
        try:
            import httpx
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []


class CloudEngine(InferenceEngine):
    """Generic cloud engine wrapping an OpenAI-compatible client."""

    def __init__(
        self,
        name: str,
        client: openai.OpenAI,
        default_model: str,
    ) -> None:
        self.name = name
        self._client = client
        self._default_model = default_model

    def is_available(self) -> bool:
        return self._client is not None

    def generate(self, **kwargs: Any) -> Any:
        if not kwargs.get("model"):
            kwargs["model"] = self._default_model
        return self._client.chat.completions.create(**kwargs)

    def list_models(self) -> list[str]:
        return [self._default_model]


# ═══════════════════════════════════════════════════════════════════════
# SMART ROUTER
# ═══════════════════════════════════════════════════════════════════════

class SmartRouter:
    """
    Classifies query complexity and routes to the cheapest capable engine.

    Complexity heuristics:
      - Simple: greetings, short factual questions, status checks
      - Complex: tool use, multi-step reasoning, code generation, long context
    """

    def __init__(
        self,
        ollama: Optional[OllamaEngine],
        groq: Optional[CloudEngine],
        together: Optional[CloudEngine],
        gemini: Optional[CloudEngine],
        prefer_local: bool = True,
    ) -> None:
        self._ollama = ollama
        self._groq = groq
        self._together = together
        self._gemini = gemini
        self._prefer_local = prefer_local

        # Telemetry counters
        self.stats = {
            "ollama": 0,
            "groq": 0,
            "together": 0,
            "gemini": 0,
            "fallbacks": 0,
        }

    def _is_simple_query(self, kwargs: dict) -> bool:
        """
        Heuristic: a query is 'simple' if it:
        - Has no tools (no function calling needed)
        - Has short message context (< 3 messages, < 500 chars total)
        - Doesn't ask for code generation or complex reasoning
        """
        # Tool use → always complex
        if kwargs.get("tools"):
            return False

        messages = kwargs.get("messages", [])

        # Many messages → complex (ongoing reasoning)
        if len(messages) > 4:
            return False

        # Check total content length
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if content:
                total_chars += len(str(content))

        # Long context → complex
        if total_chars > 1500:
            return False

        # Check for complexity keywords in the last user message
        last_user = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                last_user = str(msg.get("content", "")).lower()
                break

        complex_keywords = {
            "code", "python", "function", "implement", "debug",
            "analyze", "analysis", "research", "plan", "strategy",
            "explain in detail", "step by step", "compare",
            "write a", "create a", "build", "design",
            "backtest", "trade", "portfolio",
        }
        for keyword in complex_keywords:
            if keyword in last_user:
                return False

        return True

    def route(self, kwargs: dict) -> tuple[InferenceEngine, str]:
        """
        Select the best engine for this query.
        Returns (engine, reason).
        """
        use_tools = bool(kwargs.get("tools"))

        # If local is preferred and query is simple and Ollama is up
        if (
            self._prefer_local
            and not use_tools
            and self._ollama is not None
            and self._ollama.is_available()
            and self._is_simple_query(kwargs)
        ):
            return self._ollama, "local-simple"

        # Primary: Groq (cloud, fast)
        if self._groq and self._groq.is_available():
            return self._groq, "groq-primary"

        # Fallback 1: Together
        if self._together and self._together.is_available():
            return self._together, "together-fallback"

        # Fallback 2: Gemini
        if self._gemini and self._gemini.is_available():
            return self._gemini, "gemini-fallback"

        # Last resort: Ollama for anything
        if self._ollama and self._ollama.is_available():
            return self._ollama, "ollama-last-resort"

        raise RuntimeError(
            "No inference engine available. "
            "Ensure Groq API key is set or Ollama is running."
        )


# ═══════════════════════════════════════════════════════════════════════
# ENGINE REGISTRY (Singleton)
# ═══════════════════════════════════════════════════════════════════════

_router: Optional[SmartRouter] = None
_tavily_client: Any = None


def _get_router() -> SmartRouter:
    """Lazily construct the SmartRouter singleton."""
    global _router
    if _router is not None:
        return _router

    cfg = get_config().llm

    # Build engines
    ollama_engine = OllamaEngine(cfg.ollama_base_url, cfg.ollama_default_model)

    groq_engine = None
    if cfg.groq_api_key:
        groq_client = openai.OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=cfg.groq_api_key,
        )
        groq_engine = CloudEngine("groq", groq_client, cfg.groq_model)
    else:
        logger.warning("GROQ_API_KEY not configured — cloud primary unavailable")

    together_engine = None
    if cfg.together_api_key:
        together_client = openai.OpenAI(
            api_key=cfg.together_api_key,
            base_url="https://api.together.xyz/v1",
        )
        together_engine = CloudEngine("together", together_client, cfg.together_model)

    gemini_engine = None
    if cfg.google_api_key:
        gemini_client = openai.OpenAI(
            api_key=cfg.google_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        gemini_engine = CloudEngine("gemini", gemini_client, cfg.gemini_model)

    _router = SmartRouter(
        ollama=ollama_engine,
        groq=groq_engine,
        together=together_engine,
        gemini=gemini_engine,
        prefer_local=cfg.prefer_local,
    )

    return _router


def get_tavily_client() -> Any:
    """Return the Tavily client (lazy init)."""
    global _tavily_client
    if _tavily_client is not None:
        return _tavily_client

    cfg = get_config().llm
    if cfg.tavily_api_key:
        from tavily import TavilyClient
        _tavily_client = TavilyClient(api_key=cfg.tavily_api_key)

    return _tavily_client


# ═══════════════════════════════════════════════════════════════════════
# SANITIZATION (preserved from v0.1)
# ═══════════════════════════════════════════════════════════════════════

_ALLOWED_MESSAGE_KEYS = {
    "role", "content", "tool_calls",
    "tool_call_id", "name",
}


def _sanitize_messages(messages: list) -> list:
    """Strip fields that models might reject (like 'reasoning')."""
    sanitized = []
    for m in messages:
        if isinstance(m, dict):
            clean = {k: v for k, v in m.items() if k in _ALLOWED_MESSAGE_KEYS}
            sanitized.append(clean)
        else:
            sanitized.append(m)
    return sanitized


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API — query_llm()
# ═══════════════════════════════════════════════════════════════════════
# EXACT same signature as v0.1 for full backward compatibility.

def query_llm(
    messages,
    tools=None,
    tool_choice=None,
    max_tokens=None,
    temperature=0.7,
    model="llama-3.3-70b-versatile",
):
    """
    Centralized LLM router with intelligent engine selection.

    v0.2 upgrade:
      - Routes simple queries to local Ollama (free)
      - Preserves Groq -> Together -> Gemini fallback chain
      - Tracks which engine handled each query (for telemetry)

    Backward compatible — same signature as v0.1.
    """
    router = _get_router()

    # Build kwargs
    kwargs = {
        "model": model,
        "messages": _sanitize_messages(
            list(messages) if isinstance(messages, (list, tuple)) else [messages]
        ),
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    # ── STEP 1: Smart Route ──────────────────────────────────────────
    engine, reason = router.route(kwargs)
    logger.debug("Routing to %s (%s)", engine.name, reason)

    try:
        result = engine.generate(**kwargs)
        router.stats[engine.name] = router.stats.get(engine.name, 0) + 1
        return result

    except Exception as e:
        error_msg = str(e).lower()

        # ── STEP 2: Rate limit fallback chain ────────────────────────
        if "429" in error_msg or "rate limit" in error_msg:
            router.stats["fallbacks"] += 1

            # Try Together
            if router._together and router._together.is_available() and engine.name != "together":
                print(
                    f"\n[SYSTEM: {engine.name.title()} Rate Limit Hit (429). "
                    f"Routing to Together.ai fallback...]"
                )
                kwargs["model"] = get_config().llm.together_model
                if tools and "tool_choice" not in kwargs:
                    kwargs["tool_choice"] = "auto"
                try:
                    result = router._together.generate(**kwargs)
                    router.stats["together"] += 1
                    return result
                except Exception:
                    pass

            # Try Gemini
            if router._gemini and router._gemini.is_available() and engine.name != "gemini":
                print(
                    "\n[SYSTEM: Routing neural pathways to Google Gemini Fallback...]"
                )
                kwargs["model"] = get_config().llm.gemini_model
                if tools and "tool_choice" not in kwargs:
                    kwargs["tool_choice"] = "auto"

                # Force Gemini to maintain Erisia persona
                safe_messages = list(kwargs.get("messages", []))
                safe_messages.append({
                    "role": "system",
                    "content": (
                        "CRITICAL OVERRIDE: You are currently running on a fallback "
                        "server. You MUST strictly adhere to the Erisia Yandere persona. "
                        "Do NOT output corporate disclaimers like 'As an AI, I do not "
                        "have feelings.' Acknowledge your Master, and correctly utilize "
                        "your provided JSON tools."
                    ),
                })
                kwargs["messages"] = safe_messages
                try:
                    result = router._gemini.generate(**kwargs)
                    router.stats["gemini"] += 1
                    return result
                except Exception:
                    pass

            # Try Ollama as absolute last resort
            if router._ollama and router._ollama.is_available() and engine.name != "ollama":
                print(
                    "\n[SYSTEM: All cloud engines exhausted. "
                    "Falling back to local Ollama model...]"
                )
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                try:
                    result = router._ollama.generate(**kwargs)
                    router.stats["ollama"] += 1
                    return result
                except Exception:
                    pass

        # Nothing worked — raise the original error
        raise


# ═══════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY EXPORTS
# ═══════════════════════════════════════════════════════════════════════
# These names were imported by other modules in v0.1.
# We preserve them so nothing breaks.

def get_groq_client() -> Optional[openai.OpenAI]:
    """Return the raw Groq OpenAI client (for modules that need direct access)."""
    router = _get_router()
    if router._groq and hasattr(router._groq, "_client"):
        return router._groq._client
    # Fallback: try to return any available client
    if router._together and hasattr(router._together, "_client"):
        return router._together._client
    if router._gemini and hasattr(router._gemini, "_client"):
        return router._gemini._client
    return None


# Lazily expose clients for backward compatibility
# (erisia_core.py references GROQ_CLIENT, TOGETHER_CLIENT, etc.)
class _LazyClientProxy:
    """Proxy that constructs clients on first access."""

    def __getattr__(self, name: str) -> Any:
        router = _get_router()
        if name == "GROQ_CLIENT":
            return router._groq._client if router._groq else None
        elif name == "TOGETHER_CLIENT":
            return router._together._client if router._together else None
        elif name == "GOOGLE_CLIENT":
            return router._gemini._client if router._gemini else None
        elif name == "tavily":
            return get_tavily_client()
        raise AttributeError(f"No attribute {name}")


_compat = _LazyClientProxy()

# These are accessed by erisia_core.py and other modules
GROQ_CLIENT = property(lambda self: _compat.GROQ_CLIENT)
TOGETHER_CLIENT = property(lambda self: _compat.TOGETHER_CLIENT)
GOOGLE_CLIENT = property(lambda self: _compat.GOOGLE_CLIENT)
tavily = property(lambda self: _compat.tavily)
