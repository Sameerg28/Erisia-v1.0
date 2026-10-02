"""Phase 1A: characterize the checkout without repairing production behavior."""
import copy
import importlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("raw,expected", [
    (None, {}), ("", {}), ("  ", {}), ("not-json", {}), ("[]", {}),
    ("null", {}), ("42", {}), ([], {}), (42, {}),
    ({"nested": {"value": 2}}, {"nested": {"value": 2}}),
    (' {"enabled": true, "items": [1, 2]} ', {"enabled": True, "items": [1, 2]}),
])
def test_argument_parsing(raw, expected):
    from erisia.erisia_tool_router import _parse_tool_arguments
    assert _parse_tool_arguments(raw) == expected


@pytest.mark.parametrize("content,expected", [
    (None, []), ("ordinary response", []), ("{invalid}", []),
    ('{"arguments": {}}', []),
    ('```json\n{"name":"health","parameters":{"n":2}}\n```', [{"name": "health", "arguments": {"n": 2}}]),
    ('{"function":{"name":"health","arguments":"{\\"n\\":2}"}}', [{"name": "health", "arguments": {"n": 2}}]),
    ('prefix {"name":"a","args":{"n":1}} and {"type":"function","name":"b","arguments":{}}',
     [{"name": "a", "arguments": {"n": 1}}, {"name": "b", "arguments": {}}]),
    ('{"name":"health","arguments":"invalid"}', [{"name": "health", "arguments": {}}]),
])
def test_text_tool_extraction(content, expected):
    from erisia.erisia_tool_router import _extract_text_tool_calls
    assert _extract_text_tool_calls(content) == expected


@pytest.mark.xfail(strict=True, reason="Existing parser counts braces inside JSON strings")
def test_text_tool_extraction_with_literal_brace():
    from erisia.erisia_tool_router import _extract_text_tool_calls
    assert _extract_text_tool_calls(json.dumps({"name": "echo", "arguments": {"text": "{"}})) == [
        {"name": "echo", "arguments": {"text": "{"}}]


@pytest.fixture
def router(monkeypatch):
    from erisia import erisia_tool_router as module
    from erisia.erisia_runtime_context import ToolRuntimeContext, bind_tool_runtime_context
    ctx = ToolRuntimeContext(daemon_system=Mock(), graph_memory=Mock(),
                             get_world_state=Mock(return_value={"active": "fake"}),
                             approve_skill=Mock(return_value="approved"),
                             manage_goal_stack=Mock(return_value="goals"))
    bind_tool_runtime_context(ctx)
    return module, ctx


@pytest.mark.parametrize("name,args,target,expected_args", [
    ("check_pc_health", {}, "check_pc_health", ()),
    ("kill_process", {"process_name": "fake.exe"}, "kill_process", ("fake.exe",)),
    ("execute_local_os_command", {"script_code": "fake code"}, "execute_local_os_command", ("fake code",)),
    ("execute_secure_docker", {"script_code": "fake code"}, "execute_secure_docker", ("fake code",)),
])
def test_dispatch_existing_tools(router, monkeypatch, name, args, target, expected_args):
    module, _ = router
    handler = Mock(return_value="sentinel result")
    monkeypatch.setattr(module, target, handler)
    assert module._execute_tool_call(name, args, "request") == "sentinel result"
    handler.assert_called_once_with(*expected_args)


def test_dispatch_context_and_dynamic_skill(router):
    module, ctx = router
    assert module._execute_tool_call("get_world_state", {}, "request") == {"active": "fake"}
    ctx.get_world_state.assert_called_once_with()
    skill = Mock(return_value="computed")
    assert module._execute_tool_call("custom", {"value": 4}, "request", {"custom": skill}) == "computed"
    skill.assert_called_once_with(value=4)
    assert module._execute_tool_call("missing", {}, "request") == "[SYSTEM ERROR]: Unknown tool 'missing'."


@pytest.mark.parametrize("intent,allowed", [("approve weather", True), ("yes", True),
                                          ("how do I approve weather?", False), ("approve weather?", False)])
def test_approval_intent_gate(router, intent, allowed):
    module, ctx = router
    result = module._execute_tool_call("approve_skill", {"skill_name": "weather"}, intent)
    if allowed:
        assert result == "approved"
        ctx.approve_skill.assert_called_once_with("weather")
    else:
        assert "[ALIGNMENT INTERCEPTOR]" in result
        ctx.approve_skill.assert_not_called()


