"""Prove the existing single-brain wiring; no production lifecycle changes."""
import ast
import asyncio
import importlib
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("exit_mode", ["exit", "eof", "interrupt"])
def test_actual_core_ignition_retains_executed_brain(core, monkeypatch, exit_mode):
    from erisia import brain_loop

    # Execute the entire existing main-guard body, without rewriting its wiring.
    # Imported core supplies its real globals; unrelated startup boundaries are fakes.
    tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
    guards = [node for node in tree.body if isinstance(node, ast.If)
              and ast.unparse(node.test) == "__name__ == '__main__'"]
    assert len(guards) == 1
    ignition = compile(ast.Module(body=guards[0].body, type_ignores=[]), core.__file__, "exec")
    real_class = brain_loop.ErisiaBrain
    created = Mock(wraps=real_class)
    monkeypatch.setattr(core, "ErisiaBrain", created)
    monkeypatch.setattr(brain_loop, "ErisiaBrain", created)
    seen = []

    async def finite_heartbeat(self):
        seen.append(self)
        await self.awaken()
        self.stress_level = 0.37
        self.current_mission = "characterize lifecycle"

    monkeypatch.setattr(real_class, "heartbeat", finite_heartbeat)
    threads = []

    class CapturedThread:
        def __init__(self, target, kwargs=None, daemon=False):
            self.target, self.kwargs, self.daemon = target, kwargs or {}, daemon
            self.started = False
            self.join = Mock()
            threads.append(self)

        def start(self):
            self.started = True
            if self.target is core._run_brain_loop:
                self.target(**self.kwargs)

        def is_alive(self):
            return self.started

    monkeypatch.setattr(core, "threading", SimpleNamespace(Thread=CapturedThread))
    monkeypatch.setattr(core.os, "system", Mock())
    monkeypatch.setattr(core, "WorldStateTracker", Mock())
    monkeypatch.setattr(core, "_import_identity_layer", Mock(return_value=SimpleNamespace(IdentityLayer=Mock())))
    monkeypatch.setattr(core, "_import_planner", Mock(return_value=SimpleNamespace(PlanningEngine=Mock())))
    monkeypatch.setattr(core, "_sync_identity_goal_consistency", Mock())

    def finish_input(prompt):
        retained = core.brain_loop_instance
        assert seen == [retained]
        assert retained.core is core
        assert retained.is_running and retained.stress_level == 0.37
        if exit_mode == "eof":
            raise EOFError
        if exit_mode == "interrupt":
            raise KeyboardInterrupt
        return "exit"

    monkeypatch.setattr("builtins.input", finish_input)
    exec(ignition, core.__dict__)
    created.assert_called_once_with(core_module=core)
    brain = core.brain_loop_instance
    assert seen == [brain]
    assert core.brain_thread.kwargs == {"core_module": core, "brain": brain}
    assert core.brain_thread.target is brain_loop._run_brain_loop
    assert core.brain_thread.daemon and core.brain_thread.started
    assert len([thread for thread in threads if thread.target is brain_loop._run_brain_loop]) == 1
    assert core.shutdown_event.is_set()
    assert not brain.is_running
    state = json.loads(Path(brain.cryosleep_file).read_text())
    assert state["stress_level"] == 0.37
    assert state["current_mission"] == "characterize lifecycle"
    core.brain_thread.join.assert_called_once_with(timeout=5)


@pytest.mark.parametrize("worker_signal_rejection", [False, True])
def test_supplied_brain_target_never_constructs_replacement(monkeypatch, worker_signal_rejection):
    from erisia import brain_loop
    brain = brain_loop.ErisiaBrain()
    dependency = object()
    heartbeat = AsyncMock()
    monkeypatch.setattr(brain, "heartbeat", heartbeat)
    constructor = Mock(side_effect=AssertionError("duplicate construction"))
    monkeypatch.setattr(brain_loop, "ErisiaBrain", constructor)
    registration = Mock(side_effect=ValueError("not main thread") if worker_signal_rejection else None)
    monkeypatch.setattr(brain_loop.signal, "signal", registration)
    brain_loop._run_brain_loop(core_module=dependency, brain=brain)
    constructor.assert_not_called()
    heartbeat.assert_awaited_once_with()
    assert brain.core is dependency
    # asyncio.run may register its own temporary SIGINT callback afterward.
    for call in registration.call_args_list[:1 if worker_signal_rejection else 2]:
        assert call.args[1].__self__ is brain


