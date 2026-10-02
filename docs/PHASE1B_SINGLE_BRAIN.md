# Phase 1B: single authoritative brain verification

Date: 2026-09-27. Scope: brain instance identity and existing lifecycle controls.

## Finding and confirmed before-state

The double-instance defect described in `ARCHITECTURE_BASELINE.md` is already
absent from this checkout. Its lifecycle description is stale. Phase 1A also
noted the existing injected-instance support. No production patch was needed
or made in Phase 1B.

The current source was traced before making changes:

| Location | Current behavior |
| --- | --- |
| `erisia_core.py:3085` | Normal startup is inside the `__main__` guard |
| `erisia_core.py:3094` | Initializes `brain_loop_instance = None` |
| `erisia_core.py:3134` | Constructs one `ErisiaBrain(core_module=sys.modules[__name__])` and retains it in `brain_loop_instance` |
| `erisia_core.py:3135-3143` | Creates and starts a daemon thread targeting `_run_brain_loop`, passing that same object as `kwargs['brain']` and the running core module as `core_module` |
| `brain_loop.py:424-435` | `_run_brain_loop(core_module=None, brain=None)` constructs only when `brain is None`; supplied brains are reused, with missing core references filled in |
| `brain_loop.py:439-446` | Attempts signal registration on the supplied object's bound cryosleep method, then invokes `asyncio.run(brain.heartbeat())` |
| `brain_loop.py:391-395` | `heartbeat()` awaits `self.awaken()` and loops on that object's `self.is_running` |
| `erisia_core.py:3488-3495` | Chat cleanup sets `shutdown_event`, calls `brain_loop_instance.initiate_cryosleep()`, and joins the brain thread with a five-second timeout |

A search across production Python source found two construction sites: core
startup and the conditional fallback in `_run_brain_loop`. Only the first is
taken during normal core startup. The standalone `python -m erisia.brain_loop`
entry calls the runner without an instance and intentionally uses the fallback.

Other references to `brain_loop_instance` are its initial `None` assignment,
thread argument, and cleanup guard/call. There is no separate runtime brain
reference used for wake or stop. The module's normal import does not execute
the main guard or create a brain.

## Exact historical defect versus current call path

The historical baseline describes core retaining object A while the worker
constructs and executes object B. That would direct core cleanup to A instead
of the active B. This historical behavior was not reproduced by current code.

Before Phase 1B, and unchanged afterward:

```text
core __main__
  -> brain_loop_instance = ErisiaBrain(core_module=running_core)  [object A]
  -> Thread(target=_run_brain_loop,
            kwargs={core_module: running_core, brain: A}, daemon=True)
  -> thread.start()
  -> _run_brain_loop(..., brain=A)  [constructor fallback not taken]
  -> asyncio.run(A.heartbeat())
  -> A.awaken(); while A.is_running: ...

core chat finally
  -> shutdown_event.set()
  -> A.initiate_cryosleep()
  -> brain_thread.join(timeout=5)
```

Manual inspection answers:

- Normal core startup creates **one** ErisiaBrain object.
- The thread executes `heartbeat()` on `brain_loop_instance` itself.
- The running core module retains that exact object.
- Existing brain lifecycle methods and core cleanup affect that same object.
  Successful signal registrations also bind to that object, subject to the
  worker-thread signal limitation below.

## Lifecycle implications and remaining limitations

`__init__` stores the core reference and initializes idle state, mission, stress,
cycle count, and observation. `awaken()` sets `is_running = True` and recovers
mission/stress from temporary cryosleep state when present. `initiate_cryosleep()`
is a no-op when already inactive; otherwise it sets `is_running = False` and
serializes mission, stress, cycle count, observation, and timestamp. With a signal
argument it then calls `sys.exit(0)`. The runner's KeyboardInterrupt handler
also calls cryosleep on the supplied instance.

Remaining issues/limits observed by source inspection, deliberately unchanged:

- There is no dedicated `stop()` or `resume()` method. `awaken()` changes state
  but does not create or restart a terminated thread. Core has no interactive
  wake/resume command for this brain.
- `shutdown_event` is not checked by heartbeat. Core's final cryosleep call is
  what changes the brain's running flag.
