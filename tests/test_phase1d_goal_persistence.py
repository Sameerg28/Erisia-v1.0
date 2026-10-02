"""Offline migration regression tests for the single goal persistence boundary."""
import ast
import asyncio
import builtins
import inspect
import json
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def mcp_resource(monkeypatch, tmp_path):
    """Capture real MCP handlers with a fake registration layer, no transport."""
    class Server:
        def __init__(self, name):
            self.handlers = {}

        def __getattr__(self, name):
            def decorator_factory():
                def register(handler):
                    self.handlers[name] = handler
                    return handler
                return register
            return decorator_factory

    def module(name, **attrs):
        stub = types.ModuleType(name)
        stub.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, stub)

    module("mcp", __path__=[])
    module("mcp.server", Server=Server)
    module("mcp.types", Tool=types.SimpleNamespace, TextContent=types.SimpleNamespace,
           Resource=types.SimpleNamespace)
    module("erisia.erisia_mcp_bridge", get_bridge=lambda: Mock(get_all_tools=lambda: []))
    from erisia import erisia_config
    path = tmp_path / "selected-goals.json"
    monkeypatch.setattr(erisia_config, "get_config", lambda: types.SimpleNamespace(
        paths=types.SimpleNamespace(goal_stack_file=path)))
    from erisia.erisia_mcp import create_mcp_server
    server = create_mcp_server()
    return path, server.handlers["read_resource"]


def guard_goal_io(monkeypatch, goal_path):
    """Attribute actual goal-file I/O to its nearest production caller."""
    calls = []

    def check(path):
        if not isinstance(path, (str, Path)) or Path(path) != goal_path:
            return
        caller = next((frame.frame.f_globals.get("__name__", "") for frame in inspect.stack()
                       if frame.frame.f_globals.get("__name__", "").startswith("erisia.")), None)
        if caller:
            assert caller == "erisia.erisia_goal_store", f"Goal I/O bypass: {caller}"
            calls.append(caller)

    real_open = builtins.open

    def checked_open(file, *args, **kwargs):
        check(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", checked_open)
    for method in ("exists", "read_text", "open", "write_text", "unlink", "replace"):
        original = getattr(Path, method)

        def wrapper(self, *args, _original=original, **kwargs):
            check(self)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, method, wrapper)
    return calls


@pytest.mark.parametrize("text", [None, "", "{invalid", '["legacy"]',
                                  '{ "goals": [{"id":"one","extension":{"keep":true}}] }\n'])
def test_mcp_resource_preserves_exact_existing_response(mcp_resource, monkeypatch, text):
    path, handler = mcp_resource
    if text is not None:
        path.write_text(text, encoding="utf-8")
    calls = guard_goal_io(monkeypatch, path)
    assert asyncio.run(handler("erisia://goals")) == (text if text is not None else "[]")
    assert calls
    assert path.read_text(encoding="utf-8") == text if text is not None else not path.exists()


def test_mcp_uses_configured_path_not_unrelated_bound_store(mcp_resource, tmp_path):
    from erisia.erisia_goal_store import GoalStore, bind_goal_store
    unrelated = GoalStore(tmp_path / "other.json")
    unrelated.save(["wrong file"])
    bind_goal_store(unrelated)
    path, handler = mcp_resource
    path.write_text('["configured file"]', encoding="utf-8")
    assert asyncio.run(handler("erisia://goals")) == '["configured file"]'


def test_mcp_read_error_keeps_existing_error_response(mcp_resource, monkeypatch):
    from erisia.erisia_goal_store import GoalStore
    _, handler = mcp_resource
    monkeypatch.setattr(GoalStore, "read_text", Mock(side_effect=PermissionError("denied")))
    assert asyncio.run(handler("erisia://goals")) == "Error reading erisia://goals: denied"


@pytest.mark.parametrize("text,status,display", [
    (None, "empty", "no goals"), ("[]", "ok", "0 active goals"),
    ("", "ok", "0 active goals"), ("{invalid", "ok", "0 active goals"),
    ('[{"title":"done","status":"completed"},"legacy"]', "ok", "2 active goals"),
])
def test_doctor_preserves_missing_empty_and_count_contract(tmp_path, monkeypatch, text, status, display):
    from erisia.erisia_doctor import _check_goal_stack
    path = tmp_path / "data" / "erisia_goal_stack.json"
    if text is not None:
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
    calls = guard_goal_io(monkeypatch, path)
    assert _check_goal_stack(tmp_path) == {"name": "Goal Stack", "status": status, "display": display}
    assert calls