def test_real_heartbeat_observes_cryosleep_on_same_object(monkeypatch):
    from erisia import brain_loop
    brain = brain_loop.ErisiaBrain()
    observed = []
    sleeps = []

    async def observe():
        assert brain.is_running
        observed.append(brain)
        return False, {}

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            # BaseException escapes the production retry handler if stop regresses.
            raise BaseException("heartbeat failed to stop after cryosleep")
        assert seconds == 60
        brain.initiate_cryosleep()

    monkeypatch.setattr(brain, "observe", observe)
    monkeypatch.setattr(brain_loop.asyncio, "sleep", sleep)
    monkeypatch.setattr(brain_loop, "ErisiaBrain", Mock(side_effect=AssertionError("duplicate")))
    brain_loop._run_brain_loop(brain=brain)
    assert observed == [brain]
    assert brain.cycle_count == 1 and not brain.is_running
    assert json.loads(Path(brain.cryosleep_file).read_text())["cycle_count"] == 1


def test_existing_awaken_and_cryosleep_restore_state():
    from erisia.brain_loop import ErisiaBrain
    brain = ErisiaBrain()
    asyncio.run(brain.awaken())
    assert brain.is_running
    brain.current_mission, brain.stress_level = "resume mission", 0.625
    brain.initiate_cryosleep()
    assert not brain.is_running
    brain.current_mission, brain.stress_level = None, 0.0
    asyncio.run(brain.awaken())
    assert brain.is_running
    assert (brain.current_mission, brain.stress_level) == ("resume mission", 0.625)
    assert not Path(brain.cryosleep_file).exists()
    brain.initiate_cryosleep()
    persisted = Path(brain.cryosleep_file).read_bytes()
    brain.initiate_cryosleep()  # Existing inactive no-op.
    assert Path(brain.cryosleep_file).read_bytes() == persisted


def test_keyboard_interrupt_in_runner_sleeps_supplied_brain(monkeypatch):
    from erisia import brain_loop
    brain = brain_loop.ErisiaBrain()

    async def interrupted():
        await brain.awaken()
        raise KeyboardInterrupt

    monkeypatch.setattr(brain, "heartbeat", interrupted)
    brain_loop._run_brain_loop(brain=brain)
    assert not brain.is_running
    assert Path(brain.cryosleep_file).exists()


def test_registered_signal_handler_is_bound_to_running_brain(monkeypatch):
    from erisia import brain_loop
    brain = brain_loop.ErisiaBrain()
    monkeypatch.setattr(brain, "heartbeat", brain.awaken)
    registration = Mock()
    monkeypatch.setattr(brain_loop.signal, "signal", registration)
    brain_loop._run_brain_loop(brain=brain)
    handlers = {call.args[0]: call.args[1] for call in registration.call_args_list[:2]}
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    assert all(handler.__self__ is brain for handler in handlers.values())
    with pytest.raises(SystemExit) as exit_info:
        handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert exit_info.value.code == 0
    assert not brain.is_running and Path(brain.cryosleep_file).exists()


def test_fresh_module_imports_do_not_construct_or_start_brain(monkeypatch):
    from erisia import brain_loop
    constructor = Mock(side_effect=AssertionError("brain constructed during import"))
    runner = Mock(side_effect=AssertionError("brain started during import"))
    monkeypatch.setattr(brain_loop, "ErisiaBrain", constructor)
    monkeypatch.setattr(brain_loop, "_run_brain_loop", runner)
    core = importlib.import_module("erisia.erisia_core")
    assert core.ErisiaBrain is constructor and core._run_brain_loop is runner
    constructor.assert_not_called()
    runner.assert_not_called()
    assert not hasattr(core, "brain_loop_instance")
    # Importing brain_loop itself must not execute its __main__ guard either.
    run = Mock(side_effect=AssertionError("heartbeat run during import"))
    monkeypatch.setattr(asyncio, "run", run)
    importlib.reload(brain_loop)
    run.assert_not_called()
