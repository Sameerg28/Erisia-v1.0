"""
Erisia v0.2 — Learning Loop
=============================
Analyzes the 259+ prompt/completion pairs in erisia_training_data.jsonl
to extract behavioral patterns, tool preferences, and query signatures.

This transforms Erisia from a system that passively records experience
into one that actively learns from it.

Capabilities:
  1. Pattern Extraction — find recurring query types and response styles
  2. Tool Usage Analytics — which tools are used most/least
  3. Heuristic Auto-Generation — propose new behavioral rules
  4. Router Tuning — feed local vs cloud success data to SmartRouter
  5. Briefing Insights — "This week: 78% local, top tool: web_search"

Inspired by OpenJarvis's trace-based learning with DSPy integration.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from erisia.erisia_config import get_config, BASE_DIR

logger = logging.getLogger("erisia.learning")


class LearningLoop:
    """
    Extracts actionable intelligence from Erisia's training data
    and telemetry records.
    """

    def __init__(
        self,
        training_data_path: Optional[Path] = None,
        heuristics_path: Optional[Path] = None,
    ) -> None:
        cfg = get_config()
        self._training_path = training_data_path or cfg.paths.training_data_file
        self._heuristics_path = heuristics_path or cfg.paths.heuristics_file
        self._lock = threading.Lock()
        self._last_analysis_time: float = 0.0
        self._cached_insights: Optional[dict] = None

    # ── Data Loading ─────────────────────────────────────────────────

    def _load_training_data(self) -> list[dict]:
        """Load all prompt/completion pairs from JSONL."""
        pairs = []
        if not self._training_path.exists():
            return pairs
        try:
            with open(self._training_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if "prompt" in record and "completion" in record:
                            pairs.append(record)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.error("Failed to load training data: %s", exc)
        return pairs

    def _load_telemetry(self) -> list[dict]:
        """Load recent telemetry records."""
        telem_db = BASE_DIR / "data" / "erisia_telemetry.db"
        if not telem_db.exists():
            return []
        try:
            import sqlite3
            conn = sqlite3.connect(str(telem_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM inference_log ORDER BY timestamp DESC LIMIT 500"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("Failed to load telemetry: %s", exc)
            return []

    # ── Pattern Extraction ───────────────────────────────────────────

    def _extract_query_patterns(self, data: list[dict]) -> dict:
        """Classify prompts into categories and find patterns."""
        categories = Counter()
        prompt_lengths = []
        completion_lengths = []
        greeting_words = {"hey", "hello", "hi", "good morning", "what's up", "sup"}

        for record in data:
            prompt = record.get("prompt", "").strip().lower()
            completion = record.get("completion", "")
            prompt_lengths.append(len(prompt))
            completion_lengths.append(len(completion))

            # Classify
            if any(g in prompt for g in greeting_words):
                categories["greeting"] += 1
            elif "?" in prompt and len(prompt) < 100:
                categories["short_question"] += 1
            elif any(kw in prompt for kw in ("code", "python", "function", "implement", "fix", "debug")):
                categories["coding"] += 1
            elif any(kw in prompt for kw in ("research", "find", "search", "look up", "what is")):
                categories["research"] += 1
            elif any(kw in prompt for kw in ("plan", "strategy", "goal", "mission")):
                categories["planning"] += 1
            elif any(kw in prompt for kw in ("trade", "stock", "portfolio", "backtest", "oracle")):
                categories["trading"] += 1
            elif any(kw in prompt for kw in ("remember", "memory", "recall", "forget")):
                categories["memory_ops"] += 1
            elif any(kw in prompt for kw in ("skill", "forge", "tool", "create a")):
                categories["skill_forge"] += 1
            else:
                categories["general"] += 1

        avg_prompt_len = sum(prompt_lengths) / max(len(prompt_lengths), 1)
        avg_completion_len = sum(completion_lengths) / max(len(completion_lengths), 1)

        return {
            "total_interactions": len(data),
            "categories": dict(categories.most_common()),
            "avg_prompt_length": round(avg_prompt_len, 1),
            "avg_completion_length": round(avg_completion_len, 1),
            "ratio_completion_to_prompt": round(
                avg_completion_len / max(avg_prompt_len, 1), 2
            ),
        }

    def _extract_tool_patterns(self, data: list[dict]) -> dict:
        """Find which tools are mentioned most in completions."""
        tool_mentions = Counter()
        tool_keywords = {
            "web_search": ["web_search", "tavily", "search the web"],
            "execute_local_os_command": ["execute_local", "os_command", "run command"],
            "read_file": ["read_file", "reading file"],
            "write_file": ["write_file", "writing file"],
            "forge_new_skill": ["forge_new_skill", "forge skill", "forging"],
            "save_heuristic_rule": ["heuristic", "save_heuristic"],
            "add_memory_document": ["add_memory", "memory document"],
            "query_memory_documents": ["query_memory", "memory search"],
            "screenshot": ["screenshot", "screen capture"],
            "vision": ["vision", "analyze image"],
        }

        for record in data:
            completion = record.get("completion", "").lower()
            for tool_name, patterns in tool_keywords.items():
                if any(p in completion for p in patterns):
                    tool_mentions[tool_name] += 1

        return {
            "tool_usage": dict(tool_mentions.most_common()),
            "most_used_tool": tool_mentions.most_common(1)[0][0] if tool_mentions else "none",
            "least_used_tool": tool_mentions.most_common()[-1][0] if tool_mentions else "none",
        }

    def _extract_telemetry_patterns(self, telemetry: list[dict]) -> dict:
        """Analyze telemetry for routing optimization insights."""
        if not telemetry:
            return {"status": "no_telemetry_data"}

        engine_counts = Counter()
        total_cost = 0.0
        total_latency = 0.0
        fallback_count = 0

        for record in telemetry:
            engine = record.get("engine", "unknown")
            engine_counts[engine] += 1
            total_cost += record.get("cost_usd", 0.0)
            total_latency += record.get("latency_ms", 0.0)
            if record.get("was_fallback"):
                fallback_count += 1

        total = max(len(telemetry), 1)
        local_count = engine_counts.get("ollama", 0)

        return {
            "engine_distribution": dict(engine_counts),
            "total_cost_usd": round(total_cost, 4),
            "avg_latency_ms": round(total_latency / total, 1),
            "local_rate_pct": round((local_count / total) * 100, 1),
            "fallback_rate_pct": round((fallback_count / total) * 100, 1),
        }

    # ── Heuristic Generation ────────────────────────────────────────

    def _generate_heuristic_candidates(
        self,
        query_patterns: dict,
        tool_patterns: dict,
    ) -> list[str]:
        """Generate candidate heuristic rules from observed patterns."""
        candidates = []
        categories = query_patterns.get("categories", {})
        total = query_patterns.get("total_interactions", 1)

        # If greetings are > 10% of interactions, the user likes casual chat
        greeting_pct = (categories.get("greeting", 0) / max(total, 1)) * 100
        if greeting_pct > 10:
            candidates.append(
                f"Master Sameer frequently opens with casual greetings "
                f"({greeting_pct:.0f}% of interactions). Always respond warmly "
                f"and personally before addressing any implicit task."
            )

        # If coding is the dominant category
        coding_pct = (categories.get("coding", 0) / max(total, 1)) * 100
        if coding_pct > 20:
            candidates.append(
                f"Coding tasks make up {coding_pct:.0f}% of interactions. "
                f"Always include runnable code with error handling."
            )

        # If trading is significant
        trading_pct = (categories.get("trading", 0) / max(total, 1)) * 100
        if trading_pct > 5:
            candidates.append(
                f"Trading/Oracle queries represent {trading_pct:.0f}% of interactions. "
                f"Proactively include risk metrics and Sharpe ratios in analysis."
            )

        # Response verbosity insight
        ratio = query_patterns.get("ratio_completion_to_prompt", 1.0)
        if ratio > 5.0:
            candidates.append(
                f"Erisia's average response is {ratio:.1f}x longer than the prompt. "
                f"Master Sameer prefers detailed, thorough responses."
            )
        elif ratio < 2.0:
            candidates.append(
                f"Erisia's response ratio is only {ratio:.1f}x the prompt length. "
                f"Consider providing more detailed responses."
            )

        # Most used tool insight
        most_used = tool_patterns.get("most_used_tool", "none")
        if most_used != "none":
            candidates.append(
                f"The most frequently used tool is '{most_used}'. "
                f"Prioritize this tool in relevant contexts."
            )

        return candidates

    # ── Main Analysis ────────────────────────────────────────────────

    def analyze(self, force: bool = False) -> dict:
        """
        Run the full learning analysis.
        Results are cached for 1 hour unless force=True.
        """
        with self._lock:
            now = time.time()
            if (
                not force
                and self._cached_insights is not None
                and (now - self._last_analysis_time) < 3600
            ):
                return self._cached_insights

            # Load data
            training_data = self._load_training_data()
            telemetry = self._load_telemetry()

            # Extract patterns
            query_patterns = self._extract_query_patterns(training_data)
            tool_patterns = self._extract_tool_patterns(training_data)
            telemetry_patterns = self._extract_telemetry_patterns(telemetry)

            # Generate heuristic candidates
            heuristic_candidates = self._generate_heuristic_candidates(
                query_patterns, tool_patterns
            )

            insights = {
                "timestamp": time.time(),
                "training_data_count": len(training_data),
                "telemetry_records": len(telemetry),
                "query_patterns": query_patterns,
                "tool_patterns": tool_patterns,
                "telemetry_patterns": telemetry_patterns,
                "heuristic_candidates": heuristic_candidates,
            }

            self._cached_insights = insights
            self._last_analysis_time = now

            logger.info(
                "Learning analysis complete: %d interactions, %d telemetry records, "
                "%d heuristic candidates",
                len(training_data),
                len(telemetry),
                len(heuristic_candidates),
            )

            return insights

    def summary_for_briefing(self) -> str:
        """Return a human-readable summary for morning briefings."""
        insights = self.analyze()
        lines = ["## Learning Insights"]

        qp = insights.get("query_patterns", {})
        tp = insights.get("telemetry_patterns", {})

        lines.append(
            f"- **{qp.get('total_interactions', 0)}** training interactions analyzed"
        )

        categories = qp.get("categories", {})
        if categories:
            top3 = list(categories.items())[:3]
            cat_str = ", ".join(f"{k} ({v})" for k, v in top3)
            lines.append(f"- Top categories: {cat_str}")

        if tp.get("local_rate_pct") is not None:
            lines.append(
                f"- Local inference rate: **{tp['local_rate_pct']}%** "
                f"(${tp.get('total_cost_usd', 0):.4f} total cost)"
            )

        candidates = insights.get("heuristic_candidates", [])
        if candidates:
            lines.append(f"- **{len(candidates)}** heuristic candidates ready for review")

        return "\n".join(lines)

    def as_tool_output(self) -> str:
        """Return analysis results formatted for LLM tool consumption."""
        insights = self.analyze()
        return json.dumps(insights, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════

_loop: Optional[LearningLoop] = None


def get_learning_loop() -> LearningLoop:
    """Return the global LearningLoop singleton."""
    global _loop
    if _loop is None:
        _loop = LearningLoop()
    return _loop
