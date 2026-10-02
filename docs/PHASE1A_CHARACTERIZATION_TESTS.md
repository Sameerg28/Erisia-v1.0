# Phase 1A characterization tests

Date: 2026-09-27. Scope: tests and test isolation only; no production refactoring.

## Checkout observed

The actual repository is the nested `Erisia-v1.0-main/` directory. The checkout
already differs from `ARCHITECTURE_BASELINE.md`: `GoalStore`, `MemoryStore`,
`ToolRuntimeContext`, the `manage_goal_stack` dispatch route, and support for an
injected brain instance already exist. `tests/test_phase1_state_routing.py`
also existed before this task. These files and behaviors were retained.

The baseline's missing `manage_goal_stack` route is **not reproducible in this
checkout**. Tests assert the current working route and its explicit error when
the runtime context has no goal handler. No expected failure was invented for
the historical missing route.

## Tests added and behaviors captured

Added `tests/test_phase1a_characterization.py`: 49 collected cases, including
parameterizations and two strict expected failures.

| Area | Captured behavior |
| --- | --- |
| Argument parsing | Dictionaries and JSON objects accepted; null, blank, malformed JSON, arrays, scalars, and unsupported input types become `{}` |
| Tool extraction | Plain text, fenced JSON, multiple objects, nested function blocks, argument aliases, malformed arguments, and missing names |
| Dispatch | Health checks, process termination, local execution, Docker execution, world-state callback, dynamic keyword arguments, and exact unknown-tool response; all action handlers mocked |
| Approval guard | Explicit approval reaches its mock; explanatory/question text is blocked |
| Goal tool | Working dispatch, unbound-handler error, empty view, validation, normalized action/text, add/view/complete lifecycle, and completed goal persistence |
| GoalStack | Actual structured `GoalStack` save/reload preserves the full stored record, including ID, rationale, priority, and history; focus/summary remain usable |
| Semantic memory | Actual `MemoryManager` initializes both Chroma collections, writes stable IDs/metadata, queries and reopens temporary storage; empty inputs and backend exceptions soft-fail |
| Episodic memory | Actual SQLite creation, empty state, writes, metadata, row count, last-N selection, and chronological presentation |
| Brain | Construction retains its injected core, starts idle, initializes counters, and creates no pulse/cryosleep files |
| Imports | Fresh imports of router, cognition, semantic memory, episodic memory, brain, and complete core from unmodified source copies with external boundaries stubbed |
| Chat | Actual `erisia_complete_brain` returns an exact mocked response, includes system injection and user input, updates history, and appends temporary training JSONL |
| Chat tools | Native and text-fallback calls dispatch with parsed arguments, feed results into the follow-up LLM request, log an episode, and return the mocked final answer |
| Failures | Dynamic exceptions become error strings; built-in exceptions propagate through the router; core converts raised tool exceptions to its current fallback reply |
| Retry limit | Returned error markers trigger four total executions (initial attempt plus three retries), then return `None`, reset the error counter, and leave history/training unwritten |

Added `tests/conftest.py` to isolate both the new runtime tests and the 24
pre-existing Phase 1 tests. Existing test bodies were not changed. The original
12 Oracle/backtester tests run normally with their existing temporary fixtures.

## Confirmed defects captured

Both tests use `xfail(strict=True)`: fixing either defect will produce an XPASS
failure until the expectation is deliberately updated.

1. `test_text_tool_extraction_with_literal_brace`: valid JSON whose argument
   contains the string `"{"` produces no extracted call. The brace scanner
   counts braces inside quoted strings rather than recognizing JSON quoting.
2. `test_self_inspection_returns_requested_core`: requesting `erisia_core.py`
   reads `erisia_execution.py`; the expected core function is absent.

The asymmetric handling of returned errors versus raised exceptions, and the
retry-exhaustion `None` return, are recorded as passing characterization tests,
without changing or presuming a new intended contract.

## Isolation and external dependencies mocked

- Each runtime test imports a fresh, byte-for-byte copy of the package's Python
  files under its own pytest temporary directory. Only Python source is copied;
  user databases, skills, configuration, credentials, and memory are not copied.
  This redirects paths derived from `__file__` before import-time initialization.
- Runtime environment overrides/API-key variables are cleared for each test;
  episodic storage and feature flags are set explicitly. Module-cache and path
  changes are restored after each test.
- LLM routing and client accessors are stubbed before core import. Chat tests
  install finite mocked responses. Tavily is absent, meta review is disabled,
  and no live LLM/API request is made.
- Voice and optional service startup are stubbed. Desktop calls and process
  creation are blocked; representative OS tools execute mocks only. Signal
  registration is mocked, and passive-cognition startup is blocked.
- Outbound socket connections/datagrams are blocked. Windows asyncio's internal
  wakeup socket pair is allowed for the existing mocked brain-thread tests;
  it is not an API connection.
- Telemetry initialization is stubbed. Chroma uses real temporary persistent
  databases with anonymized telemetry disabled. Its embedding function is a
  deterministic local four-component vector, with no model downloads. Clients
  sharing a Chroma system are shut down once per test.
- JSON goals, training JSONL, graph paths, skill directories, brain paths, and
  SQLite persistence all resolve to temporary test storage. Persistent user
  data is not used or modified.

## Test results

Final verification command, run from the nested repository root:

```powershell
python -m pytest tests -q -ra --junitxml=tests/.phase1a-results.xml
```

The temporary XML report was used to verify per-file counts and then removed.
For normal reruns, use `python -m pytest tests -q -ra`.

Environment: Windows, Python 3.14, Chroma 1.5.9; existing installed dependencies.

| Suite | Passed | Failed/errors | Expected failures |
| --- | ---: | ---: | ---: |
| Existing Oracle/backtester tests | 12 | 0 | 0 |
| Existing `test_phase1_state_routing.py` | 24 | 0 | 0 |
| New `test_phase1a_characterization.py` | 47 | 0 | 2 |
| **Total** | **83** | **0** | **2** |

85 collected cases; no ordinary skips or unexpected passes. Final run took
28.44 seconds and emitted 15 deprecation warnings from existing `utcnow()` use
and Chroma's `asyncio.iscoroutinefunction` use. No production change was made
to silence those warnings.

An initial harness run exposed duplicate Chroma shutdown and blocking of
Windows asyncio's internal socket pair. Both test-harness issues were corrected;
they are not production defects or remaining test failures.

## Limits: not safely exercised in this phase

- Actual API clients, LLM routing/failover, remote services, voice, desktop
  observation/actions, Docker execution, and optional service processes.
- Unmocked deployment/startup with user configuration. Core import smoke covers
  real core initialization under explicitly mocked external boundaries.
- Production embedding-model quality/downloads. Chroma storage/query mechanics
  are exercised, but deterministic test vectors do not establish semantic quality.
- Long-running cognition/maintenance loops, real shutdown signals, concurrent
  multi-process storage contention, and destructive corruption recovery.
- Full skill forge approval execution or REST/MCP/Telegram integration.

## Production source files modified

**None.** SHA-256 hashes of all 52 Python files under `src/` were recorded before
test execution and compared afterward; all were unchanged. No architecture
adapter was introduced, and `erisia_complete_brain` was not rewritten.

Deliverables are limited to the two new test files and this report. Phase 1A
ends here; no subsequent refactoring phase was performed.
