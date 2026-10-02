"""
Phase 1 characterization + stabilization tests.

No live API calls. External LLM / network / OS actions are mocked.
"""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Tool argument parsing ─────────────────────────────────────────────


def test_parse_tool_arguments_dict_and_json():
    from erisia.erisia_tool_router import _parse_tool_arguments

    assert _parse_tool_arguments({"a": 1}) == {"a": 1}
    assert _parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert _parse_tool_arguments(None) == {}
    assert _parse_tool_arguments("not-json") == {}
    assert _parse_tool_arguments("") == {}


def test_extract_text_tool_calls():
    from erisia.erisia_tool_router import _extract_text_tool_calls

    calls = _extract_text_tool_calls(
        '{"name": "check_pc_health", "arguments": {}}'
    )
    assert len(calls) == 1
    assert calls[0]["name"] == "check_pc_health"


# ── Tool dispatch ─────────────────────────────────────────────────────


@pytest.fixture
def tool_runtime(tmp_path):
    from erisia.erisia_goal_store import GoalStore, bind_goal_store, reset_goal_store_for_tests
    from erisia.erisia_runtime_context import (
        ToolRuntimeContext,
        bind_tool_runtime_context,
        clear_tool_runtime_context,
    )

    reset_goal_store_for_tests()
    clear_tool_runtime_context()
    store = bind_goal_store(GoalStore(tmp_path / "goals.json"))

    def manage(action, goal_text=None):
        return store.manage(action, goal_text)

    graph = MagicMock()
    graph.add_memory_relation.return_value = "[Graph Updated]"

    ctx = bind_tool_runtime_context(
        ToolRuntimeContext(
            daemon_system=MagicMock(),
            get_world_state=lambda: {"ok": True},
            analyze_screen=lambda *_a, **_k: "[screen]",
            reason_about_event=lambda *_a, **_k: "[reason]",
            update_consciousness=lambda *_a, **_k: "[consciousness]",
            save_heuristic_rule=lambda *_a, **_k: "[heuristic]",
            forge_new_skill=lambda *_a, **_k: "[forge]",
            forge_pending_skill=lambda *_a, **_k: "[pending]",
            approve_skill=lambda *_a, **_k: "[approve]",
            reject_skill=lambda *_a, **_k: "[reject]",
            graph_memory=graph,
            custom_skill_functions={},
            speak_text=lambda *_a, **_k: "[speak]",
            mirofish_call=lambda **_k: "[mirofish]",
            manage_goal_stack=manage,
        )
    )
    yield ctx, store
    clear_tool_runtime_context()
    reset_goal_store_for_tests()


def test_dispatch_check_pc_health(tool_runtime):
    from erisia.erisia_tool_router import _execute_tool_call

    with patch("erisia.erisia_tool_router.check_pc_health", return_value="CPU OK"):
        result = _execute_tool_call("check_pc_health", {}, "status?")
    assert result == "CPU OK"


def test_dispatch_manage_goal_stack(tool_runtime):
    from erisia.erisia_tool_router import _execute_tool_call

    _, store = tool_runtime
    added = _execute_tool_call(
        "manage_goal_stack",
        {"action": "add", "goal_text": "Stabilize routing"},
        "add a goal",
    )
    assert "Added ->" in added
    viewed = _execute_tool_call("manage_goal_stack", {"action": "view"}, "view goals")
    assert "Stabilize routing" in viewed
    goals = store.load()
    assert any(
        isinstance(g, dict) and g.get("title") == "Stabilize routing" for g in goals
    )


def test_dispatch_unknown_tool(tool_runtime):
    from erisia.erisia_tool_router import _execute_tool_call

    result = _execute_tool_call("not_a_real_tool", {}, "hi")
    assert "Unknown tool" in result


def test_dynamic_skill_failure_handling(tool_runtime):
    from erisia.erisia_runtime_context import get_tool_runtime_context
    from erisia.erisia_tool_router import _execute_tool_call

    def boom(**_kwargs):
        raise RuntimeError("boom")

    ctx = get_tool_runtime_context()
    ctx.custom_skill_functions["broken_skill"] = boom
    result = _execute_tool_call("broken_skill", {}, "run it")
    assert "[DYNAMIC SKILL ERROR]" in result
    assert "boom" in result


# ── GoalStore ─────────────────────────────────────────────────────────


def test_goalstore_structured_round_trip(tmp_path):
    from erisia.erisia_goal_store import GoalStore, reset_goal_store_for_tests

    reset_goal_store_for_tests()
    path = tmp_path / "erisia_goal_stack.json"
    store = GoalStore(path)
    created = store.add(
        "Persist structured goal",
        rationale="phase1",
        next_action="test",
        priority=0.8,
        source="test",
    )
    assert isinstance(created, dict)
    assert created["title"] == "Persist structured goal"
    assert "id" in created

    reloaded = GoalStore(path).load()
    assert len(reloaded) == 1
    assert reloaded[0]["title"] == "Persist structured goal"
    assert reloaded[0]["rationale"] == "phase1"
    assert reloaded[0]["priority"] == 0.8