def test_manage_goal_dispatch_is_currently_wired(router):
    module, ctx = router
    assert module._execute_tool_call("manage_goal_stack", {"action": "add", "goal_text": "Read"}, "request") == "goals"
    ctx.manage_goal_stack.assert_called_once_with("add", "Read")
    ctx.manage_goal_stack = None
    assert module._execute_tool_call("manage_goal_stack", {"action": "view"}, "request") == (
        "[SYSTEM ERROR]: manage_goal_stack is not bound in runtime context.")


def test_dispatch_failure_contract(router, monkeypatch):
    module, _ = router
    fail = Mock(side_effect=RuntimeError("characterized failure"))
    assert module._execute_tool_call("broken", {}, "request", {"broken": fail}) == (
        "[DYNAMIC SKILL ERROR]: characterized failure")
    monkeypatch.setattr(module, "check_pc_health", fail)
    with pytest.raises(RuntimeError, match="characterized failure"):
        module._execute_tool_call("check_pc_health", {}, "request")


def test_manage_goal_lifecycle(core):
    assert core.manage_goal_stack("view") == "[GOAL STACK]: No active goals."
    assert core.manage_goal_stack("add", " ") == "[GOAL STACK]: goal_text is required to add."
    assert core.manage_goal_stack("unknown") == "[GOAL STACK]: Invalid action. Use add, complete, or view."
    assert core.manage_goal_stack(" ADD ", " Read architecture ") == "[GOAL STACK]: Added -> Read architecture"
    assert core.manage_goal_stack("view") == "[GOAL STACK]: 1. Read architecture"
    assert core.manage_goal_stack("complete", "Read architecture") == "[GOAL STACK]: Completed -> Read architecture"
    assert core.manage_goal_stack("view") == "[GOAL STACK]: No active goals."
    stored = json.loads(Path(core.GOAL_STACK_FILE).read_text(encoding="utf-8"))
    assert stored[0]["status"] == "completed"
    assert stored[0]["title"] == "Read architecture"


def test_goalstack_structured_round_trip(tmp_path):
    from erisia.erisia_cognition import GoalStack
    path = tmp_path / "goals.json"
    stack = GoalStack(path)
    assert stack.focus_snapshot() == []
    stack.add_or_update_goal("Read architecture", rationale="Understand runtime", next_action="Read baseline",
                             priority=0.8, confidence=0.7, urgency=0.6, source="manual")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    restored = GoalStack(path)
    assert restored.goals == on_disk == stack.goals
    goal = restored.focus_snapshot()[0]
    assert goal["title"] == "Read architecture"
    assert goal["rationale"] == "Understand runtime"
    assert goal["priority"] == 0.8
    assert goal["id"] and goal["history"]
    assert "Read architecture" in restored.summary_text()


def test_semantic_memory_persists_and_queries(tmp_path):
    from erisia.erisia_memory import MemoryManager
    directory = tmp_path / "semantic"
    first = MemoryManager(directory)
    assert first.collection.name == "erisia_knowledge"
    assert first.tools_collection.name == "erisia_tools"
    assert first.add_memory("baseline memory", {"source": "test"}, "known-id") == "known-id"
    second = MemoryManager(directory)
    assert second.query_memory("baseline", n_results=1) == ["baseline memory"]
    result = second.collection.get(ids=["known-id"])
    assert result["metadatas"] == [{"source": "test"}]
    assert (directory / "chroma.sqlite3").exists()


def test_semantic_memory_failure_contract(tmp_path, monkeypatch):
    from erisia.erisia_memory import MemoryManager
    memory = MemoryManager(tmp_path / "semantic")
    assert memory.add_memory("") is None
    assert memory.query_memory("") == []
    monkeypatch.setattr(memory, "collection", Mock(add=Mock(side_effect=RuntimeError("storage")),
                                                 query=Mock(side_effect=RuntimeError("storage"))))
    assert memory.add_memory("record") is None
    assert memory.query_memory("record") == []


def test_episodic_order_limit_and_metadata(tmp_path, monkeypatch):
    from erisia import erisia_episodic_memory as epi
    path = tmp_path / "episodes.db"
    monkeypatch.setattr(epi, "DB_PATH", path)
    assert epi.get_recent_context() == "No episodic memory recorded yet."
    for index in range(3):
        epi.log_episode("test", f"episode-{index}", {"index": index})
    context = epi.get_recent_context(limit=2)
    assert "episode-0" not in context
    assert context.index("episode-1") < context.index("episode-2")
    assert 'metadata={"index": 2}' in context
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 3


