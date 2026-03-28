"""
Erisia v0.2 — Telemetry Engine
================================
Self-awareness about resource usage, cost, and performance.
Inspired by OpenJarvis's InstrumentedEngine and TelemetryStore.

Subscribes to the EventBus to passively record metrics:
  - Token usage per query
  - Estimated cost per engine
  - Latency per LLM call
  - Engine routing distribution (local vs cloud)
  - Fallback frequency
  - Daily/weekly cost trends

Feeds into:
  - Self-Audit: cost becomes an audit dimension  
  - Morning Briefings: "Yesterday you spent $0.04, 78% queries local"
  - SmartRouter: learn which query types succeed locally
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from erisia.erisia_config import get_config, BASE_DIR
from erisia.erisia_events import EventBus, EventType, Event

logger = logging.getLogger("erisia.telemetry")

# Pricing per 1M tokens (approximate, as of 2026)
_PRICING = {
    "groq": {"input": 0.05, "output": 0.08},       # Groq free tier / low cost
    "together": {"input": 0.20, "output": 0.20},    # Together.ai
    "gemini": {"input": 0.075, "output": 0.30},     # Gemini 2.0 Flash
    "ollama": {"input": 0.0, "output": 0.0},        # Local = free
}


@dataclass
class InferenceRecord:
    """A single LLM call record."""

    timestamp: float
    engine: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    was_fallback: bool
    query_hash: str  # first 40 chars of query for dedup


class TelemetryStore:
    """
    SQLite-backed telemetry store.
    Records every LLM call for analysis.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = BASE_DIR / "data" / "erisia_telemetry.db"
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inference_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    engine TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    was_fallback INTEGER DEFAULT 0,
                    query_hash TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inference_timestamp
                ON inference_log(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inference_engine
                ON inference_log(engine)
            """)
            conn.commit()

    def record(self, rec: InferenceRecord) -> None:
        """Insert an inference record."""
        with self._lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    """INSERT INTO inference_log
                    (timestamp, engine, model, input_tokens, output_tokens,
                     latency_ms, cost_usd, was_fallback, query_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rec.timestamp,
                        rec.engine,
                        rec.model,
                        rec.input_tokens,
                        rec.output_tokens,
                        rec.latency_ms,
                        rec.cost_usd,
                        1 if rec.was_fallback else 0,
                        rec.query_hash,
                    ),
                )
                conn.commit()
            except Exception as exc:
                logger.error("Telemetry record failed: %s", exc)

    def daily_summary(self, days: int = 1) -> dict[str, Any]:
        """Return a summary of the last N days."""
        with self._lock:
            try:
                conn = self._get_conn()
                cutoff = time.time() - (days * 86400)
                row = conn.execute(
                    """SELECT
                        COUNT(*) as total_calls,
                        COALESCE(SUM(cost_usd), 0) as total_cost,
                        COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                        COALESCE(AVG(latency_ms), 0) as avg_latency,
                        COALESCE(SUM(CASE WHEN was_fallback = 1 THEN 1 ELSE 0 END), 0) as fallback_count,
                        COALESCE(SUM(CASE WHEN engine = 'ollama' THEN 1 ELSE 0 END), 0) as local_count
                    FROM inference_log WHERE timestamp > ?""",
                    (cutoff,),
                ).fetchone()

                total = row[0] or 1
                return {
                    "period_days": days,
                    "total_calls": row[0],
                    "total_cost_usd": round(row[1], 4),
                    "total_tokens": row[2],
                    "avg_latency_ms": round(row[3], 1),
                    "fallback_rate": round((row[4] / total) * 100, 1),
                    "local_rate": round((row[5] / total) * 100, 1),
                }
            except Exception as exc:
                logger.error("Daily summary failed: %s", exc)
                return {}

    def engine_distribution(self, days: int = 7) -> dict[str, int]:
        """Return query count per engine for the last N days."""
        with self._lock:
            try:
                conn = self._get_conn()
                cutoff = time.time() - (days * 86400)
                rows = conn.execute(
                    """SELECT engine, COUNT(*) as cnt
                    FROM inference_log WHERE timestamp > ?
                    GROUP BY engine ORDER BY cnt DESC""",
                    (cutoff,),
                ).fetchall()
                return {row[0]: row[1] for row in rows}
            except Exception:
                return {}

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


class TelemetrySubscriber:
    """
    Subscribes to the EventBus and records inference metrics.
    Automatically calculates cost based on engine pricing.
    """

    def __init__(self, store: TelemetryStore, bus: EventBus) -> None:
        self._store = store
        self._bus = bus
        self._pending_starts: dict[str, float] = {}  # query_hash -> start_time
        self._lock = threading.Lock()

        # Subscribe to inference events
        bus.subscribe(EventType.INFERENCE_START, self._on_inference_start)
        bus.subscribe(EventType.INFERENCE_END, self._on_inference_end)
        bus.subscribe(EventType.INFERENCE_FALLBACK, self._on_fallback)

        logger.info("Telemetry subscriber active")

    def _on_inference_start(self, event: Event) -> None:
        query_hash = event.data.get("query_hash", "")
        with self._lock:
            self._pending_starts[query_hash] = event.timestamp

    def _on_inference_end(self, event: Event) -> None:
        data = event.data
        query_hash = data.get("query_hash", "")
        engine = data.get("engine", "unknown")
        model = data.get("model", "unknown")

        # Calculate latency
        with self._lock:
            start_time = self._pending_starts.pop(query_hash, event.timestamp)
        latency_ms = (event.timestamp - start_time) * 1000

        # Extract token counts from response
        input_tokens = data.get("input_tokens", 0)
        output_tokens = data.get("output_tokens", 0)

        # Calculate cost
        pricing = _PRICING.get(engine, {"input": 0.0, "output": 0.0})
        cost = (
            (input_tokens / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
        )

        record = InferenceRecord(
            timestamp=event.timestamp,
            engine=engine,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            was_fallback=data.get("was_fallback", False),
            query_hash=query_hash[:40],
        )
        self._store.record(record)

    def _on_fallback(self, event: Event) -> None:
        logger.info(
            "Inference fallback: %s -> %s (reason: %s)",
            event.data.get("from_engine", "?"),
            event.data.get("to_engine", "?"),
            event.data.get("reason", "unknown"),
        )


# ═══════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════

_store: Optional[TelemetryStore] = None
_subscriber: Optional[TelemetrySubscriber] = None


def get_telemetry_store() -> TelemetryStore:
    """Return the global TelemetryStore singleton."""
    global _store
    if _store is None:
        _store = TelemetryStore()
    return _store


def init_telemetry(bus: EventBus) -> TelemetrySubscriber:
    """Initialize telemetry and wire it to the EventBus."""
    global _subscriber
    if _subscriber is None:
        store = get_telemetry_store()
        _subscriber = TelemetrySubscriber(store, bus)
    return _subscriber
