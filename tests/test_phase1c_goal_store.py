"""Persistence-only regression coverage; never load production goals into runtime."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "phase1c_mixed_goals.json"


@pytest.fixture
def records():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def store(tmp_path):
    from erisia.erisia_goal_store import GoalStore
    return GoalStore(tmp_path / "goals.json")


def test_missing_file_load_has_no_side_effects(store):
    assert store.load() == []
    assert not store.path.exists()


@pytest.mark.parametrize("content", ["", "  \n", "{invalid", '[{"title":"truncated"}', "null", "42", '{}'])
def test_empty_malformed_or_unsupported_file_is_read_only_empty(store, content):
    store.path.write_text(content, encoding="utf-8")
    before = store.path.read_bytes()
    assert store.load() == []
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("selection", ["single", "multiple", "legacy", "mixed"])
def test_round_trip_preserves_records_and_unknown_fields(store, records, selection):
    chosen = {"single": [records[0]], "multiple": [records[0], records[2]],
              "legacy": [records[1]], "mixed": records}[selection]
    before = copy.deepcopy(chosen)
    store.save(chosen)
    assert store.load() == chosen == before
    assert json.loads(store.path.read_text(encoding="utf-8")) == before


@pytest.mark.parametrize("wrapper", ["goals", "items", "stack"])
def test_legacy_wrapper_read_does_not_rewrite(store, records, wrapper):
    store.path.write_text(json.dumps({wrapper: records}), encoding="utf-8")
    before = store.path.read_bytes()
    assert store.load() == records
    assert store.path.read_bytes() == before


def test_legacy_load_is_deterministic_without_invented_fields(store):
    store.path.write_text('["  keep exact title  "]', encoding="utf-8")
    assert store.load() == store.load() == ["  keep exact title  "]
    assert store.list_titles() == ["keep exact title"]
    assert store.path.read_text(encoding="utf-8") == '["  keep exact title  "]'


def test_repeated_saves_are_valid_and_non_mutating(store, records):
    for index in range(10):
        records[0]["extension"] = {"revision": index}
        store.save(records)
        assert json.loads(store.path.read_text(encoding="utf-8")) == records
    assert list(store.path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("invalid", ["unserializable", "circular"])
def test_serialization_failure_preserves_original_and_leaves_no_temp(store, records, invalid):
    store.save(records)
    before = store.path.read_bytes()
    replacement = [{"unknown": object()}]
    if invalid == "circular":
        replacement = []
        replacement.append(replacement)
    with pytest.raises((TypeError, ValueError)):
        store.save(replacement)
    assert store.path.read_bytes() == before
    assert list(store.path.parent.glob("*.tmp")) == []


def test_failed_serialization_does_not_create_missing_directory(tmp_path):
    from erisia.erisia_goal_store import GoalStore
    store = GoalStore(tmp_path / "not-created" / "goals.json")
    with pytest.raises(TypeError):
        store.save([{"value": object()}])
    assert not store.path.parent.exists()


def test_save_does_not_touch_existing_sidecar_or_backup(store, records):
    sidecar = store.path.with_suffix(".json.tmp")
    backup = store.path.with_suffix(".json.bak")
    for path in (sidecar, backup):
        path.write_bytes(b"leave me alone")
    store.save(records)
    assert store.load() == records
    for path in (sidecar, backup):
        assert path.read_bytes() == b"leave me alone"


@pytest.mark.parametrize("failure", ["write", "flush", "fsync", "replace"])
def test_io_failure_keeps_previous_valid_state(store, records, monkeypatch, failure):
    from erisia import erisia_goal_store as module
    store.save(records)
    before = store.path.read_bytes()
    if failure in {"fsync", "replace"}:
        monkeypatch.setattr(module.os, failure, Mock(side_effect=OSError(failure)))
    else:
        real_temporary_file = module.tempfile.NamedTemporaryFile

        class BrokenFile:
            def __init__(self, handle):
                self.handle = handle
                self.name = handle.name

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def write(self, text):
                if failure == "write":
                    self.handle.write(text[:5])
                    raise OSError("partial write")
                return self.handle.write(text)

            def flush(self):
                raise OSError("flush")

        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile",
                            lambda **kw: BrokenFile(real_temporary_file(**kw)))
    with pytest.raises(OSError):
        store.save([{"title": "replacement"}])
    assert store.path.read_bytes() == before
    assert store.load() == records
    assert list(store.path.parent.glob("*.tmp")) == []


def test_replace_sees_complete_synced_temp_and_intact_original(store, records, monkeypatch):
    from erisia import erisia_goal_store as module
    store.save(records)
    before = store.path.read_bytes()
    replacement = [{"title": "new snapshot", "extension": {"keep": True}}]
    real_replace = module.os.replace
    real_fsync = module.os.fsync
    order = []

    def sync(fd):
        order.append("sync")
        return real_fsync(fd)

    def replace(source, destination):
        assert order == ["sync"]
        assert Path(source).parent == store.path.parent
        assert Path(source) != store.path.with_suffix(".json.tmp")
        assert json.loads(Path(source).read_text(encoding="utf-8")) == replacement
        assert store.path.read_bytes() == before
        order.append("replace")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "fsync", sync)
    monkeypatch.setattr(module.os, "replace", replace)
    store.save(replacement)
    assert order == ["sync", "replace"]
    assert store.load() == replacement


def test_goalstack_uses_existing_persistence_boundary(store, records, monkeypatch):
    from erisia import erisia_goal_store as module
    from erisia.erisia_cognition import GoalStack
    store.save(records)
    before = store.path.read_bytes()
    read = Mock(wraps=module.load_goals_raw)
    write = Mock(wraps=module.save_goals_raw)
    monkeypatch.setattr(module, "load_goals_raw", read)
    monkeypatch.setattr(module, "save_goals_raw", write)
    stack = GoalStack(store.path)
    read.assert_called_once_with(store.path)
    write.assert_not_called()
    assert store.path.read_bytes() == before
    summary = stack.summary_text()
    stack._save()
    write.assert_called_once_with(store.path, stack.goals)
    assert store.load() == records
    assert GoalStack(store.path).summary_text() == summary


def test_every_goalstack_created_field_and_extension_survives(store):
    from erisia.erisia_cognition import GoalStack
    stack = GoalStack(store.path)
    goal = stack.add_or_update_goal("Understand persistence", deadline="2030-01-01")
    assert set(goal) == {"id", "title", "rationale", "next_action", "priority", "confidence",
                         "urgency", "progress", "status", "source", "deadline", "created_at",
                         "updated_at", "recency", "history"}
    goal["unknown"] = {"nested": [1, None, "café"]}
    store.save(stack.goals)
    assert GoalStack(store.path).goals == stack.goals


def test_default_accessor_uses_existing_configured_path(tmp_path, monkeypatch):
    from erisia import erisia_goal_store as module
    from erisia import erisia_config
    configured = tmp_path / "configured" / "goals.json"
    monkeypatch.setattr(erisia_config, "get_config", lambda: SimpleNamespace(
        paths=SimpleNamespace(goal_stack_file=configured)))
    module.reset_goal_store_for_tests()
    assert module.get_goal_store().path == configured
    assert not configured.exists()


def test_production_goal_file_unchanged(production_goals_unchanged):
    # The same guard runs again after all suites finish, using only read-only hashes.
    production_goals_unchanged()
