import datetime
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import Counter, deque


TERMINAL_GOAL_STATUSES = frozenset({
    "done",
    "completed",
    "cancelled",
    "abandoned",
})

AUTO_GOAL_SOURCES = frozenset({
    "passive",
    "passive_cognition",
    "meta_review",
})
BLACKLISTED_GOAL_PATTERNS: frozenset[str] = frozenset({
    "sandbox",
    "validatetest",
    "rerunvalidation",
    "runvalidation",
    "datecommand",
    "smoketest",
    "revalidate",
    "analysesandbox",
    "analyzesandbox",
    "investigatediscrepanc",
    "discrepanc",
    "approveskill",
    "rejectskill",
    "skillforge",
    "approveall",
    "rejectall",
    "comparevalidation",
    "comparevalidationreport",
    "validationreport",
    "approveorreject",
    "comparereport",
    "reportsdiffer",
    "reportsidentical",
})

# --- DAEMON RATE LIMITING ---
_last_daemon_llm_call: float = 0.0
DAEMON_LLM_COOLDOWN_SECONDS: float = 120.0


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _utc_timestamp():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json_parse(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Fallback: extract probable JSON object.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = raw[start : end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


class SalienceEngine:
    """Computes cognitive salience using novelty + urgency + uncertainty + risk."""

    URGENCY_TERMS = {
        "urgent", "asap", "today", "now", "deadline", "blocker", "stuck",
        "crash", "broken", "failure", "critical", "immediately",
    }
    ACTION_TERMS = {
        "build", "implement", "fix", "test", "deploy", "debug", "optimize",
        "research", "design", "create", "refactor", "improve",
    }
    UNCERTAINTY_TERMS = {
        "maybe", "unsure", "unknown", "unclear", "doubt", "risk", "assume",
        "possibly", "uncertain", "guess",
    }
    REFLECTION_TERMS = {
        "learned", "realized", "insight", "pattern", "mistake", "improvement",
        "reflection", "intuition", "hypothesis",
    }

    def __init__(self, history_size=512):
        self._fingerprint_history = deque(maxlen=history_size)
        self._lock = threading.Lock()

    def _tokenize(self, text):
        return [tok for tok in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(tok) > 2]

    def _fingerprint(self, text):
        tokens = self._tokenize(text)
        if not tokens:
            return set()
        shingles = []
        if len(tokens) < 3:
            shingles = [" ".join(tokens)]
        else:
            for i in range(len(tokens) - 2):
                shingles.append(" ".join(tokens[i : i + 3]))
        return {hashlib.sha1(s.encode("utf-8")).hexdigest()[:12] for s in shingles}

    def _novelty(self, text):
        fp = self._fingerprint(text)
        if not fp:
            return 0.0

        with self._lock:
            if not self._fingerprint_history:
                self._fingerprint_history.append(fp)
                return 1.0

            max_overlap = 0.0
            for old in self._fingerprint_history:
                inter = len(fp & old)
                union = len(fp | old)
                if union == 0:
                    continue
                max_overlap = max(max_overlap, inter / union)

            self._fingerprint_history.append(fp)
        return _clamp(1.0 - max_overlap)

    def score(self, text, event_type="generic", metadata=None):
        metadata = metadata or {}
        tokens = self._tokenize(text)
        token_set = set(tokens)
        token_count = max(1, len(tokens))

        urgency = len(token_set & self.URGENCY_TERMS) / 4.0
        actionability = len(token_set & self.ACTION_TERMS) / 5.0
        uncertainty = len(token_set & self.UNCERTAINTY_TERMS) / 4.0
        reflective_depth = len(token_set & self.REFLECTION_TERMS) / 4.0
        novelty = self._novelty(text)
        complexity = _clamp(math.log(token_count + 1, 2) / 7.0)

        source_weight = {
            "mission_result": 0.92,
            "mission_failure": 0.96,
            "meta_review": 0.88,
            "conversation_turn": 0.70,
            "tool_execution": 0.62,
            "passive_reflection": 0.80,
        }.get(event_type, 0.55)

        importance_hint = _clamp(float(metadata.get("importance", 0.5)))
        risk_hint = _clamp(float(metadata.get("risk_hint", 0.0)))

        score = (
            novelty * 0.24
            + urgency * 0.17
            + actionability * 0.16
            + uncertainty * 0.10
            + reflective_depth * 0.12
            + complexity * 0.08
            + source_weight * 0.07
            + importance_hint * 0.04
            + risk_hint * 0.02
        )
        score = _clamp(score)

        if score >= 0.84:
            route = "consciousness"
        elif score >= 0.62:
            route = "episodic_memory"
        elif score >= 0.45:
            route = "goal_stack"
        else:
            route = "discard"

        return {
            "score": score,
            "route": route,
            "factors": {
                "novelty": round(novelty, 3),
                "urgency": round(_clamp(urgency), 3),
                "actionability": round(_clamp(actionability), 3),
                "uncertainty": round(_clamp(uncertainty), 3),
                "reflective_depth": round(_clamp(reflective_depth), 3),
                "complexity": round(complexity, 3),
                "source_weight": round(source_weight, 3),
                "importance_hint": round(importance_hint, 3),
                "risk_hint": round(risk_hint, 3),
            },
        }


class GoalStack:
    """Persistent weighted goal stack with dedupe and priority decay."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self._logger = logging.getLogger("erisia.cognition")
        self.goals = []
        self._recently_completed = {}
        self._load()
        self._refresh_recently_completed()

    def purge_corrupted_goals(self) -> int:
        """
        Remove all goals matching blacklisted patterns.
        Returns count of goals removed.
        Called on startup to clean legacy corruption.
        """
        import re
        before = len(self.goals)
        clean_goals = []
        for goal in self.goals:
            title = ""
            if isinstance(goal, str):
                title = goal
            elif isinstance(goal, dict):
                title = str(
                    goal.get("title") or 
                    goal.get("goal") or 
                    goal.get("text") or ""
                )
            normalized = re.sub(r'[^a-z0-9]', '', 
                                 title.lower())
            is_blacklisted = any(
                pattern in normalized
                for pattern in BLACKLISTED_GOAL_PATTERNS
            )
            if not is_blacklisted:
                clean_goals.append(goal)
        
        removed = before - len(clean_goals)
        if removed > 0:
            self.goals = clean_goals
            self._save()
            self._logger.info(
                "GoalStack: purged %d corrupted goals",
                removed,
            )
        return removed

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.goals = data
            self._refresh_recently_completed()
        except Exception as e:
            print(f"[GOAL STACK WARNING]: Failed to load goal stack. Error: {e}")
            self.goals = []
            self._recently_completed = {}

    def _save(self):
        try:
            self._refresh_recently_completed()
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.goals, f, indent=2)
        except Exception as e:
            print(f"[GOAL STACK WARNING]: Failed to save goal stack. Error: {e}")

    def _normalize(self, text):
        return [tok for tok in re.findall(r"[a-zA-Z0-9_]+", (text or "").lower()) if len(tok) > 2]

    def _normalize_compact(self, text):
        return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

    def _parse_utc_timestamp(self, value):
        if not value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=datetime.UTC)
        return parsed.astimezone(datetime.UTC)

    def _remember_completed_goal(self, goal_title, completed_ts=None):
        normalized = self._normalize_compact(goal_title)
        if not normalized:
            return

        candidate_ts = completed_ts or _utc_timestamp()
        existing_ts = self._recently_completed.get(normalized)
        existing_dt = self._parse_utc_timestamp(existing_ts)
        candidate_dt = self._parse_utc_timestamp(candidate_ts)

        if candidate_dt is not None:
            age_hours = (
                datetime.datetime.now(datetime.UTC) - candidate_dt
            ).total_seconds() / 3600.0
            if age_hours >= 24.0:
                return

        if existing_dt is not None and candidate_dt is not None and existing_dt > candidate_dt:
            return
        self._recently_completed[normalized] = candidate_ts

    def _prune_recently_completed(self):
        now = datetime.datetime.now(datetime.UTC)
        stale_keys = []
        for normalized, completed_time in self._recently_completed.items():
            completed_dt = self._parse_utc_timestamp(completed_time)
            if completed_dt is None:
                stale_keys.append(normalized)
                continue
            age_hours = (now - completed_dt).total_seconds() / 3600.0
            if age_hours >= 24.0:
                stale_keys.append(normalized)
        for normalized in stale_keys:
            self._recently_completed.pop(normalized, None)

    def _refresh_recently_completed(self):
        self._prune_recently_completed()
        for goal in self.goals:
            if not isinstance(goal, dict):
                continue
            status = str(goal.get("status", "active")).strip().lower()
            if status not in TERMINAL_GOAL_STATUSES:
                continue
            goal_title = goal.get("title") or goal.get("goal") or goal.get("text") or goal.get("name") or ""
            completed_ts = (
                goal.get("completed_at")
                or goal.get("updated_at")
                or goal.get("created_at")
                or _utc_timestamp()
            )
            self._remember_completed_goal(goal_title, completed_ts)

    def _is_recent_completion_duplicate(self, goal_title):
        normalized_new = self._normalize_compact(goal_title)
        if not normalized_new:
            return False

        self._prune_recently_completed()
        for completed_key, completed_time in self._recently_completed.items():
            completed_dt = self._parse_utc_timestamp(completed_time)
            if completed_dt is None:
                continue
            age_hours = (
                datetime.datetime.now(datetime.UTC) - completed_dt
            ).total_seconds() / 3600.0
            if age_hours >= 24.0:
                continue

            prefix_len = max(len(normalized_new[:12]), 1)
            similarity = len(
                set(normalized_new[:12]) & set(completed_key[:12])
            ) / prefix_len
            if similarity > 0.7:
                self._logger.info(
                    "GoalStack: blocked duplicate goal '%s' (similar to recently completed '%s')",
                    goal_title,
                    completed_key,
                )
                return True
        return False

    def _is_blacklisted_auto_goal(self, goal_title):
        normalized_goal = self._normalize_compact(goal_title)
        return any(pattern in normalized_goal for pattern in BLACKLISTED_GOAL_PATTERNS)

    def _similarity(self, a, b):
        ta = set(self._normalize(a))
        tb = set(self._normalize(b))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    def _target_goal(self, title):
        best = None
        best_score = 0.0
        for g in self.goals:
            if isinstance(g, dict):
                status = str(g.get("status", "active")).strip().lower()
                if status in TERMINAL_GOAL_STATUSES:
                    continue
            target_title = g.get("title", "") if isinstance(g, dict) else g
            score = self._similarity(title, target_title)
            if score > best_score:
                best = g
                best_score = score
        if best_score >= 0.72:
            return best
        return None

    def _composite_score(self, goal):
        if isinstance(goal, str):
            return 0.50  # Default baseline priority for string goals
        status = str(goal.get("status", "active")).strip().lower()
        if status in TERMINAL_GOAL_STATUSES:
            return -1.0

        p = _clamp(float(goal.get("priority", 0.5)))
        c = _clamp(float(goal.get("confidence", 0.5)))
        progress = _clamp(float(goal.get("progress", 0.0)))
        uncertainty = 1.0 - c
        urgency = _clamp(float(goal.get("urgency", 0.5)))
        recency = _clamp(float(goal.get("recency", 0.5)))
        return p * 0.35 + uncertainty * 0.22 + urgency * 0.23 + recency * 0.10 + (1.0 - progress) * 0.10

    def add_or_update_goal(
        self,
        title,
        rationale="",
        next_action="",
        priority=0.55,
        confidence=0.5,
        urgency=0.5,
        source="unknown",
        deadline=None,
    ):
        title = (title or "").strip()
        if not title:
            return None

        with self.lock:
            self._refresh_recently_completed()

            if source in AUTO_GOAL_SOURCES and self._is_blacklisted_auto_goal(title):
                self._logger.info(
                    "GoalStack: blacklisted auto-goal blocked: '%s'",
                    title,
                )
                return None

            now = _utc_timestamp()
            existing = self._target_goal(title)
            
            # --- 1. Create a brand new goal if it doesn't exist ---
            if existing is None:
                if self._is_recent_completion_duplicate(title):
                    return None
                goal = {
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "rationale": rationale or "",
                    "next_action": next_action or "",
                    "priority": _clamp(float(priority)),
                    "confidence": _clamp(float(confidence)),
                    "urgency": _clamp(float(urgency)),
                    "progress": 0.0,
                    "status": "active",
                    "source": source,
                    "deadline": deadline,
                    "created_at": now,
                    "updated_at": now,
                    "recency": 1.0,
                    "history": [{"ts": now, "event": "created", "source": source}],
                }
                self.goals.append(goal)
                self._save()
                return goal

            # --- 2. THE NUCLEAR FIX: If existing is a string, overwrite it completely ---
            if isinstance(existing, str):
                try:
                    idx = self.goals.index(existing)
                    new_dict_goal = {
                        "id": str(uuid.uuid4()),
                        "title": existing,
                        "rationale": "",
                        "next_action": "",
                        "priority": 0.5,
                        "confidence": 0.5,
                        "urgency": 0.5,
                        "progress": 0.0,
                        "status": "active",
                        "source": source,
                        "created_at": now,
                        "updated_at": now,
                        "recency": 1.0,
                        "history": [{"ts": now, "event": "migrated", "source": "system"}]
                    }
                    self.goals[idx] = new_dict_goal
                    existing = self.goals[idx] 
                except ValueError:
                    return None 

            # --- 3. FINAL TYPE GUARD: If it's still not a dict, kill it with fire ---
            if not isinstance(existing, dict):
                return None

            # --- 4. Safely update the guaranteed dictionary ---
            existing["rationale"] = rationale or existing.get("rationale", "")
            if next_action:
                existing["next_action"] = next_action
            existing["priority"] = _clamp(max(float(existing.get("priority", 0.5)), float(priority)))
            existing["confidence"] = _clamp((float(existing.get("confidence", 0.5)) * 0.65) + (float(confidence) * 0.35))
            existing["urgency"] = _clamp(max(float(existing.get("urgency", 0.5)), float(urgency)))
            existing["recency"] = 1.0
            existing["updated_at"] = now
            if deadline:
                existing["deadline"] = deadline
            existing.setdefault("history", []).append({"ts": now, "event": "updated", "source": source})
            self._save()
            return existing

    def push(self, goal_text, priority=0.6, confidence=0.5, urgency=0.5, source="manual"):
        """Simple wrapper to add a goal to the stack."""
        return self.add_or_update_goal(
            title=goal_text,
            priority=priority,
            confidence=confidence,
            urgency=urgency,
            source=source
        )

    def decay(self):
        with self.lock:
            for goal in self.goals:
                if isinstance(goal, dict):
                    goal["recency"] = _clamp(float(goal.get("recency", 0.5)) * 0.95)
            self._save()

    def focus_snapshot(self, limit=5):
        with self.lock:
            ranked = sorted(self.goals, key=self._composite_score, reverse=True)
            active = [
                g for g in ranked
                if (
                    str(g.get("status", "active")).strip().lower() == "active"
                    if isinstance(g, dict) else True
                )
            ]
            return active[:limit]

    def summary_text(self, limit=5):
        focus = self.focus_snapshot(limit=limit)
        if not focus:
            return "No active goals."
        lines = []
        for idx, g in enumerate(focus, start=1):
            score = self._composite_score(g)
            if isinstance(g, str):
                line = f"{idx}. {g} | next: n/a | score={score:.2f} | conf=0.50"
            else:
                line = f"{idx}. {g.get('title')} | next: {g.get('next_action','n/a')} | score={score:.2f} | conf={float(g.get('confidence',0.5)):.2f}"
            lines.append(line)
        return "\n".join(lines)

    def ingest_goal_candidates(self, candidates, source="passive"):
        if not isinstance(candidates, list):
            return []
        accepted = []
        for c in candidates:
            if not isinstance(c, dict):
                continue
            goal_title = str(c.get("title", "")).strip()
            if source in AUTO_GOAL_SOURCES and self._is_blacklisted_auto_goal(goal_title):
                self._logger.info(
                    "GoalStack: blacklisted auto-goal blocked: '%s'",
                    goal_title,
                )
                continue
            try:
                parsed_priority = float(c.get("priority", 0.55))
            except (ValueError, TypeError):
                parsed_priority = 0.55
            goal = self.add_or_update_goal(
                title=goal_title,
                rationale=c.get("rationale", ""),
                next_action=c.get("next_action", ""),
                priority=parsed_priority,
                confidence=float(c.get("confidence", 0.5)),
                urgency=float(c.get("urgency", parsed_priority)),
                deadline=c.get("deadline"),
                source=source,
            )
            if goal:
                accepted.append(goal)
        return accepted


class JournalEngine:
    """Structured passive cognition journal writer."""

    def __init__(self, directory):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)
        self._lock = threading.Lock()

    def append_entry(self, payload):
        payload = payload or {}
        ts = payload.get("timestamp", _utc_timestamp())
        day = ts[:10]
        path = os.path.join(self.directory, f"Cognition_{day}.md")

        reflection = payload.get("reflection", "").strip()
        if not reflection:
            reflection = "No reflection text provided."

        goals = payload.get("goals", [])
        uncertainties = payload.get("uncertainties", [])
        salience = payload.get("salience", {})
        trigger = payload.get("trigger", "unknown")

        lines = [
            f"\n## Passive Reflection @ {ts}",
            f"Trigger: {trigger}",
            "",
            "### Reflection",
            reflection,
            "",
            "### Salience",
            f"- avg_score: {salience.get('avg_score', 0):.3f}",
            f"- max_score: {salience.get('max_score', 0):.3f}",
            f"- routed_to_consciousness: {salience.get('consciousness_count', 0)}",
            f"- routed_to_episodic_memory: {salience.get('episodic_count', 0)}",
            "",
            "### Goal Candidates",
        ]
        if goals:
            for g in goals:
                lines.append(f"- {g.get('title', 'untitled')} | next: {g.get('next_action', 'n/a')}")
        else:
            lines.append("- none")

        lines.extend(["", "### Uncertainties"])
        if uncertainties:
            for u in uncertainties:
                lines.append(f"- {u}")
        else:
            lines.append("- none")

        with self._lock:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("# Erisia Passive Cognition Journal\n")
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")


class PassiveCognitionEngine:
    """Asynchronous background cognition processor with LLM + heuristics."""

    def __init__(
        self,
        llm_client,
        collection,
        graph_memory,
        goal_stack,
        journal_engine,
        consciousness_callback=None,
        memory_lock=None,
        interval_seconds=90,
        idle_cycles_for_reflection=4,
    ):
        self.llm_client = llm_client
        self.collection = collection
        self.graph_memory = graph_memory
        self.goal_stack = goal_stack
        self.journal_engine = journal_engine
        self._logger = logging.getLogger("erisia.cognition")
        self.consciousness_callback = consciousness_callback
        self.memory_lock = memory_lock or threading.RLock()
        self.interval_seconds = max(20, int(interval_seconds))
        self.idle_cycles_for_reflection = max(2, int(idle_cycles_for_reflection))

        self.salience_engine = SalienceEngine(history_size=1024)
        self.event_queue = deque(maxlen=400)
        self.queue_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.idle_cycles = 0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def publish_event(self, event_type, content, metadata=None):
        item = {
            "ts": _utc_timestamp(),
            "type": event_type,
            "content": str(content or "").strip(),
            "metadata": metadata or {},
        }
        if not item["content"]:
            return
        with self.queue_lock:
            self.event_queue.append(item)

    def ingest_backtest_memory(
        self,
        ticker: str,
        mode: str,
        win_rate: float,
        sharpe_ratio: float,
        max_drawdown: float,
        total_trades: int,
        profit_factor: float,
        best_regime: str,
        worst_regime: str,
        memory_note: str,
        run_timestamp: str,
    ) -> None:
        """Consolidate a completed backtest result into long-term memory."""
        episode_text = (
            f"Oracle Backtest Experience — {ticker} ({mode}) "
            f"on {run_timestamp}:\n"
            f"Win Rate: {win_rate * 100:.2f}% | "
            f"Sharpe: {sharpe_ratio:.3f} | "
            f"Max Drawdown: {max_drawdown * 100:.2f}% | "
            f"Total Trades: {total_trades} | "
            f"Profit Factor: {profit_factor:.3f}\n"
            f"Best Regime: {best_regime} | "
            f"Worst Regime: {worst_regime}\n"
            f"Memory: {memory_note}"
        )

        metadata: dict[str, str | float | int] = {
            "type": "oracle_backtest",
            "ticker": ticker,
            "mode": mode,
            "win_rate": round(win_rate, 4),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "max_drawdown": round(max_drawdown, 4),
            "total_trades": total_trades,
            "profit_factor": round(profit_factor, 4),
            "best_regime": best_regime,
            "worst_regime": worst_regime,
            "run_timestamp": run_timestamp,
        }

        graph_facts: list[str] = [
            f"Oracle traded {ticker} using {mode} strategy",
            f"Oracle achieved {win_rate * 100:.2f}% win rate on {ticker}",
            f"Oracle Sharpe ratio on {ticker} was {sharpe_ratio:.3f}",
            f"Oracle performs best on {ticker} in {best_regime} regimes",
            f"Oracle performs worst on {ticker} in {worst_regime} regimes",
            f"Oracle profit factor on {ticker} was {profit_factor:.3f}",
        ]
        graph_relations: list[tuple[str, str, str]] = [
            ("Oracle", "traded", f"{ticker} using {mode} strategy"),
            ("Oracle", "achieved win rate on", f"{ticker}: {win_rate * 100:.2f}%"),
            ("Oracle", "sharpe ratio on", f"{ticker}: {sharpe_ratio:.3f}"),
            ("Oracle", "performs best on", f"{ticker} in {best_regime} regimes"),
            ("Oracle", "performs worst on", f"{ticker} in {worst_regime} regimes"),
            ("Oracle", "profit factor on", f"{ticker}: {profit_factor:.3f}"),
        ]

        if profit_factor > 1.5:
            salience = 0.85
        elif profit_factor > 1.0:
            salience = 0.65
        elif profit_factor > 0.0:
            salience = 0.45
        else:
            salience = 0.30

        try:
            with self.memory_lock:
                self.collection.add(
                    documents=[episode_text],
                    metadatas=[metadata],
                    ids=[str(uuid.uuid4())],
                )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            self._logger.error(
                "Failed to store backtest memory for %s: %s", ticker, exc
            )

        for entity1, relation, entity2 in graph_relations:
            try:
                self.graph_memory.add_memory_relation(entity1, relation, entity2)
            except (AttributeError, RuntimeError, TypeError, ValueError, OSError) as exc:
                self._logger.error(
                    "Failed to store graph fact for %s: %s", ticker, exc
                )

        self._logger.info(
            "Hippocampus: consolidated backtest memory for %s "
            "(salience=%.2f, facts=%d)",
            ticker, salience, len(graph_facts),
        )

    def record_oracle_session(
        self,
        ticker: str,
        win_rate: float,
        sharpe_ratio: float,
        profit_factor: float,
        memory_note: str,
    ) -> None:
        """Write an Oracle session entry to today's cognition journal."""
        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        journal_path = os.path.join(
            self.journal_engine.directory, f"Cognition_{today}.md"
        )

        entry = (
            f"\n## Oracle Backtest — {ticker} "
            f"[{datetime.now(UTC).strftime('%H:%M UTC')}]\n"
            f"- Win Rate: {win_rate * 100:.2f}%\n"
            f"- Sharpe: {sharpe_ratio:.3f}\n"
            f"- Profit Factor: {profit_factor:.3f}\n"
            f"- Assessment: {memory_note}\n"
        )

        try:
            with open(journal_path, "a", encoding="utf-8") as file_handle:
                file_handle.write(entry)
            self._logger.info(
                "Oracle session recorded in journal: %s", journal_path
            )
        except OSError as exc:
            self._logger.error(
                "Failed to write Oracle session to journal: %s", exc
            )

    def _dequeue_batch(self, max_batch=14):
        batch = []
        with self.queue_lock:
            while self.event_queue and len(batch) < max_batch:
                batch.append(self.event_queue.popleft())
        return batch

    def _loop(self):
        print("[Passive Cognition Engine: online]")
        while not self.stop_event.is_set():
            batch = self._dequeue_batch()
            if batch:
                self.idle_cycles = 0
                self._process_batch(batch)
            else:
                self.idle_cycles += 1
                if self.idle_cycles >= self.idle_cycles_for_reflection:
                    self.idle_cycles = 0
                    self._process_batch(
                        [
                            {
                                "ts": _utc_timestamp(),
                                "type": "passive_reflection",
                                "content": "No fresh events. Perform strategic goal re-evaluation and uncertainty scan.",
                                "metadata": {"importance": 0.65},
                            }
                        ]
                    )
            time.sleep(self.interval_seconds)

    def _collection_add(self, doc):
        if not doc:
            return
        try:
            with self.memory_lock:
                self.collection.add(documents=[doc], ids=[str(uuid.uuid4())])
        except Exception as e:
            print(f"[PASSIVE MEMORY WARNING]: Failed to write episodic memory. Error: {e}")

    def _generate_reflection(self, batch, goal_summary):
        global _last_daemon_llm_call
        now = time.time()
        if now - _last_daemon_llm_call < DAEMON_LLM_COOLDOWN_SECONDS:
            self._logger.debug("Daemon LLM call skipped due to cooldown.")
            return None  # Skip this cycle
        
        event_text = "\n".join(
            [f"{idx+1}. ({e['type']}) {e['content']}" for idx, e in enumerate(batch)]
        )
        prompt = f"""
You are Erisia's advanced passive cognition layer.
Analyze the events and output STRICT JSON with this exact schema:
{{
  "reflection": "string",
  "memory_candidates": [{{"text":"string","importance":0.0}}],
  "candidate_goals": [{{"title":"string","rationale":"string","next_action":"string","priority":0.0,"confidence":0.0,"urgency":0.0,"deadline":null}}],
  "relational_facts": [{{"entity1":"string","relation":"string","entity2":"string"}}],
  "uncertainties": ["string"]
}}

Rules:
- importance, priority, confidence, urgency must be numeric in [0,1].
- Keep candidate_goals concise and technically actionable.
- No markdown, no explanations, only JSON.

ACTIVE GOALS:
{goal_summary}

EVENT BATCH:
{event_text}
"""
        try:
            raw = self.llm_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=700,
            ).choices[0].message.content
            _last_daemon_llm_call = now # Update timestamp after successful call
            parsed = _safe_json_parse(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception as e:
            print(f"[PASSIVE COGNITION WARNING]: LLM reflection failed. Error: {e}")
        return None

    def _heuristic_reflection(self, batch):
        merged = " ".join([b.get("content", "") for b in batch]).strip()
        toks = [t for t in re.findall(r"[a-zA-Z0-9_]+", merged.lower()) if len(t) > 2]
        common = [w for w, _ in Counter(toks).most_common(6)]
        return {
            "reflection": f"Heuristic reflection: dominant topics were {', '.join(common) if common else 'none'}.",
            "memory_candidates": [{"text": merged[:1000], "importance": 0.55}] if merged else [],
            "candidate_goals": [],
            "relational_facts": [],
            "uncertainties": [],
        }

    def _process_batch(self, batch):
        goal_summary = self.goal_stack.summary_text(limit=6)
        reflection = self._generate_reflection(batch, goal_summary) or self._heuristic_reflection(batch)

        memories = reflection.get("memory_candidates", [])
        goals = reflection.get("candidate_goals", [])
        facts = reflection.get("relational_facts", [])
        uncertainties = reflection.get("uncertainties", [])
        reflection_text = reflection.get("reflection", "").strip()

        self.goal_stack.decay()
        accepted_goals = self.goal_stack.ingest_goal_candidates(goals, source="passive_cognition")

        consciousness_count = 0
        episodic_count = 0
        scores = []

        for m in memories:
            if isinstance(m, dict):
                text = (m.get("text") or "").strip()
                importance = _clamp(float(m.get("importance", 0.5)))
            else:
                text = str(m).strip()
                importance = 0.5
            if not text:
                continue

            base_type = batch[0].get("type", "generic")
            salience = self.salience_engine.score(text=text, event_type=base_type, metadata={"importance": importance})
            scores.append(salience["score"])
            route = salience["route"]

            if route == "consciousness" and self.consciousness_callback:
                consciousness_count += 1
                try:
                    self.consciousness_callback(
                        f"[Passive Reflection | score={salience['score']:.3f}]\n{text}"
                    )
                except Exception as e:
                    print(f"[PASSIVE COGNITION WARNING]: consciousness callback failed. Error: {e}")
            elif route in {"episodic_memory", "goal_stack"}:
                episodic_count += 1
                self._collection_add(
                    f"[Passive Memory | route={route} | score={salience['score']:.3f}] {text}"
                )

        for fact in facts:
            if not isinstance(fact, dict):
                continue
            e1 = (fact.get("entity1") or "").strip()
            rel = (fact.get("relation") or "").strip()
            e2 = (fact.get("entity2") or "").strip()
            if not (e1 and rel and e2):
                continue
            try:
                self.graph_memory.add_memory_relation(e1, rel, e2)
            except Exception as e:
                print(f"[PASSIVE COGNITION WARNING]: graph update failed. Error: {e}")

        avg_score = (sum(scores) / len(scores)) if scores else 0.0
        max_score = max(scores) if scores else 0.0
        self.journal_engine.append_entry(
            {
                "timestamp": _utc_timestamp(),
                "trigger": batch[0].get("type", "unknown"),
                "reflection": reflection_text or "No reflection text generated.",
                "goals": accepted_goals,
                "uncertainties": uncertainties if isinstance(uncertainties, list) else [],
                "salience": {
                    "avg_score": avg_score,
                    "max_score": max_score,
                    "consciousness_count": consciousness_count,
                    "episodic_count": episodic_count,
                },
            }
        )
