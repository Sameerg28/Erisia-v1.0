"""
GoalStore — single access boundary for goal_stack.json persistence.

GoalStack structured records remain authoritative. Legacy flat helpers must
call this facade instead of writing the JSON file directly.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("erisia.goal_store")

# Path-keyed locks so every writer shares the same mutex per file.
_file_locks: dict[str, threading.RLock] = {}
_file_locks_guard = threading.Lock()

# Default store singleton (bound to config path).
_default_store: Optional["GoalStore"] = None
_default_store_lock = threading.Lock()


def _utc_now() -> str:
    import datetime

    return (
        datetime.datetime.now(datetime.UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolved_key(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def file_lock_for(path: str | Path) -> threading.RLock:
    key = _resolved_key(path)
    with _file_locks_guard:
        if key not in _file_locks:
            _file_locks[key] = threading.RLock()
        return _file_locks[key]


def load_goals_raw(path: str | Path) -> list[Any]:
    """
    Compatibility reader for goal_stack.json.

    Preserves mixed formats (dict records and legacy strings).
    Never discards structured fields or supplies invented defaults for strings.
    Missing, empty, malformed, or unsupported input returns an empty list;
    loading never rewrites the file. Legacy goals/items/stack list wrappers
    are accepted; an explicit save writes the canonical top-level list.
    """
    path_obj = Path(path)
    with file_lock_for(path_obj):
        if not path_obj.exists():
            return []
        try:
            with open(path_obj, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to load goals from %s: %s", path_obj, exc)
            return []

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            nested = data.get("goals") or data.get("items") or data.get("stack")
            if isinstance(nested, list):
                return nested
        return []


def save_goals_raw(path: str | Path, goals: list[Any]) -> None:
    """Serialize, sync, and atomically replace the goal list without truncation.

    Failures propagate with the previous destination intact. Only this call's
    unique temporary file is cleaned up; existing sidecars/backups are untouched.
    The path lock coordinates in-process writes, not multi-process transactions.
    """
    path_obj = Path(path)
    with file_lock_for(path_obj):
        # Fail before touching the filesystem if the snapshot cannot serialize.
        payload = json.dumps(list(goals), indent=2, ensure_ascii=False)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            # Same directory keeps replacement on one filesystem. Close before
            # replace for Windows compatibility; do not reuse a shared .tmp name.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path_obj.parent,
                prefix=f".{path_obj.name}.", suffix=".tmp", delete=False,
            ) as f:
                tmp = Path(f.name)
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path_obj)
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not remove goal temporary file %s: %s", tmp, exc)


def goal_title(goal: Any) -> str:
    if isinstance(goal, str):
        return goal.strip()
    if isinstance(goal, dict):
        for key in ("title", "goal", "text", "name"):
            val = goal.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def is_active_goal(goal: Any) -> bool:
    if isinstance(goal, str):
        return bool(goal.strip())
    if isinstance(goal, dict):
        status = str(goal.get("status", "active")).strip().lower()
        return status not in {"completed", "done", "abandoned", "cancelled"}
    return False


class GoalStore:
    """
    Minimal facade over GoalStack persistence.

    Operations mirror what current code actually needs — not a generic CRUD API.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = file_lock_for(self.path)
        self._stack = None  # lazy GoalStack, shared for this path

    def load(self) -> list[Any]:
        return load_goals_raw(self.path)

    def save(self, goals: list[Any]) -> None:
        save_goals_raw(self.path, goals)

    def exists(self) -> bool:
        """Report file presence for callers that distinguish missing from empty."""
        with self._lock:
            return self.path.exists()

    def read_text(self) -> str:
        """Read the raw resource text, preserving the existing MCP contract.

        Missing files yield "[]"; existing text (including empty or malformed
        JSON) is returned unchanged. Read errors propagate to the resource's
        existing error handler. This does not parse, normalize, or save goals.
        """
        with self._lock:
            return self.path.read_text(encoding="utf-8") if self.path.exists() else "[]"

    def get_stack(self):
        """Return the shared GoalStack bound to this store's path."""
        if self._stack is None:
            # Import here to avoid import-time cycles with cognition.
            from erisia.erisia_cognition import GoalStack

            stack = GoalStack(str(self.path))
            self._stack = stack
        return self._stack

    def bind_stack(self, stack) -> None:
        """Attach an already-constructed GoalStack (e.g. from core init)."""
        self._stack = stack

    def list(self, active_only: bool = False) -> list[Any]:
        goals = self.load()
        if not active_only:
            return list(goals)
        return [g for g in goals if is_active_goal(g)]

    def list_titles(self, active_only: bool = True) -> list[str]:
        titles = []
        for goal in self.list(active_only=active_only):
            title = goal_title(goal)
            if title:
                titles.append(title)
        return titles

    def get(self, goal_id_or_title: str) -> Any | None:
        needle = str(goal_id_or_title or "").strip()
        if not needle:
            return None
        needle_lower = needle.lower()
        for goal in self.load():
            if isinstance(goal, dict):
                if str(goal.get("id", "")) == needle:
                    return goal
                if goal_title(goal).lower() == needle_lower:
                    return goal
            elif isinstance(goal, str) and goal.strip().lower() == needle_lower:
                return goal
        return None

    def add(
        self,
        title: str,
        *,
        rationale: str = "",
        next_action: str = "",
        priority: float = 0.55,
        confidence: float = 0.5,
        urgency: float = 0.5,
        source: str = "manual",
    ) -> Any | None:
        """Add/update via GoalStack so structured fields are preserved."""
        stack = self.get_stack()
        return stack.add_or_update_goal(
            title=title,
            rationale=rationale,
            next_action=next_action,
            priority=priority,
            confidence=confidence,
            urgency=urgency,
            source=source,
        )

    def update(self, goal_id_or_title: str, **fields: Any) -> Any | None:
        """Update fields on an existing structured goal; promotes strings to dicts."""
        with self._lock:
            goals = self.load()
            needle = str(goal_id_or_title or "").strip()
            if not needle:
                return None
            needle_lower = needle.lower()
            for idx, goal in enumerate(goals):
                match = False
                if isinstance(goal, dict):
                    match = (
                        str(goal.get("id", "")) == needle
                        or goal_title(goal).lower() == needle_lower
                    )
                elif isinstance(goal, str):
                    match = goal.strip().lower() == needle_lower

                if not match:
                    continue

                if isinstance(goal, str):
                    goal = {
                        "id": str(uuid.uuid4()),
                        "title": goal.strip(),
                        "rationale": "",
                        "next_action": "",
                        "priority": 0.5,
                        "confidence": 0.5,
                        "urgency": 0.5,
                        "progress": 0.0,
                        "status": "active",
                        "source": "system",
                        "created_at": _utc_now(),
                        "updated_at": _utc_now(),
                        "recency": 1.0,
                        "history": [
                            {
                                "ts": _utc_now(),
                                "event": "migrated",
                                "source": "goal_store",
                            }
                        ],
                    }
                    goals[idx] = goal

                for key, value in fields.items():
                    if key == "id":
                        continue
                    goal[key] = value
                goal["updated_at"] = _utc_now()
                goal.setdefault("history", []).append(
                    {
                        "ts": _utc_now(),
                        "event": "updated",
                        "source": "goal_store",
                    }
                )
                self.save(goals)
                if self._stack is not None:
                    self._stack.goals = goals
                return goal
            return None

    def remove(self, goal_id_or_title: str) -> bool:
        with self._lock:
            goals = self.load()
            needle = str(goal_id_or_title or "").strip()
            if not needle:
                return False
            needle_lower = needle.lower()
            kept = []
            removed = False
            for goal in goals:
                if isinstance(goal, dict):
                    if (
                        str(goal.get("id", "")) == needle
                        or goal_title(goal).lower() == needle_lower
                    ):
                        removed = True
                        continue
                elif isinstance(goal, str) and goal.strip().lower() == needle_lower:
                    removed = True
                    continue
                kept.append(goal)
            if removed:
                self.save(kept)
                if self._stack is not None:
                    self._stack.goals = kept
            return removed

    def complete(self, goal_text: str | None = None) -> tuple[bool, str]:
        """
        Mark the target (or first active) goal completed.

        Preserves structured fields — does not flatten or discard the record.
        """
        with self._lock:
            goals = self.load()
            if not goals:
                return False, ""

            target_idx = None
            needle = str(goal_text or "").strip().lower()

            for idx, goal in enumerate(goals):
                if not is_active_goal(goal):
                    continue
                if needle:
                    if goal_title(goal).lower() != needle and not (
                        isinstance(goal, dict) and str(goal.get("id", "")) == goal_text
                    ):
                        continue
                target_idx = idx
                break

            if target_idx is None and not needle:
                # Fallback: first entry even if already terminal (legacy pop semantics)
                target_idx = 0
            if target_idx is None:
                return False, ""

            goal = goals[target_idx]
            title = goal_title(goal) or str(goal)
            now = _utc_now()

            if isinstance(goal, str):
                goals[target_idx] = {
                    "id": str(uuid.uuid4()),
                    "title": goal.strip(),
                    "rationale": "",
                    "next_action": "",
                    "priority": 0.5,
                    "confidence": 0.5,
                    "urgency": 0.5,
                    "progress": 1.0,
                    "status": "completed",
                    "source": "system",
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": now,
                    "recency": 0.0,
                    "history": [
                        {"ts": now, "event": "migrated", "source": "goal_store"},
                        {"ts": now, "event": "completed", "source": "manage_goal_stack"},
                    ],
                }
            elif isinstance(goal, dict):
                goal["status"] = "completed"
                goal["progress"] = 1.0
                goal["updated_at"] = now
                goal["completed_at"] = now
                goal.setdefault("history", []).append(
                    {"ts": now, "event": "completed", "source": "manage_goal_stack"}
                )
            else:
                return False, ""

            self.save(goals)
            if self._stack is not None:
                self._stack.goals = goals
                if hasattr(self._stack, "_remember_completed_goal"):
                    self._stack._remember_completed_goal(title, now)
            return True, title

    def manage(self, action: str, goal_text: str | None = None) -> str:
        """Tool-facing API matching manage_goal_stack(action, goal_text)."""
        act = str(action or "").strip().lower()

        if act == "view":
            titles = self.list_titles(active_only=True)
            if not titles:
                return "[GOAL STACK]: No active goals."
            listing = " | ".join(f"{idx + 1}. {t}" for idx, t in enumerate(titles))
            return f"[GOAL STACK]: {listing}"

        if act == "add":
            cleaned = str(goal_text or "").strip()
            if not cleaned:
                return "[GOAL STACK]: goal_text is required to add."
            result = self.add(cleaned, source="manage_goal_stack")
            if result is None:
                return f"[GOAL STACK]: Blocked or duplicate -> {cleaned}"
            return f"[GOAL STACK]: Added -> {cleaned}"

        if act == "complete":
            ok, title = self.complete(goal_text)
            if not ok:
                return "[GOAL STACK]: No goals to complete."
            return f"[GOAL STACK]: Completed -> {title}"

        return "[GOAL STACK]: Invalid action. Use add, complete, or view."


def get_goal_store(path: str | Path | None = None) -> GoalStore:
    """Return the default GoalStore (config path) or a store for an explicit path."""
    global _default_store
    if path is not None:
        # Explicit path: reuse default if same file, else ephemeral store.
        path_obj = Path(path)
        with _default_store_lock:
            if (
                _default_store is not None
                and _resolved_key(_default_store.path) == _resolved_key(path_obj)
            ):
                return _default_store
        return GoalStore(path_obj)

    with _default_store_lock:
        if _default_store is None:
            from erisia.erisia_config import get_config

            _default_store = GoalStore(get_config().paths.goal_stack_file)
        return _default_store


def bind_goal_store(store: GoalStore) -> GoalStore:
    """Register the runtime-owned GoalStore as the process default."""
    global _default_store
    with _default_store_lock:
        _default_store = store
    return store


def reset_goal_store_for_tests() -> None:
    """Test helper — clear the default singleton."""
    global _default_store
    with _default_store_lock:
        _default_store = None
