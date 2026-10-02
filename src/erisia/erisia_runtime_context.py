"""
ToolRuntimeContext — minimal dependency boundary for the tool router.

Breaks the hard core ↔ router import cycle by injecting the callables/objects
the router needs, instead of importing erisia_core inside dispatch.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

_lock = threading.RLock()
_context: Optional["ToolRuntimeContext"] = None


@dataclass
class ToolRuntimeContext:
    """Explicit runtime deps for _execute_tool_call. No cognition redesign."""

    daemon_system: Any = None
    get_world_state: Callable[[], Any] | None = None
    analyze_screen: Callable[..., Any] | None = None
    reason_about_event: Callable[..., Any] | None = None
    update_consciousness: Callable[..., Any] | None = None
    save_heuristic_rule: Callable[..., Any] | None = None
    forge_new_skill: Callable[..., Any] | None = None
    forge_pending_skill: Callable[..., Any] | None = None
    approve_skill: Callable[..., Any] | None = None
    reject_skill: Callable[..., Any] | None = None
    graph_memory: Any = None
    custom_skill_functions: dict = field(default_factory=dict)
    speak_text: Callable[..., Any] | None = None
    mirofish_call: Callable[..., Any] | None = None
    manage_goal_stack: Callable[..., Any] | None = None


def bind_tool_runtime_context(ctx: ToolRuntimeContext) -> ToolRuntimeContext:
    global _context
    with _lock:
        _context = ctx
    return ctx


def get_tool_runtime_context() -> ToolRuntimeContext | None:
    with _lock:
        return _context


def clear_tool_runtime_context() -> None:
    global _context
    with _lock:
        _context = None


def resolve_tool_runtime_context() -> ToolRuntimeContext:
    """
    Return bound context, or lazily build one from erisia_core as a
    transitional fallback (keeps older call paths working).
    """
    ctx = get_tool_runtime_context()
    if ctx is not None:
        return ctx

    # Transitional fallback — prefer bind_tool_runtime_context from core init.
    from erisia.erisia_core import (
        daemon_system,
        get_world_state,
        analyze_screen,
        reason_about_event,
        update_consciousness,
        save_heuristic_rule,
        forge_new_skill,
        forge_pending_skill,
        approve_skill,
        reject_skill,
        graph_memory,
        custom_skill_functions,
        speak_text,
        mirofish_call,
        manage_goal_stack,
    )

    ctx = ToolRuntimeContext(
        daemon_system=daemon_system,
        get_world_state=get_world_state,
        analyze_screen=analyze_screen,
        reason_about_event=reason_about_event,
        update_consciousness=update_consciousness,
        save_heuristic_rule=save_heuristic_rule,
        forge_new_skill=forge_new_skill,
        forge_pending_skill=forge_pending_skill,
        approve_skill=approve_skill,
        reject_skill=reject_skill,
        graph_memory=graph_memory,
        custom_skill_functions=custom_skill_functions,
        speak_text=speak_text,
        mirofish_call=mirofish_call,
        manage_goal_stack=manage_goal_stack,
    )
    return bind_tool_runtime_context(ctx)