def test_goalstore_mixed_format_compatibility(tmp_path):
    from erisia.erisia_goal_store import GoalStore, load_goals_raw

    path = tmp_path / "mixed.json"
    mixed = [
        {
            "id": "abc",
            "title": "Structured",
            "status": "active",
            "priority": 0.7,
            "extra_field": "keep-me",
        },
        "Legacy string goal",
    ]
    path.write_text(json.dumps(mixed), encoding="utf-8")
    loaded = load_goals_raw(path)
    assert loaded[0]["extra_field"] == "keep-me"
    assert loaded[1] == "Legacy string goal"

    store = GoalStore(path)
    store.complete("Structured")
    after = store.load()
    structured = next(g for g in after if isinstance(g, dict) and g.get("id") == "abc")
    assert structured["status"] == "completed"
    assert structured["extra_field"] == "keep-me"
    assert "Legacy string goal" in [g if isinstance(g, str) else g.get("title") for g in after]


def test_legacy_helpers_do_not_flatten_file(tmp_path, monkeypatch):
    from erisia.erisia_goal_store import GoalStore, bind_goal_store, reset_goal_store_for_tests

    reset_goal_store_for_tests()
    path = tmp_path / "goals.json"
    store = bind_goal_store(GoalStore(path))
    store.add("Keep structured", rationale="important", source="test")

    # Import helpers after bind; patch path lookup.
    import erisia.erisia_core as core

    monkeypatch.setattr(
        core,
        "_get_erisia_paths",
        lambda: {"GOAL_STACK_FILE": str(path), "GOAL_STACK_PATH": path, "GOAL_STALE_DAYS": 7},
    )
    core._save_subconscious_goal_stack(["Keep structured", "New via legacy"])
    data = json.loads(path.read_text(encoding="utf-8"))
    structured = [g for g in data if isinstance(g, dict) and g.get("title") == "Keep structured"]
    assert structured, "structured goal must survive legacy helper"
    assert structured[0].get("rationale") == "important"
    titles = [
        g.get("title") if isinstance(g, dict) else g for g in data
    ]
    assert "New via legacy" in titles
    reset_goal_store_for_tests()


def test_goalstore_repeated_writes_do_not_corrupt(tmp_path):
    from erisia.erisia_goal_store import GoalStore

    path = tmp_path / "goals.json"
    store = GoalStore(path)
    titles = [
        "Audit Oracle sharpe ratio",
        "Approve pending weather skill",
        "Rewrite firewall documentation",
        "Inspect relational memory graph",
        "Calibrate voice listening timeout",
        "Review portfolio risk limits",
        "Prune stale mission reports",
        "Validate tool router dispatch",
    ]
    for title in titles:
        store.add(title, source="test")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) >= 8
    json.loads(path.read_text(encoding="utf-8"))  # still valid JSON


# ── MemoryStore / Chroma ──────────────────────────────────────────────


@pytest.fixture
def isolated_memory(tmp_path):
    from erisia.erisia_memory_store import MemoryStore, bind_memory_store, reset_memory_store_for_tests

    reset_memory_store_for_tests()
    store = bind_memory_store(MemoryStore(memory_dir=tmp_path / "chroma"))
    yield store
    reset_memory_store_for_tests()


def test_memory_store_init_add_query(isolated_memory):
    store = isolated_memory
    doc_id = store.add_memory("Erisia remembers the baseline audit.")
    assert doc_id
    hits = store.query_memory("baseline audit", n_results=3)
    assert isinstance(hits, list)
    assert any("baseline" in h.lower() for h in hits)


def test_memory_store_empty_query(isolated_memory):
    store = isolated_memory
    assert store.query_memory("", n_results=3) == []


def test_memory_store_repeated_init(tmp_path):
    from erisia.erisia_memory_store import MemoryStore, reset_memory_store_for_tests

    reset_memory_store_for_tests()
    path = tmp_path / "chroma2"
    a = MemoryStore(memory_dir=path)
    a.add_memory("first document about apples")
    b = MemoryStore(memory_dir=path)
    hits = b.query_memory("apples", n_results=2)
    assert hits
    reset_memory_store_for_tests()


def test_legacy_memory_manager_does_not_open_second_client(isolated_memory):
    import erisia.erisia_memory_manager as mm

    assert mm.db_client is None
    mm.add_memory_document("legacy path write")
    result = mm.query_memory_documents("legacy path", n_results=2)
    assert isinstance(result, dict)
    docs = (result.get("documents") or [[]])[0]
    assert any("legacy" in d.lower() for d in docs)


def test_memory_store_failure_handling(isolated_memory):
    store = isolated_memory
    # Empty document should soft-fail
    assert store.add_memory("") is None


# ── Episodic memory ───────────────────────────────────────────────────


def test_episodic_memory_write_read(tmp_path, monkeypatch):
    db_path = tmp_path / "episodic_test.db"
    monkeypatch.setenv("ERISIA_EPISODIC_DB_PATH", str(db_path))

    import importlib
    import erisia.erisia_episodic_memory as epi

    importlib.reload(epi)
    epi.log_episode("user_chat", "hello characterization", {"k": 1})
    ctx = epi.get_recent_context(limit=5)
    assert "hello characterization" in ctx
    assert "user_chat" in ctx