- Cryosleep does not cancel in-flight cognition or interrupt the 5/60/15-second
  sleeps. The five-second join can expire while the daemon thread is still
  active. State can change during an in-flight cycle after a sleep request.
- An early shutdown before `awaken()` can hit cryosleep's inactive no-op;
  subsequent awakening is not prevented by a startup/shutdown handshake.
- Python rejects `signal.signal` in the normal worker thread. The runner catches
  `ValueError`; this does not install a main-thread SIGTERM handler. Core's chat
  EOF/KeyboardInterrupt/exit paths do reach its finally cleanup, but arbitrary
  process termination is not proven graceful.
- Cryosleep writes cycle count and last observation, but awakening restores only
  mission and stress. A fresh process therefore does not recover those counters.
- Startup before the chat `try/finally` is not fully covered by that cleanup.
  Runner exceptions are logged without a general finally-based running-state reset.

These limitations are distinct from the absent duplicate-instance defect and
were not repaired. No heartbeat algorithm, prompts, decision logic, tools,
goals, memory, or other architecture was changed.

## Files changed and minimal implementation change

- Added `tests/test_phase1b_single_brain.py`.
- Updated `tests/conftest.py` only to include the new filename in the existing
  offline runtime isolation fixture.
- Added this report.

**Production implementation change: none.** SHA-256 hashes of all 52 Python
files under `src/` matched before and after this task. Existing Phase 1A tests
and the two unrelated confirmed defects were left unchanged.

## Focused tests added

Ten collected cases:

1. Actual core ignition with normal `exit`.
2. Actual core ignition with EOF.
3. Actual core ignition with KeyboardInterrupt.
4. Supplied-instance runner path when signal registration succeeds.
5. Supplied-instance runner path when worker signal registration raises ValueError.
6. Real heartbeat stops after cryosleep on the same object.
7. Existing awaken/cryosleep restores mission/stress on the same object and
   repeated inactive cryosleep is a no-op.
8. Runner KeyboardInterrupt sleeps the supplied instance.
9. Registered signal handlers bind to the executed object and change its state.
10. Fresh core/brain imports do not construct or run a brain.

The ignition tests compile and execute the entire actual main-guard body from
the unchanged temporary source copy, in the imported core module's globals.
They do not reproduce the startup assignments in test code. A constructor spy
counts calls in both core and runner; captured thread arguments prove identity;
finite heartbeat state is checked before exit; the real cleanup writes its
cryosleep state into temporary storage. Threads, unrelated startup services,
terminal clearing, and input are mocked. These are controlled startup wiring
tests, not a full live `python -m` deployment or a concurrent scheduling test.

The real-heartbeat test fakes observation and sleep, stops after one iteration,
and has an escape guard if stopping regresses. No infinite heartbeat, live LLM,
API request, desktop action, real signal delivery, or user-data persistence is
performed. Phase 1A's temporary package/storage isolation remains in use.

## Complete verification results

Command from the nested repository root:

```powershell
python -m pytest tests -q -ra
```

| Suite | Passed | Failed/errors | Expected failures |
| --- | ---: | ---: | ---: |
| Existing Oracle/backtester tests | 12 | 0 | 0 |
| Existing Phase 1 state/routing tests | 24 | 0 | 0 |
| Phase 1A characterization tests | 47 | 0 | 2 |
| New Phase 1B tests | 10 | 0 | 0 |
| **Total** | **93** | **0** | **2** |

95 cases; no unexpected failures, ordinary skips, or unexpected passes. Runtime:
48.47 seconds. The 15 existing deprecation warnings remain. Relevant Phase 1A
module import smoke tests and the new Phase 1B import test are included in this
full run, not additional counts.

The two unchanged strict expected failures remain JSON-string brace extraction
and self-inspection returning the wrong source. An initial focused run found
three test assertion failures from including asyncio's own temporary signal
registrations; the assertions were corrected to inspect the brain runner's
registrations separately. No production issue was hidden or repaired.

## Behavior changes and stop condition

No production behavior changed. The current single-instance behavior is now
covered by focused startup and lifecycle regression tests. The historical
baseline remains preserved; this report records the verified correction to it.
Phase 1B ends here.
