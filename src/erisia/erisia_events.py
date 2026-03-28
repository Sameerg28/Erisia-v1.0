"""
Erisia v0.2 — EventBus (Neural Connective Tissue)
===================================================
Lightweight publish-subscribe event system inspired by OpenJarvis's
EventBus pattern.

This enables emergent behavior: when one module emits an event,
other modules can react without explicit coupling.

Examples:
  - SKILL_FORGED  -> Episodic memory logs it, Identity updates forge count
  - INFERENCE_FALLBACK -> Telemetry logs cost spike
  - USER_IDLE -> Daemon starts autonomous missions
  - STRESS_CHANGE -> Briefing adapts verbosity

Thread-safe, zero external dependencies.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("erisia.events")


# ═══════════════════════════════════════════════════════════════════════
# EVENT TYPES
# ═══════════════════════════════════════════════════════════════════════

class EventType(Enum):
    """All event types in the Erisia system."""

    # ── LLM / Inference ──────────────────────────────────────────────
    INFERENCE_START = "inference.start"
    INFERENCE_END = "inference.end"
    INFERENCE_FALLBACK = "inference.fallback"
    INFERENCE_ERROR = "inference.error"

    # ── Memory ───────────────────────────────────────────────────────
    MEMORY_INDEXED = "memory.indexed"
    MEMORY_RECALLED = "memory.recalled"
    EPISODIC_LOGGED = "episodic.logged"
    EPISODIC_REFLECTED = "episodic.reflected"
    GRAPH_UPDATED = "graph.updated"

    # ── Skills ───────────────────────────────────────────────────────
    SKILL_FORGED = "skill.forged"
    SKILL_APPROVED = "skill.approved"
    SKILL_REJECTED = "skill.rejected"
    SKILL_EXECUTED = "skill.executed"
    SKILL_FAILED = "skill.failed"

    # ── Cognition ────────────────────────────────────────────────────
    GOAL_PUSHED = "goal.pushed"
    GOAL_COMPLETED = "goal.completed"
    GOAL_DECAYED = "goal.decayed"
    AUDIT_COMPLETED = "audit.completed"
    PLAN_CREATED = "plan.created"
    PLAN_STEP_DONE = "plan.step_done"
    HEURISTIC_LEARNED = "heuristic.learned"

    # ── Daemon ───────────────────────────────────────────────────────
    DAEMON_MISSION_START = "daemon.mission.start"
    DAEMON_MISSION_END = "daemon.mission.end"
    DAEMON_IDLE = "daemon.idle"

    # ── User / Identity ──────────────────────────────────────────────
    USER_MESSAGE = "user.message"
    USER_IDLE = "user.idle"
    STRESS_CHANGE = "identity.stress_change"
    CONSCIOUSNESS_UPDATED = "identity.consciousness_updated"

    # ── System ───────────────────────────────────────────────────────
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"
    HEALTH_CHECK = "system.health_check"

    # ── Telemetry ────────────────────────────────────────────────────
    TELEMETRY_RECORDED = "telemetry.recorded"

    # Generic wildcard — subscribe to ALL events
    ALL = "*"


# ═══════════════════════════════════════════════════════════════════════
# EVENT DATA
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Event:
    """An event emitted by a module."""

    type: EventType
    data: dict = field(default_factory=dict)
    source: str = "unknown"
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"Event({self.type.value}, source={self.source}, keys={list(self.data.keys())})"


# ═══════════════════════════════════════════════════════════════════════
# EVENT BUS
# ═══════════════════════════════════════════════════════════════════════

# Type alias for subscribers
Subscriber = Callable[[Event], None]


class EventBus:
    """
    Thread-safe publish-subscribe event bus.

    Usage:
        bus = EventBus()

        # Subscribe
        def on_skill_forged(event: Event):
            print(f"New skill: {event.data['name']}")
        bus.subscribe(EventType.SKILL_FORGED, on_skill_forged)

        # Emit
        bus.emit(EventType.SKILL_FORGED, {"name": "web_scraper"}, source="forge")

        # Unsubscribe
        bus.unsubscribe(EventType.SKILL_FORGED, on_skill_forged)
    """

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Subscriber]] = {}
        self._lock = threading.RLock()
        self._event_history: list[Event] = []
        self._max_history = 500

    def subscribe(
        self,
        event_type: EventType,
        callback: Subscriber,
    ) -> None:
        """Register a callback for an event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                logger.debug(
                    "Subscribed %s to %s",
                    getattr(callback, "__name__", str(callback)),
                    event_type.value,
                )

    def unsubscribe(
        self,
        event_type: EventType,
        callback: Subscriber,
    ) -> None:
        """Remove a callback for an event type."""
        with self._lock:
            subs = self._subscribers.get(event_type, [])
            if callback in subs:
                subs.remove(callback)

    def emit(
        self,
        event_type: EventType,
        data: Optional[dict] = None,
        source: str = "unknown",
    ) -> Event:
        """
        Emit an event to all subscribers.
        Callbacks are executed synchronously in subscription order.
        Errors in callbacks are caught and logged (never propagated).
        """
        event = Event(
            type=event_type,
            data=data or {},
            source=source,
        )

        # Record in history
        with self._lock:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]

            # Collect callbacks
            callbacks = list(self._subscribers.get(event_type, []))
            # Also notify ALL subscribers
            if event_type != EventType.ALL:
                callbacks.extend(self._subscribers.get(EventType.ALL, []))

        # Execute outside the lock to avoid deadlocks
        for callback in callbacks:
            try:
                callback(event)
            except Exception as exc:
                logger.error(
                    "EventBus subscriber error [%s -> %s]: %s",
                    event_type.value,
                    getattr(callback, "__name__", "?"),
                    exc,
                    exc_info=True,
                )

        return event

    def recent_events(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 20,
    ) -> list[Event]:
        """Return recent events, optionally filtered by type."""
        with self._lock:
            if event_type is None:
                return list(self._event_history[-limit:])
            return [
                e for e in self._event_history
                if e.type == event_type
            ][-limit:]

    def subscriber_count(self, event_type: Optional[EventType] = None) -> int:
        """Return the number of subscribers."""
        with self._lock:
            if event_type is None:
                return sum(len(v) for v in self._subscribers.values())
            return len(self._subscribers.get(event_type, []))

    def stats(self) -> dict[str, Any]:
        """Return bus statistics."""
        with self._lock:
            type_counts: dict[str, int] = {}
            for event in self._event_history:
                key = event.type.value
                type_counts[key] = type_counts.get(key, 0) + 1

            return {
                "total_events": len(self._event_history),
                "subscriber_count": self.subscriber_count(),
                "event_type_counts": type_counts,
                "subscribed_types": [
                    t.value for t, subs in self._subscribers.items() if subs
                ],
            }

    def clear_history(self) -> None:
        """Clear the event history."""
        with self._lock:
            self._event_history.clear()


# ═══════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETON
# ═══════════════════════════════════════════════════════════════════════

_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Return the global EventBus singleton."""
    global _bus
    if _bus is not None:
        return _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
            logger.info("EventBus initialized")
    return _bus