def test_brain_construction_is_idle(offline_runtime):
    from erisia.brain_loop import ErisiaBrain
    dependency = object()
    brain = ErisiaBrain(core_module=dependency)
    assert brain.core is dependency
    assert (brain.is_running, brain.cycle_count, brain.stress_level) == (False, 0, 0.0)
    assert brain.current_mission is None and brain.last_observation == {}
    assert Path(brain.cryosleep_file).parent == offline_runtime / "data"
    assert not Path(brain.cryosleep_file).exists()
    assert not Path(brain.pulse_log).exists()


@pytest.mark.parametrize("name,attribute", [
    ("erisia_tool_router", "_execute_tool_call"), ("erisia_cognition", "GoalStack"),
    ("erisia_memory", "MemoryManager"), ("erisia_episodic_memory", "log_episode"),
    ("brain_loop", "ErisiaBrain"), ("erisia_core", "erisia_complete_brain"),
])
def test_module_import_smoke(offline_runtime, name, attribute):
    module = importlib.import_module(f"erisia.{name}")
    assert callable(getattr(module, attribute))
    assert Path(module.__file__).is_relative_to(offline_runtime)


def response(content="Acknowledged.", tools=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tools))])


def tool_call():
    return NS(id="call-1", function=NS(name="check_pc_health", arguments="{}"))


def test_complete_brain_plain_reply(core, monkeypatch):
    llm = Mock(return_value=response())
    monkeypatch.setattr(core, "query_llm", llm)
    assert core.erisia_complete_brain("ping", system_injection="test instruction") == "Acknowledged."
    assert llm.call_count == 1
    messages = llm.call_args.kwargs["messages"]
    assert messages[-1] == {"role": "user", "content": "ping"}
    assert any("test instruction" in m["content"] for m in messages)
    assert core.chat_history == ["Master Sameer: ping", "Erisia: Acknowledged."]
    assert json.loads(Path(core.TRAINING_DATA_FILE).read_text()) == {"prompt": "ping", "completion": "Acknowledged."}


@pytest.mark.parametrize("native", [True, False])
def test_complete_brain_tool_round_trip(core, monkeypatch, native):
    first = response("", [tool_call()]) if native else response('{"name":"check_pc_health","arguments":{}}')
    seen = []
    replies = iter([first, response("CPU OK.")])

    def llm(**kwargs):
        seen.append(copy.deepcopy(kwargs["messages"]))
        return next(replies)

    execute = Mock(return_value="CPU: fake")
    monkeypatch.setattr(core, "query_llm", llm)
    monkeypatch.setattr(core, "_execute_tool_call", execute)
    assert core.erisia_complete_brain("status") == "CPU OK."
    execute.assert_called_once_with("check_pc_health", {}, "status", {})
    assert len(seen) == 2
    assert any("CPU: fake" in str(m.get("content")) for m in seen[1])
    if native:
        assert {"role": "tool", "tool_call_id": "call-1", "name": "check_pc_health", "content": "CPU: fake"} in seen[1]
    assert "Executed check_pc_health" in core.get_recent_context()


def test_complete_brain_returned_error_retry_limit(core, monkeypatch):
    llm = Mock(return_value=response("", [tool_call()]))
    execute = Mock(return_value="[SYSTEM ERROR]: characterized failure")
    monkeypatch.setattr(core, "query_llm", llm)
    monkeypatch.setattr(core, "_execute_tool_call", execute)
    assert core.erisia_complete_brain("status") is None
    assert execute.call_count == llm.call_count == 4
    assert core.consecutive_errors == 0
    assert core.chat_history == []
    assert not Path(core.TRAINING_DATA_FILE).exists()


def test_complete_brain_raised_tool_error(core, monkeypatch):
    llm = Mock(return_value=response("", [tool_call()]))
    monkeypatch.setattr(core, "query_llm", llm)
    monkeypatch.setattr(core, "_execute_tool_call", Mock(side_effect=RuntimeError("tool exploded")))
    assert core.erisia_complete_brain("status") == "Master... my connection faltered."
    assert llm.call_count == 1
    assert core.chat_history[-1] == "Erisia: Master... my connection faltered."


@pytest.mark.xfail(strict=True, reason="Existing self-inspection maps erisia_core.py to erisia_execution.py")
def test_self_inspection_returns_requested_core():
    from erisia.erisia_execution import inspect_core_architecture
    assert "def erisia_complete_brain(" in inspect_core_architecture("erisia_core.py")