# ── Brain singleton ───────────────────────────────────────────────────


def test_brain_construction():
    from erisia.brain_loop import ErisiaBrain

    brain = ErisiaBrain(core_module=None)
    assert brain.is_running is False
    assert brain.cycle_count == 0


def test_brain_thread_shares_same_instance():
    from erisia.brain_loop import ErisiaBrain, _run_brain_loop

    brain = ErisiaBrain(core_module=None)
    seen = {}

    async def fake_heartbeat(self):
        seen["brain_id"] = id(self)
        self.is_running = False

    brain.heartbeat = types.MethodType(fake_heartbeat, brain)

    thread = threading.Thread(
        target=_run_brain_loop,
        kwargs={"core_module": None, "brain": brain},
        daemon=True,
    )
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert seen.get("brain_id") == id(brain)


def test_brain_standalone_creates_instance_when_missing():
    from erisia.brain_loop import _run_brain_loop

    seen = {}

    class FakeBrain:
        def __init__(self, core_module=None):
            self.core = core_module
            seen["created"] = True

        async def heartbeat(self):
            seen["ran"] = True

        def initiate_cryosleep(self, *a, **k):
            return None

    with patch("erisia.brain_loop.ErisiaBrain", FakeBrain):
        _run_brain_loop(core_module=None, brain=None)
    assert seen.get("created") and seen.get("ran")


# ── Import / smoke ────────────────────────────────────────────────────


def test_import_router_without_executing_core_cycle():
    """Router module imports without requiring core at import time."""
    import importlib

    mod = importlib.import_module("erisia.erisia_tool_router")
    assert hasattr(mod, "_execute_tool_call")
    assert hasattr(mod, "_parse_tool_arguments")


def test_import_goal_and_memory_stores_independently():
    import erisia.erisia_goal_store as gs
    import erisia.erisia_memory_store as ms
    import erisia.erisia_runtime_context as rc

    assert hasattr(gs, "GoalStore")
    assert hasattr(ms, "MemoryStore")
    assert hasattr(rc, "ToolRuntimeContext")


def test_mocked_complete_brain_smoke(tmp_path, monkeypatch):
    """Minimal erisia_complete_brain path with mocked LLM (no live APIs)."""
    # Heavy module — import may pull optional services; patch key externals.
    pytest.importorskip("chromadb")

    from erisia.erisia_goal_store import GoalStore, bind_goal_store, reset_goal_store_for_tests
    from erisia.erisia_memory_store import MemoryStore, bind_memory_store, reset_memory_store_for_tests

    reset_goal_store_for_tests()
    reset_memory_store_for_tests()
    bind_goal_store(GoalStore(tmp_path / "goals.json"))
    bind_memory_store(MemoryStore(memory_dir=tmp_path / "mem"))

    mock_msg = MagicMock()
    mock_msg.content = "Acknowledged, Master."
    mock_msg.tool_calls = None
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    # Import core only inside test after env isolation where possible.
    import erisia.erisia_core as core

    monkeypatch.setattr(core, "query_llm", lambda *a, **k: mock_response)
    monkeypatch.setattr(core, "log_episode", lambda *a, **k: None)
    monkeypatch.setattr(core, "get_recent_context", lambda *a, **k: "")
    monkeypatch.setattr(core, "load_dynamic_skills", lambda base, *a, **k: (base, {}))
    monkeypatch.setattr(core, "initialize_advanced_cognition", lambda: None)
    monkeypatch.setattr(core, "passive_cognition_engine", None)
    monkeypatch.setattr(core, "goal_stack", None)
    monkeypatch.setattr(core, "ENABLE_META_REVIEW", False, raising=False)

    # Avoid training file writes into repo — redirect path
    monkeypatch.setitem(
        core._get_erisia_paths(),
        "TRAINING_DATA_FILE",
        str(tmp_path / "training.jsonl"),
    )
    core.TRAINING_DATA_FILE = str(tmp_path / "training.jsonl")

    reply = core.erisia_complete_brain("ping")
    assert isinstance(reply, str)
    assert len(reply) > 0

    reset_goal_store_for_tests()
    reset_memory_store_for_tests()


def test_no_production_direct_goal_json_dump_outside_store():
    """Static check: goal_stack.json writers should go through GoalStore helpers."""
    core_path = SRC / "erisia" / "erisia_core.py"
    text = core_path.read_text(encoding="utf-8")
    # Legacy flatten writer removed.
    assert 'json.dump(safe_goals' not in text
    assert "store.manage" in text or "get_goal_store" in text


def test_memory_manager_marked_legacy_no_persistent_client_init():
    mm_path = SRC / "erisia" / "erisia_memory_manager.py"
    text = mm_path.read_text(encoding="utf-8")
    assert "LEGACY MODULE" in text
    assert "chromadb.PersistentClient" not in text
