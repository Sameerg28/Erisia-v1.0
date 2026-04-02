"""
Erisia v0.2 — Centralized Configuration
========================================
Single source of truth for all API keys, paths, and system settings.
Eliminates the 3x duplicated _load_api_key() across erisia_llm.py,
erisia_core.py, and erisia_daemon.py.

Inspired by OpenJarvis's JarvisConfig pattern.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger("erisia.config")

# ── Project Root ─────────────────────────────────────────────────────
# Two levels up from this file: erisia_config.py -> erisia/ -> src/ -> project
BASE_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = BASE_DIR / "src"

# Load .env from config folder
_env_path = BASE_DIR / "config" / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=False)


# ── Key Loader ───────────────────────────────────────────────────────
def _load_api_key(env_var: str, labels: set[str]) -> Optional[str]:
    """
    Load an API key by checking:
      1. Environment variable (highest priority)
      2. config/api.txt labels (fallback)
    """
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env.strip()

    # Try api.txt
    for candidate_path in (BASE_DIR / "config" / "api.txt", BASE_DIR / "api.txt"):
        if not candidate_path.exists():
            continue
        try:
            text = candidate_path.read_text(encoding="utf-8")
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key_part, label_part = line.split("=", 1)
                key_part = key_part.strip()
                label_part = label_part.strip().lower()
                if key_part and label_part in labels:
                    return key_part
        except Exception as exc:
            logger.warning("Failed to read api.txt at %s: %s", candidate_path, exc)

    return None


# ── Dataclass Configs ────────────────────────────────────────────────

@dataclass(frozen=True)
class LLMConfig:
    """Configuration for all LLM inference engines."""

    groq_api_key: Optional[str] = None
    together_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "qwen2.5:0.5b"

    # Cloud models
    groq_model: str = "llama-3.3-70b-versatile"
    together_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    gemini_model: str = "gemini-2.0-flash"

    # Routing
    prefer_local: bool = True  # Route simple queries to Ollama when available
    local_max_tokens: int = 2048  # Limit local model output length


@dataclass(frozen=True)
class PathConfig:
    """All file system paths, computed from BASE_DIR."""

    base_dir: Path = BASE_DIR

    # Config
    env_file: Path = field(default_factory=lambda: BASE_DIR / "config" / ".env")
    mission_file: Path = field(default_factory=lambda: BASE_DIR / "config" / "erisia_missions.txt")
    consciousness_file: Path = field(default_factory=lambda: BASE_DIR / "config" / "Erisia_Consciousness.md")

    # Skills
    skills_dir: Path = field(default_factory=lambda: BASE_DIR / "skills")
    pending_skills_dir: Path = field(default_factory=lambda: BASE_DIR / "skills" / "pending")
    sandbox_dir: Path = field(default_factory=lambda: BASE_DIR / "skills" / "sandbox")

    # Data & Memory
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    memory_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "erisia_memory")
    training_data_file: Path = field(default_factory=lambda: BASE_DIR / "data" / "erisia_training_data.jsonl")
    goal_stack_file: Path = field(default_factory=lambda: BASE_DIR / "data" / "erisia_goal_stack.json")
    heuristics_file: Path = field(default_factory=lambda: BASE_DIR / "data" / "erisia_heuristics.json")
    plans_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "plans")
    world_state_file: Path = field(default_factory=lambda: BASE_DIR / "data" / "erisia_world_state.json")

    # Databases
    erisia_db: Path = field(default_factory=lambda: BASE_DIR / "erisia_memory.db")
    oracle_db: Path = field(default_factory=lambda: BASE_DIR / "oracle_memory.db")

    # Reports
    report_dir: Path = field(default_factory=lambda: BASE_DIR / "reports")


@dataclass(frozen=True)
class OracleConfig:
    """Configuration for the Oracle trading subsystem."""

    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets/v2"


@dataclass
class ErisiaConfig:
    """
    Master configuration object — single source of truth.
    Constructed once at module load, shared across all modules.
    """

    llm: LLMConfig = field(default_factory=LLMConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    oracle: OracleConfig = field(default_factory=OracleConfig)

    # Feature flags
    enable_meta_review: bool = True
    enable_world_state_heavy_dump: bool = False
    passive_cognition_interval: int = 90
    max_dynamic_tools_per_query: int = 3
    goal_stale_days: int = 7


# ── Singleton Construction ───────────────────────────────────────────

_config: Optional[ErisiaConfig] = None


def get_config() -> ErisiaConfig:
    """
    Return the global ErisiaConfig singleton.
    Lazily constructed on first call.
    """
    global _config
    if _config is not None:
        return _config

    # Load all API keys
    groq_key = _load_api_key("GROQ_API_KEY", {"groq api", "groq"})
    together_key = _load_api_key("TOGETHER_API_KEY", {"together api", "togetherai", "together"})
    google_key = _load_api_key("GOOGLE_API_KEY", {"google api", "gemini", "gemini api"})
    openrouter_key = _load_api_key("OPENROUTER_API_KEY", {"openrouter api", "openrouter"})
    tavily_key = _load_api_key("TAVILY_API_KEY", {"tavily", "tavily api", "talvi"})
    alpaca_key = os.environ.get("ALPACA_API_KEY")
    alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")
    alpaca_base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

    # Ollama settings from env
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
    prefer_local = os.environ.get("ERISIA_PREFER_LOCAL", "1").strip() == "1"

    # Feature flags from env
    enable_meta = os.environ.get("ERISIA_ENABLE_META_REVIEW", "1").strip() == "1"
    enable_heavy_dump = os.environ.get("ERISIA_ENABLE_HEAVY_UI_DUMP", "0").strip() == "1"
    passive_interval = int(os.environ.get("ERISIA_PASSIVE_COGNITION_INTERVAL", "90"))
    try:
        max_dyn_tools = max(1, int(os.environ.get("ERISIA_MAX_DYNAMIC_TOOLS", "3")))
    except (ValueError, TypeError):
        max_dyn_tools = 3

    _config = ErisiaConfig(
        llm=LLMConfig(
            groq_api_key=groq_key,
            together_api_key=together_key,
            google_api_key=google_key,
            openrouter_api_key=openrouter_key,
            tavily_api_key=tavily_key,
            ollama_base_url=ollama_url,
            ollama_default_model=ollama_model,
            prefer_local=prefer_local,
        ),
        paths=PathConfig(),
        oracle=OracleConfig(
            alpaca_api_key=alpaca_key,
            alpaca_secret_key=alpaca_secret,
            alpaca_base_url=alpaca_base,
        ),
        enable_meta_review=enable_meta,
        enable_world_state_heavy_dump=enable_heavy_dump,
        passive_cognition_interval=passive_interval,
        max_dynamic_tools_per_query=max_dyn_tools,
    )

    # Log config status
    logger.info(
        "Config loaded — Groq:%s Together:%s Google:%s Ollama:%s Tavily:%s",
        "✓" if groq_key else "✗",
        "✓" if together_key else "✗",
        "✓" if google_key else "✗",
        ollama_url,
        "✓" if tavily_key else "✗",
    )

    return _config
