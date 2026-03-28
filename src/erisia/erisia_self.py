"""
Erisia Identity Layer — erisia_self.py

Maintains a continuously updated psychological and behavioral
model of the user. Tracks cognitive rhythm, stress signatures,
goal consistency, decision velocity, and value map.

This module is Erisia's most intimate layer. It transforms raw
interaction data into a living portrait of who the user is —
not just what they do, but how they think, when they thrive,
and what they truly care about.

Called by erisia_core.py on every user interaction.
Persists state to SQLite and ChromaDB.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import chromadb
except Exception:  # pragma: no cover - runtime dependency may be optional
    chromadb = None

IDENTITY_DB_TABLE = "user_identity_snapshots"
INTERACTION_LOG_TABLE = "interaction_log"
LOGGER_NAME = "erisia.identity"
SNAPSHOT_VERSION = "1.0"

# Cognitive rhythm
PEAK_HOUR_WINDOW = 3
MIN_INTERACTIONS_FOR_RHYTHM = 10

# Stress detection
STRESS_SHORT_MESSAGE_THRESHOLD = 15
STRESS_LONG_MESSAGE_THRESHOLD = 400
STRESS_RAPID_FIRE_SECONDS = 8

# Goal consistency
GOAL_STALE_DAYS = 7
GOAL_COMPLETE_BONUS = 0.15
GOAL_ABANDONED_PENALTY = 0.10

# Decision velocity
FAST_DECISION_SECONDS = 30
SLOW_DECISION_SECONDS = 300

# Value map
VALUE_DECAY_DAYS = 30
TOP_VALUES_COUNT = 5

USER_ID = "sameer"
IDENTITY_COLLECTION_NAME = "erisia_identity"
SESSION_BREAK_SECONDS = 30 * 60
FOCUS_GAP_SECONDS = 15 * 60
MAX_HISTORY_ANALYSIS_ROWS = 750
MESSAGE_TEXT_COLUMN = "message_text"

VALUE_SIGNALS: dict[str, list[str]] = {
    "achievement": [
        "build",
        "create",
        "launch",
        "finish",
        "complete",
        "success",
        "win",
        "goal",
        "target",
        "accomplish",
    ],
    "autonomy": [
        "my own",
        "independent",
        "freedom",
        "control",
        "sovereign",
        "self",
        "decide",
        "choose",
    ],
    "mastery": [
        "learn",
        "understand",
        "improve",
        "better",
        "expert",
        "skill",
        "knowledge",
        "deep",
    ],
    "impact": [
        "help",
        "change",
        "matter",
        "world",
        "people",
        "difference",
        "mission",
        "purpose",
    ],
    "security": [
        "stable",
        "safe",
        "reliable",
        "protect",
        "consistent",
        "backup",
        "risk",
        "careful",
    ],
}

GOAL_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "trading": (
        "trade",
        "trading",
        "market",
        "portfolio",
        "oracle",
        "backtest",
        "stock",
        "signal",
        "ticker",
    ),
    "development": (
        "code",
        "build",
        "implement",
        "python",
        "script",
        "fix",
        "debug",
        "refactor",
        "tool",
    ),
    "planning": (
        "plan",
        "roadmap",
        "goal",
        "mission",
        "objective",
        "strategy",
        "organize",
    ),
    "research": (
        "analyze",
        "research",
        "investigate",
        "study",
        "compare",
        "evaluate",
    ),
    "personal": (
        "health",
        "sleep",
        "identity",
        "reflect",
        "routine",
        "habit",
    ),
}

DECISION_REVERSAL_MARKERS = (
    "actually",
    "instead",
    "switch",
    "change",
    "reverse",
    "rather",
    "not that",
    "different approach",
    "scratch that",
)


@dataclass(slots=True)
class CognitiveRhythm:
    peak_hours: list[int]
    drift_hours: list[int]
    avg_session_length_minutes: float
    longest_focus_streak_minutes: float
    preferred_work_start: int
    sessions_analyzed: int
    last_updated: str


@dataclass(slots=True)
class StressSignature:
    baseline_message_length: float
    current_message_length: float
    stress_score: float
    stress_indicators: list[str]
    rapid_fire_count: int
    last_stress_event: str | None
    trend: str


@dataclass(slots=True)
class GoalConsistency:
    consistency_score: float
    goals_stated: int
    goals_completed: int
    goals_abandoned: int
    goals_stale: int
    avg_completion_days: float
    most_completed_category: str
    most_abandoned_category: str
    last_updated: str


@dataclass(slots=True)
class DecisionVelocity:
    avg_response_seconds: float
    fast_decisions_pct: float
    slow_decisions_pct: float
    velocity_trend: str
    impulsive_reversal_count: int
    total_decisions_tracked: int
    last_updated: str


@dataclass(slots=True)
class ValueMap:
    core_values: list[str]
    value_scores: dict[str, float]
    stated_vs_revealed_gap: float
    dominant_motivation: str
    last_updated: str


@dataclass(slots=True)
class IdentitySnapshot:
    user_id: str
    snapshot_version: str
    cognitive_rhythm: CognitiveRhythm
    stress_signature: StressSignature
    goal_consistency: GoalConsistency
    decision_velocity: DecisionVelocity
    value_map: ValueMap
    total_interactions: int
    first_seen: str
    last_updated: str


@dataclass(slots=True)
class InteractionEvent:
    timestamp: str
    message_length: int
    session_id: str
    hour_of_day: int
    response_latency_seconds: float
    topic_category: str
    sentiment_polarity: float
    contains_goal: bool
    contains_decision: bool
    contains_frustration: bool


class IdentityLayer:
    """
    Erisia's persistent model of who the user is.
    Continuously updated on every interaction.
    Queried by erisia_core.py to adapt behavior.
    """

    __slots__ = (
        "_db_path",
        "_logger",
        "_snapshot",
        "_session_id",
        "_session_start",
        "_last_interaction_time",
        "_session_interactions",
    )

    def __init__(
        self,
        db_path: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        self._session_id = str(uuid.uuid4())
        self._session_start = datetime.now(UTC)
        self._last_interaction_time: datetime | None = None
        self._session_interactions = 0
        self._ensure_schema()
        self._snapshot = self._load_or_create_snapshot()

    def observe_interaction(
        self,
        message: str,
        topic_category: str = "general",
    ) -> None:
        """
        Process a single user message and update all identity components.
        Call this from erisia_core.py on every user input.
        """
        cleaned_message = str(message or "").strip()
        now = datetime.now(UTC)
        latency = 0.0
        if self._last_interaction_time is not None:
            elapsed = (now - self._last_interaction_time).total_seconds()
            if elapsed > SESSION_BREAK_SECONDS:
                self._session_id = str(uuid.uuid4())
                self._session_start = now
                self._session_interactions = 0
                self._snapshot.stress_signature.rapid_fire_count = 0
            else:
                latency = elapsed

        self._last_interaction_time = now
        self._session_interactions += 1

        event = InteractionEvent(
            timestamp=self._utc_now_iso(now),
            message_length=len(cleaned_message),
            session_id=self._session_id,
            hour_of_day=now.hour,
            response_latency_seconds=latency,
            topic_category=topic_category,
            sentiment_polarity=self._estimate_sentiment(cleaned_message),
            contains_goal=self._contains_goal_signal(cleaned_message),
            contains_decision=self._contains_decision_signal(cleaned_message),
            contains_frustration=self._contains_frustration_signal(cleaned_message),
        )

        self._log_interaction(event, cleaned_message)
        self._persist_interaction_to_chroma(cleaned_message, event)
        self._update_cognitive_rhythm(event)
        self._update_stress_signature(event)
        self._update_decision_velocity(event)
        self._update_value_map(cleaned_message, event)
        self._persist_snapshot(increment_interactions=True)

    def update_goal_consistency(
        self,
        goals_completed: int,
        goals_abandoned: int,
        goals_stale: int,
        goals_stated: int,
    ) -> None:
        """
        Sync goal consistency metrics from the GoalStack.
        Call this from erisia_core.py whenever goal stack changes.
        """
        gc = self._snapshot.goal_consistency
        gc.goals_stated = max(0, int(goals_stated))
        gc.goals_completed = max(0, int(goals_completed))
        gc.goals_abandoned = max(0, int(goals_abandoned))
        gc.goals_stale = max(0, int(goals_stale))

        total = max(gc.goals_stated, 1)
        completion_rate = gc.goals_completed / total
        abandonment_rate = gc.goals_abandoned / total
        stale_rate = gc.goals_stale / total
        completion_bonus = min(gc.goals_completed * GOAL_COMPLETE_BONUS, 0.35)
        abandonment_penalty = min(gc.goals_abandoned * GOAL_ABANDONED_PENALTY, 0.30)

        gc.consistency_score = self._clamp(
            0.45
            + completion_rate * 0.35
            + completion_bonus
            - abandonment_rate * 0.25
            - stale_rate * 0.18
            - abandonment_penalty
        )

        self._refresh_goal_analytics(gc)
        gc.last_updated = self._utc_now_iso()
        self._persist_snapshot()

    def get_snapshot(self) -> IdentitySnapshot:
        """Return the current identity snapshot."""
        return self._snapshot

    def get_stress_level(self) -> float:
        """Return current stress score 0.0-1.0 for adaptive responses."""
        return self._snapshot.stress_signature.stress_score

    def get_peak_hours(self) -> list[int]:
        """Return detected peak cognitive hours for scheduling."""
        return list(self._snapshot.cognitive_rhythm.peak_hours)

    def get_dominant_motivation(self) -> str:
        """Return the user's dominant motivation type."""
        return self._snapshot.value_map.dominant_motivation

    def get_consistency_score(self) -> float:
        """Return goal consistency score 0.0-1.0."""
        return self._snapshot.goal_consistency.consistency_score

    def generate_self_report(self) -> str:
        """
        Generate a plain-English summary of what Erisia knows
        about the user. Called on demand by erisia_core.py.
        """
        s = self._snapshot
        rhythm = s.cognitive_rhythm
        stress = s.stress_signature
        goals = s.goal_consistency
        velocity = s.decision_velocity
        values = s.value_map

        peak_str = (
            f"{rhythm.peak_hours[0]:02d}:00-{rhythm.peak_hours[-1]:02d}:00"
            if rhythm.peak_hours
            else "not yet determined"
        )

        report = (
            f"\n{'═' * 52}\n"
            f"  Erisia Identity Report — Sameer\n"
            f"{'─' * 52}\n"
            f"  Interactions tracked : {s.total_interactions}\n"
            f"  Peak cognitive hours : {peak_str}\n"
            f"  Current stress level : {stress.stress_score:.2f}/1.0"
            f" ({stress.trend})\n"
            f"  Goal consistency     : {goals.consistency_score:.2f}/1.0\n"
            f"  Decision velocity    : {velocity.velocity_trend}\n"
            f"  Dominant motivation  : {values.dominant_motivation}\n"
            f"  Core values          : {', '.join(values.core_values[:3])}\n"
            f"{'─' * 52}\n"
            f"  Stress indicators    : "
            f"{', '.join(stress.stress_indicators) or 'none'}\n"
            f"  Goals completed      : {goals.goals_completed}\n"
            f"  Goals stale          : {goals.goals_stale}\n"
            f"{'═' * 52}\n"
        )
        return report

    def _update_cognitive_rhythm(self, event: InteractionEvent) -> None:
        """Update peak/drift hour detection from interaction timing."""
        rhythm = self._snapshot.cognitive_rhythm
        rows = self._load_interaction_rows(limit=MAX_HISTORY_ANALYSIS_ROWS)

        session_starts: list[int] = []
        session_durations: list[float] = []
        longest_focus = 0.0
        session_groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            session_groups[str(row["session_id"])].append(row)

        for session_rows in session_groups.values():
            ordered = sorted(
                session_rows,
                key=lambda row: self._parse_timestamp(str(row["timestamp"])),
            )
            if not ordered:
                continue
            session_starts.append(int(ordered[0]["hour_of_day"]))
            start_ts = self._parse_timestamp(str(ordered[0]["timestamp"]))
            end_ts = self._parse_timestamp(str(ordered[-1]["timestamp"]))
            duration_minutes = max(0.0, (end_ts - start_ts).total_seconds() / 60.0)
            session_durations.append(duration_minutes)
            longest_focus = max(longest_focus, self._compute_focus_streak_minutes(ordered))

        rhythm.sessions_analyzed = len(session_groups)
        rhythm.avg_session_length_minutes = (
            sum(session_durations) / len(session_durations)
            if session_durations
            else max(0.0, (datetime.now(UTC) - self._session_start).total_seconds() / 60.0)
        )
        rhythm.longest_focus_streak_minutes = longest_focus
        rhythm.preferred_work_start = (
            Counter(session_starts).most_common(1)[0][0] if session_starts else event.hour_of_day
        )

        if len(rows) >= MIN_INTERACTIONS_FOR_RHYTHM:
            hourly_scores: dict[int, list[float]] = defaultdict(list)
            for row in rows:
                quality_signal = self._quality_signal(row)
                hourly_scores[int(row["hour_of_day"])].append(quality_signal)

            averaged_scores = {
                hour: sum(scores) / len(scores)
                for hour, scores in hourly_scores.items()
                if scores
            }
            if averaged_scores:
                peak_center = max(averaged_scores, key=lambda hour: averaged_scores[hour])
                rhythm.peak_hours = list(
                    range(max(0, peak_center - PEAK_HOUR_WINDOW), min(23, peak_center + PEAK_HOUR_WINDOW) + 1)
                )
                drift_candidates = [
                    hour
                    for hour, _score in sorted(averaged_scores.items(), key=lambda item: item[1])
                    if hour not in rhythm.peak_hours
                ]
                rhythm.drift_hours = drift_candidates[:4]

        rhythm.last_updated = event.timestamp

    def _update_stress_signature(self, event: InteractionEvent) -> None:
        """Detect stress from message patterns and timing."""
        stress = self._snapshot.stress_signature
        rows = self._load_interaction_rows(limit=50)
        previous_score = stress.stress_score

        baseline_pool = [int(row["message_length"]) for row in rows[:-1]] or [event.message_length]
        baseline_average = sum(baseline_pool) / len(baseline_pool)
        stress.baseline_message_length = baseline_average
        stress.current_message_length = float(event.message_length)

        rapid_fire_count = 0
        for row in reversed(rows):
            if str(row["session_id"]) != event.session_id:
                break
            latency = float(row["latency_seconds"])
            if 0.0 < latency <= STRESS_RAPID_FIRE_SECONDS:
                rapid_fire_count += 1
                continue
            if latency > STRESS_RAPID_FIRE_SECONDS:
                break
        stress.rapid_fire_count = rapid_fire_count

        indicators: list[str] = []
        deviation = abs(event.message_length - baseline_average) / max(baseline_average, 1.0)
        raw_score = min(0.18, deviation * 0.18)

        if event.message_length < STRESS_SHORT_MESSAGE_THRESHOLD and baseline_average > 30.0:
            indicators.append("terse_message")
            raw_score += 0.22

        if event.message_length > STRESS_LONG_MESSAGE_THRESHOLD:
            indicators.append("verbose_overload")
            raw_score += 0.15

        if stress.rapid_fire_count >= 3:
            indicators.append("rapid_fire_input")
            raw_score += 0.22

        if event.contains_frustration:
            indicators.append("frustration_detected")
            raw_score += 0.25

        if event.message_length > STRESS_LONG_MESSAGE_THRESHOLD and (event.hour_of_day >= 22 or event.hour_of_day <= 4):
            indicators.append("late_night_overload")
            raw_score += 0.12

        if event.sentiment_polarity < -0.5:
            indicators.append("negative_tone")
            raw_score += 0.10

        if not indicators:
            raw_score = max(0.0, raw_score - 0.06)

        stress.stress_score = self._clamp(previous_score * 0.65 + self._clamp(raw_score) * 0.35)
        stress.stress_indicators = indicators

        delta = stress.stress_score - previous_score
        if delta > 0.08:
            stress.trend = "rising"
        elif delta < -0.08:
            stress.trend = "falling"
        else:
            stress.trend = "stable"

        if indicators:
            stress.last_stress_event = event.timestamp

    def _update_decision_velocity(self, event: InteractionEvent) -> None:
        """Track how quickly the user makes decisions."""
        if not event.contains_decision:
            return

        velocity = self._snapshot.decision_velocity
        rows = self._load_interaction_rows(limit=MAX_HISTORY_ANALYSIS_ROWS)
        decision_rows = [row for row in rows if int(row["contains_decision"]) == 1]
        if not decision_rows:
            return

        latencies = [max(0.0, float(row["latency_seconds"])) for row in decision_rows]
        velocity.total_decisions_tracked = len(decision_rows)
        velocity.avg_response_seconds = sum(latencies) / len(latencies)

        fast_count = sum(1 for latency in latencies if latency < FAST_DECISION_SECONDS)
        slow_count = sum(1 for latency in latencies if latency > SLOW_DECISION_SECONDS)
        velocity.fast_decisions_pct = fast_count / len(latencies)
        velocity.slow_decisions_pct = slow_count / len(latencies)
        velocity.impulsive_reversal_count = self._count_impulsive_reversals(decision_rows)

        if len(latencies) >= 6:
            split_index = max(1, len(latencies) // 2)
            prior_avg = sum(latencies[:split_index]) / len(latencies[:split_index])
            recent_avg = sum(latencies[split_index:]) / len(latencies[split_index:])
            if recent_avg <= prior_avg * 0.75:
                velocity.velocity_trend = "accelerating"
            elif recent_avg >= prior_avg * 1.25:
                velocity.velocity_trend = "deliberating"
            else:
                velocity.velocity_trend = "stable"
        elif velocity.fast_decisions_pct >= 0.60:
            velocity.velocity_trend = "accelerating"
        elif velocity.slow_decisions_pct >= 0.45:
            velocity.velocity_trend = "deliberating"
        else:
            velocity.velocity_trend = "stable"

        velocity.last_updated = event.timestamp

    def _update_value_map(
        self,
        message: str,
        event: InteractionEvent,
    ) -> None:
        """Infer core values from message content patterns."""
        del message
        values = self._snapshot.value_map
        rows = self._load_interaction_rows(limit=MAX_HISTORY_ANALYSIS_ROWS)
        now = self._parse_timestamp(event.timestamp)

        since_last_update_days = max(
            0.0,
            (now - self._parse_timestamp(values.last_updated)).total_seconds() / 86400.0,
        )
        snapshot_decay = math.exp(-since_last_update_days / max(1.0, VALUE_DECAY_DAYS))
        aggregate_scores = {
            value: score * snapshot_decay
            for value, score in values.value_scores.items()
        }

        goal_messages = 0
        aligned_goal_messages = 0

        for row in rows:
            row_timestamp = self._parse_timestamp(str(row["timestamp"]))
            age_days = max(0.0, (now - row_timestamp).total_seconds() / 86400.0)
            weight = math.exp(-age_days / max(1.0, VALUE_DECAY_DAYS))
            if int(row["contains_goal"]) == 1:
                weight *= 1.10
                goal_messages += 1

            text = str(row[MESSAGE_TEXT_COLUMN] or "").lower()
            row_total_hits = 0
            for value, keywords in VALUE_SIGNALS.items():
                hits = sum(1 for keyword in keywords if keyword in text)
                row_total_hits += hits
                if hits:
                    aggregate_scores[value] = aggregate_scores.get(value, 0.0) + (hits * 0.08 * weight)

            if int(row["contains_goal"]) == 1 and row_total_hits > 0:
                aligned_goal_messages += 1

        for value in VALUE_SIGNALS:
            aggregate_scores.setdefault(value, 0.0)

        peak_score = max(aggregate_scores.values(), default=0.0)
        if peak_score > 0.0:
            values.value_scores = {
                value: self._clamp(score / peak_score)
                for value, score in aggregate_scores.items()
            }
        else:
            values.value_scores = {value: 0.0 for value in VALUE_SIGNALS}

        ranked_values = sorted(
            values.value_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        values.core_values = [name for name, _score in ranked_values[:TOP_VALUES_COUNT]]
        if not values.core_values:
            values.core_values = list(VALUE_SIGNALS)[:TOP_VALUES_COUNT]

        values.dominant_motivation = values.core_values[0]

        if goal_messages > 0:
            current_gap = 1.0 - (aligned_goal_messages / goal_messages)
            values.stated_vs_revealed_gap = self._clamp(
                values.stated_vs_revealed_gap * 0.60 + current_gap * 0.40
            )

        values.last_updated = event.timestamp

    def _estimate_sentiment(self, message: str) -> float:
        """Lightweight rule-based sentiment estimate."""
        positive_words = {
            "good",
            "great",
            "perfect",
            "yes",
            "excellent",
            "nice",
            "love",
            "amazing",
            "brilliant",
            "correct",
            "right",
            "works",
            "done",
            "success",
            "finally",
            "clean",
        }
        negative_words = {
            "no",
            "wrong",
            "bad",
            "broken",
            "error",
            "fail",
            "problem",
            "issue",
            "frustrated",
            "annoying",
            "stuck",
            "fix",
            "bug",
            "crash",
            "worse",
            "still",
        }
        words = set(re.findall(r"[a-z0-9_']+", message.lower()))
        pos = len(words & positive_words)
        neg = len(words & negative_words)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    def _contains_goal_signal(self, message: str) -> bool:
        """Detect if the message contains a goal statement."""
        goal_phrases = (
            "i want",
            "i need",
            "i will",
            "let's build",
            "we should",
            "goal is",
            "objective",
            "target",
            "i'm going to",
            "plan to",
            "next step",
        )
        lower = message.lower()
        return any(phrase in lower for phrase in goal_phrases)

    def _contains_decision_signal(self, message: str) -> bool:
        """Detect if the message contains a decision."""
        decision_phrases = (
            "let's do",
            "go with",
            "i choose",
            "decided",
            "we'll use",
            "start with",
            "first",
            "yes",
            "okay",
            "proceed",
            "continue",
            "do it",
        )
        lower = message.lower()
        return any(phrase in lower for phrase in decision_phrases)

    def _contains_frustration_signal(self, message: str) -> bool:
        """Detect frustration signals in message content."""
        frustration_phrases = (
            "still not",
            "why is",
            "doesn't work",
            "not working",
            "again",
            "same error",
            "still broken",
            "what the",
            "how many times",
            "come on",
            "seriously",
        )
        lower = message.lower()
        return any(phrase in lower for phrase in frustration_phrases)

    def _ensure_schema(self) -> None:
        """Create SQLite tables if they do not exist."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {IDENTITY_DB_TABLE} (
                        user_id TEXT PRIMARY KEY,
                        snapshot_json TEXT NOT NULL,
                        total_interactions INTEGER NOT NULL DEFAULT 0,
                        first_seen TEXT NOT NULL,
                        last_updated TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {INTERACTION_LOG_TABLE} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        message_length INTEGER NOT NULL,
                        hour_of_day INTEGER NOT NULL,
                        latency_seconds REAL NOT NULL,
                        topic_category TEXT NOT NULL,
                        sentiment REAL NOT NULL,
                        contains_goal INTEGER NOT NULL,
                        contains_decision INTEGER NOT NULL,
                        contains_frustration INTEGER NOT NULL
                    );
                    """
                )
                self._ensure_column(conn, INTERACTION_LOG_TABLE, MESSAGE_TEXT_COLUMN, "TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{INTERACTION_LOG_TABLE}_timestamp "
                    f"ON {INTERACTION_LOG_TABLE}(timestamp);"
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{INTERACTION_LOG_TABLE}_session "
                    f"ON {INTERACTION_LOG_TABLE}(session_id);"
                )
                conn.commit()
        except sqlite3.Error as exc:
            self._logger.error("Failed to ensure identity schema: %s", exc)

    def _load_or_create_snapshot(self) -> IdentitySnapshot:
        """Load existing snapshot from SQLite or create a fresh one."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT snapshot_json FROM {IDENTITY_DB_TABLE} WHERE user_id = ?;",
                    (USER_ID,),
                ).fetchone()
                if row is not None:
                    return self._deserialize_snapshot(str(row["snapshot_json"]))
        except sqlite3.Error as exc:
            self._logger.error("Failed to load identity snapshot: %s", exc)
        return self._create_fresh_snapshot()

    def _create_fresh_snapshot(self) -> IdentitySnapshot:
        """Create a blank identity snapshot for first-time users."""
        now = self._utc_now_iso()
        return IdentitySnapshot(
            user_id=USER_ID,
            snapshot_version=SNAPSHOT_VERSION,
            cognitive_rhythm=CognitiveRhythm(
                peak_hours=[],
                drift_hours=[],
                avg_session_length_minutes=0.0,
                longest_focus_streak_minutes=0.0,
                preferred_work_start=9,
                sessions_analyzed=0,
                last_updated=now,
            ),
            stress_signature=StressSignature(
                baseline_message_length=50.0,
                current_message_length=0.0,
                stress_score=0.0,
                stress_indicators=[],
                rapid_fire_count=0,
                last_stress_event=None,
                trend="stable",
            ),
            goal_consistency=GoalConsistency(
                consistency_score=0.5,
                goals_stated=0,
                goals_completed=0,
                goals_abandoned=0,
                goals_stale=0,
                avg_completion_days=0.0,
                most_completed_category="",
                most_abandoned_category="",
                last_updated=now,
            ),
            decision_velocity=DecisionVelocity(
                avg_response_seconds=0.0,
                fast_decisions_pct=0.0,
                slow_decisions_pct=0.0,
                velocity_trend="stable",
                impulsive_reversal_count=0,
                total_decisions_tracked=0,
                last_updated=now,
            ),
            value_map=ValueMap(
                core_values=["achievement", "mastery", "autonomy", "impact", "security"],
                value_scores={
                    "achievement": 0.5,
                    "mastery": 0.5,
                    "autonomy": 0.5,
                    "impact": 0.5,
                    "security": 0.5,
                },
                stated_vs_revealed_gap=0.0,
                dominant_motivation="achievement",
                last_updated=now,
            ),
            total_interactions=0,
            first_seen=now,
            last_updated=now,
        )

    def _persist_snapshot(self, *, increment_interactions: bool = False) -> None:
        """Save the current snapshot to SQLite and mirror it to ChromaDB."""
        if increment_interactions:
            self._snapshot.total_interactions += 1
        self._snapshot.last_updated = self._utc_now_iso()
        payload = self._serialize_snapshot()
        try:
            with self._connect() as conn:
                conn.execute(
                    f"""
                    INSERT INTO {IDENTITY_DB_TABLE}
                        (user_id, snapshot_json, total_interactions, first_seen, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        snapshot_json = excluded.snapshot_json,
                        total_interactions = excluded.total_interactions,
                        last_updated = excluded.last_updated;
                    """,
                    (
                        self._snapshot.user_id,
                        payload,
                        self._snapshot.total_interactions,
                        self._snapshot.first_seen,
                        self._snapshot.last_updated,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            self._logger.error("Failed to persist identity snapshot: %s", exc)
        self._persist_snapshot_to_chroma(payload)

    def _log_interaction(self, event: InteractionEvent, message: str) -> None:
        """Append an interaction event to the interaction log table."""
        try:
            with self._connect() as conn:
                conn.execute(
                    f"""
                    INSERT INTO {INTERACTION_LOG_TABLE}
                        (timestamp, session_id, message_length, hour_of_day,
                         latency_seconds, topic_category, sentiment,
                         contains_goal, contains_decision, contains_frustration,
                         {MESSAGE_TEXT_COLUMN})
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        event.timestamp,
                        event.session_id,
                        event.message_length,
                        event.hour_of_day,
                        event.response_latency_seconds,
                        event.topic_category,
                        event.sentiment_polarity,
                        int(event.contains_goal),
                        int(event.contains_decision),
                        int(event.contains_frustration),
                        message,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            self._logger.error("Failed to log interaction event: %s", exc)

    def _serialize_snapshot(self) -> str:
        """Serialize the identity snapshot to JSON."""
        return json.dumps(asdict(self._snapshot), ensure_ascii=False, separators=(",", ":"))

    def _deserialize_snapshot(self, json_str: str) -> IdentitySnapshot:
        """Deserialize a stored snapshot back into dataclasses."""
        try:
            data = json.loads(json_str)
            return IdentitySnapshot(
                user_id=str(data["user_id"]),
                snapshot_version=str(data.get("snapshot_version", SNAPSHOT_VERSION)),
                cognitive_rhythm=CognitiveRhythm(**data["cognitive_rhythm"]),
                stress_signature=StressSignature(**data["stress_signature"]),
                goal_consistency=GoalConsistency(**data["goal_consistency"]),
                decision_velocity=DecisionVelocity(**data["decision_velocity"]),
                value_map=ValueMap(**data["value_map"]),
                total_interactions=int(data["total_interactions"]),
                first_seen=str(data["first_seen"]),
                last_updated=str(data["last_updated"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._logger.error(
                "Failed to deserialize snapshot, creating fresh: %s",
                exc,
            )
            return self._create_fresh_snapshot()

    def _refresh_goal_analytics(self, gc: GoalConsistency) -> None:
        goal_records = self._read_goal_stack_records()
        completed_counter: Counter[str] = Counter()
        abandoned_counter: Counter[str] = Counter()
        completion_days: list[float] = []

        for goal in goal_records:
            category = self._infer_goal_category(goal)
            status = str(goal.get("status", "active")).strip().lower()
            if status in {"completed", "done"}:
                completed_counter[category] += 1
                created_at = goal.get("created_at")
                finished_at = goal.get("completed_at") or goal.get("updated_at") or goal.get("last_updated")
                if isinstance(created_at, str) and isinstance(finished_at, str):
                    try:
                        start = self._parse_timestamp(created_at)
                        end = self._parse_timestamp(finished_at)
                        completion_days.append(max(0.0, (end - start).total_seconds() / 86400.0))
                    except ValueError:
                        pass
            elif status in {"abandoned", "cancelled"}:
                abandoned_counter[category] += 1

        gc.avg_completion_days = (
            sum(completion_days) / len(completion_days)
            if completion_days
            else 0.0
        )
        gc.most_completed_category = completed_counter.most_common(1)[0][0] if completed_counter else ""
        gc.most_abandoned_category = abandoned_counter.most_common(1)[0][0] if abandoned_counter else ""

    def _read_goal_stack_records(self) -> list[dict[str, Any]]:
        path = self._resolve_goal_stack_path()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._logger.debug("Unable to parse goal stack for identity analytics: %s", exc)
            return []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _infer_goal_category(self, goal: dict[str, Any]) -> str:
        haystack = " ".join(
            str(goal.get(key, ""))
            for key in ("title", "goal", "text", "name", "next_action", "rationale", "source")
        ).lower()
        for category, keywords in GOAL_CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                return category
        return "general"

    def _load_interaction_rows(self, *, limit: int) -> list[sqlite3.Row]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT timestamp, session_id, message_length, hour_of_day,
                           latency_seconds, topic_category, sentiment,
                           contains_goal, contains_decision, contains_frustration,
                           {MESSAGE_TEXT_COLUMN}
                    FROM {INTERACTION_LOG_TABLE}
                    ORDER BY id DESC
                    LIMIT ?;
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
        except sqlite3.Error as exc:
            self._logger.error("Failed to load interaction history: %s", exc)
            return []
        return list(reversed(rows))

    def _compute_focus_streak_minutes(self, rows: list[sqlite3.Row]) -> float:
        if not rows:
            return 0.0
        longest = 0.0
        streak_start = self._parse_timestamp(str(rows[0]["timestamp"]))
        previous_ts = streak_start
        for row in rows[1:]:
            current_ts = self._parse_timestamp(str(row["timestamp"]))
            if (current_ts - previous_ts).total_seconds() <= FOCUS_GAP_SECONDS:
                previous_ts = current_ts
                longest = max(longest, (previous_ts - streak_start).total_seconds() / 60.0)
                continue
            streak_start = current_ts
            previous_ts = current_ts
        return longest

    def _quality_signal(self, row: sqlite3.Row) -> float:
        message_length = max(0, int(row["message_length"]))
        sentiment = float(row["sentiment"])
        contains_goal = int(row["contains_goal"]) == 1
        contains_decision = int(row["contains_decision"]) == 1
        contains_frustration = int(row["contains_frustration"]) == 1

        length_score = self._clamp(math.log1p(message_length) / 6.5)
        sentiment_score = self._clamp((sentiment + 1.0) / 2.0)
        signal = length_score * 0.55 + sentiment_score * 0.35
        if contains_goal:
            signal += 0.08
        if contains_decision:
            signal += 0.04
        if contains_frustration:
            signal -= 0.12
        return self._clamp(signal)

    def _count_impulsive_reversals(self, decision_rows: list[sqlite3.Row]) -> int:
        reversals = 0
        for index, row in enumerate(decision_rows[:-1]):
            latency = float(row["latency_seconds"])
            if latency >= FAST_DECISION_SECONDS:
                continue
            current_text = str(row[MESSAGE_TEXT_COLUMN] or "")
            for future_row in decision_rows[index + 1 : index + 3]:
                future_text = str(future_row[MESSAGE_TEXT_COLUMN] or "")
                if self._looks_like_reversal(current_text, future_text):
                    reversals += 1
                    break
        return reversals

    def _looks_like_reversal(self, previous_text: str, new_text: str) -> bool:
        previous_lower = previous_text.lower()
        new_lower = new_text.lower()
        if not previous_lower or not new_lower:
            return False

        if any(marker in new_lower for marker in DECISION_REVERSAL_MARKERS):
            return True
        if ("yes" in previous_lower and "no" in new_lower) or ("no" in previous_lower and "yes" in new_lower):
            return True

        previous_tokens = {
            token
            for token in re.findall(r"[a-z0-9_]+", previous_lower)
            if len(token) > 2
        }
        new_tokens = {
            token
            for token in re.findall(r"[a-z0-9_]+", new_lower)
            if len(token) > 2
        }
        overlap = len(previous_tokens & new_tokens)
        return overlap >= 2 and ("instead" in new_lower or "change" in new_lower)

    def _persist_interaction_to_chroma(self, message: str, event: InteractionEvent) -> None:
        collection = self._get_chroma_collection()
        if collection is None:
            return
        try:
            collection.upsert(
                ids=[f"interaction:{event.timestamp}:{uuid.uuid4().hex[:8]}"],
                documents=[message],
                metadatas=[
                    {
                        "doc_type": "interaction",
                        "user_id": USER_ID,
                        "timestamp": event.timestamp,
                        "session_id": event.session_id,
                        "message_length": event.message_length,
                        "hour_of_day": event.hour_of_day,
                        "latency_seconds": float(event.response_latency_seconds),
                        "topic_category": event.topic_category,
                        "sentiment": float(event.sentiment_polarity),
                        "contains_goal": bool(event.contains_goal),
                        "contains_decision": bool(event.contains_decision),
                        "contains_frustration": bool(event.contains_frustration),
                    }
                ],
            )
        except Exception as exc:
            self._logger.debug("Failed to mirror identity interaction to ChromaDB: %s", exc)

    def _persist_snapshot_to_chroma(self, snapshot_json: str) -> None:
        collection = self._get_chroma_collection()
        if collection is None:
            return
        try:
            collection.upsert(
                ids=[f"snapshot:{self._snapshot.user_id}"],
                documents=[snapshot_json],
                metadatas=[
                    {
                        "doc_type": "identity_snapshot",
                        "user_id": self._snapshot.user_id,
                        "snapshot_version": self._snapshot.snapshot_version,
                        "total_interactions": self._snapshot.total_interactions,
                        "last_updated": self._snapshot.last_updated,
                        "stress_score": float(self._snapshot.stress_signature.stress_score),
                        "consistency_score": float(self._snapshot.goal_consistency.consistency_score),
                        "dominant_motivation": self._snapshot.value_map.dominant_motivation,
                    }
                ],
            )
        except Exception as exc:
            self._logger.debug("Failed to mirror identity snapshot to ChromaDB: %s", exc)

    def _get_chroma_collection(self) -> Any | None:
        if chromadb is None:
            return None
        try:
            chroma_path = self._resolve_chroma_path()
            chroma_path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(chroma_path))
            return client.get_or_create_collection(name=IDENTITY_COLLECTION_NAME)
        except Exception as exc:
            self._logger.debug("Unable to initialize identity Chroma collection: %s", exc)
            return None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name});").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl};")

    def _resolve_chroma_path(self) -> Path:
        configured = os.environ.get("ERISIA_MEMORY_DIR")
        if configured:
            return Path(configured).expanduser().resolve()
        return (self._db_path.resolve().parent / "data" / "erisia_memory").resolve()

    def _resolve_goal_stack_path(self) -> Path:
        configured = os.environ.get("ERISIA_GOAL_STACK_FILE")
        if configured:
            return Path(configured).expanduser().resolve()
        return (self._db_path.resolve().parent / "data" / "erisia_goal_stack.json").resolve()

    def _parse_timestamp(self, value: str) -> datetime:
        text = str(value).strip()
        if not text:
            raise ValueError("Empty timestamp")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _utc_now_iso(self, value: datetime | None = None) -> str:
        target = value if value is not None else datetime.now(UTC)
        return target.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _clamp(self, value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, float(value)))