def test_legacy_wrappers_keep_structured_fields(core, monkeypatch):
    from erisia.erisia_goal_store import GoalStore
    path = Path(core.GOAL_STACK_FILE)
    store = GoalStore(path)
    record = {"id": "keep", "title": "Preserve architecture notes", "status": "active",
              "priority": 0.75, "history": [], "extension": {"nested": [1, None]}}
    store.save([record, "Legacy task"])
    calls = guard_goal_io(monkeypatch, path)
    assert core._load_subconscious_goal_stack() == [record["title"], "Legacy task"]
    core._save_subconscious_goal_stack([record["title"], "Legacy task", "Organize invoices"])
    saved = store.load()
    assert saved[0] == record and saved[1] == "Legacy task"
    assert len(saved) == 3
    assert "Completed ->" in core.manage_goal_stack("complete", record["title"])
    completed = store.load()[0]
    assert completed["extension"] == record["extension"]
    assert completed["priority"] == record["priority"]
    assert completed["status"] == "completed"
    assert calls


def test_brain_summary_already_uses_store(tmp_path, monkeypatch):
    from erisia.erisia_goal_store import GoalStore, bind_goal_store
    from erisia.brain_loop import ErisiaBrain
    path = tmp_path / "goals.json"
    store = bind_goal_store(GoalStore(path))
    store.save(["Legacy", {"title": "Active", "status": "active"}, {"title": "Done", "status": "done"}])
    before = path.read_bytes()
    calls = guard_goal_io(monkeypatch, path)
    assert ErisiaBrain()._get_goal_stack_summary() == "2 active goals"
    assert path.read_bytes() == before
    assert calls


def test_passive_batch_persists_through_existing_goalstack(tmp_path, monkeypatch):
    from erisia import erisia_goal_store as persistence
    from erisia.erisia_cognition import GoalStack, PassiveCognitionEngine
    path = tmp_path / "goals.json"
    stack = GoalStack(path)
    write = Mock(wraps=persistence.save_goals_raw)
    monkeypatch.setattr(persistence, "save_goals_raw", write)
    engine = PassiveCognitionEngine(None, Mock(), Mock(), stack, Mock())
    monkeypatch.setattr(engine, "_generate_reflection", lambda *a: {
        "candidate_goals": [{"title": "Organize invoices", "priority": 0.8}],
        "reflection": "Offline fixture"})
    calls = guard_goal_io(monkeypatch, path)
    engine._process_batch([{"type": "fixture"}])
    assert write.call_count == 2  # Existing decay followed by ingest.
    assert persistence.GoalStore(path).load()[0]["title"] == "Organize invoices"
    assert calls


def test_remaining_known_goal_consumers_have_no_direct_file_io():
    """Guard the audited functions and MCP goal branch without banning unrelated I/O."""
    root = Path(__file__).resolve().parents[1] / "src" / "erisia"
    targets = {
        "erisia_core.py": {"_load_subconscious_goal_stack", "_save_subconscious_goal_stack",
                           "manage_goal_stack", "_count_completed_goals", "_count_abandoned_goals",
                           "_count_stale_goals", "_count_total_goals"},
        "brain_loop.py": {"_get_goal_stack_summary"},
        "erisia_doctor.py": {"_check_goal_stack"},
        "erisia_audit.py": {"_audit_goals"},
        "erisia_briefing.py": {"_collect_active_goals"},
        "erisia_self.py": {"_read_goal_stack_records"},
        "erisia_cognition.py": {"_load", "_save"},
    }
    audited = []
    for file, names in targets.items():
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in functions} == names
        audited.extend(functions)
    mcp = ast.parse((root / "erisia_mcp.py").read_text(encoding="utf-8"))
    branches = [node for node in ast.walk(mcp) if isinstance(node, ast.If)
                and ast.unparse(node.test) == "uri_text == 'erisia://goals'"]
    assert len(branches) == 1
    # Calls on store objects are allowed, raw path/file/JSON operations are not.
    audited.extend(branches)
    for node in audited:
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            expression = ast.unparse(call.func)
            assert expression not in {"open", "json.load", "json.dump", "json.loads", "json.dumps"}
            if isinstance(call.func, ast.Attribute) and call.func.attr in {
                "open", "read_text", "write_text", "read_bytes", "write_bytes", "exists", "replace", "unlink",
            }:
                receiver = ast.unparse(call.func.value)
                assert receiver == "store" or receiver.startswith("GoalStore("), expression
