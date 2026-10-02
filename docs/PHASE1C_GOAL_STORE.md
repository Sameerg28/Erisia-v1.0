# Phase 1C: goal persistence boundary

Date: 2026-09-27. Scope: existing persistence facade safety only.

## Pre-change read/write map (recorded before production edits)

The current checkout already contains `erisia_goal_store.py`. GoalStack already
delegates `_load` and `_save` to its raw persistence functions. No second facade
or caller migration is needed. The historical architecture baseline is stale
on this point; the Phase 1A/1B reports correctly describe existing adapters.

| Path | Reads | Writes |
| --- | --- | --- |
| `GoalStore.load/save` | `load_goals_raw` | `save_goals_raw` |
| `GoalStack._load/_save` in `erisia_cognition.py` | `load_goals_raw` | `save_goals_raw`; called by add/update, decay, explicit purge |
| Existing GoalStore add/update/remove/complete/manage | Raw load or cached GoalStack | Raw save or GoalStack save |
| Core `_load_subconscious_goal_stack` | Store active title view | None |
| Core `_save_subconscious_goal_stack` | Store title view | Adds missing titles via existing `store.add`; no flat overwrite |
| Core `manage_goal_stack` | Store manage/view | Store manage/add/complete; emits existing events |
| Core goal counters and identity sync | Store load | None |
| Core context, audit finding ingestion, meta review | GoalStack focus/summary | GoalStack push/ingest |
| Core `initialize_advanced_cognition` | Store get_stack/bind_stack | Existing explicit `purge_corrupted_goals` can save on startup |
| Brain `_get_goal_stack_summary` | Store list | None directly; tool execution can reach core manage |
| Passive cognition `_process_batch` | GoalStack summary | GoalStack decay/ingest |
| Planner | No goal-file access; its `goal` is a plan intent | Writes separate plan files only |
| REST goals handler | Store get_stack/focus | None |
| MCP goals tool | Store get_stack/summary | None |
| MCP goals resource | Direct `Path.read_text` of configured goal file | None |
| Channel goals command | GoalStack focus via configured path | None |
| Audit, briefing, doctor, identity goal readers | `load_goals_raw` (some normalize into read-only views) | None |
| Tool router/runtime context | Calls bound core manage handler | Indirect only |
| `erisia_memory_manager.GOAL_STACK_FILE` | Unused path constant | None |

Repository Python-source search found no known direct goal JSON writers outside
`save_goals_raw`. Direct `open/json.load` resides in `load_goals_raw`; direct
`open/json.dump` and temporary replacement reside in `save_goals_raw` before
this phase. MCP's raw resource read is the remaining file-read bypass. Generic
arbitrary-code tools are not a constrained goal persistence API.

The observed production file was inspected read-only for shape: a top-level
list of 102 entries (16 dictionaries, 86 strings). Its structured records have
15 fields and active status; history entries have `ts`, `event`, and `source`.
No production content is copied into test fixtures.

## Authoritative schema and defaults

Authority: `GoalStack.add_or_update_goal` in `erisia_cognition.py`; canonical
serialization is a JSON array. Records are open dictionaries, not a closed
validated schema. Existing unknown fields, nested values, and list order survive
load/save. There is no new schema version or model conversion in this phase.

| Field | Existing meaning and creation default |
| --- | --- |
| `id` | UUID string generated when a goal is created |
| `title` | Required nonblank goal text; stripped on explicit creation |
| `rationale` | Explanation; empty string |
| `next_action` | Suggested next step; empty string |
| `priority` | Clamped 0..1 importance; `add_or_update_goal` default 0.55; `push` default 0.6 |
| `confidence` | Clamped 0..1 confidence; 0.5; ranking uses its inverse as uncertainty |
| `urgency` | Clamped 0..1 urgency; 0.5 |
| `progress` | Completion fraction; 0.0 on creation |
| `status` | `active` on creation; terminal statuses recognized as completed/done/cancelled/abandoned |
| `source` | Producer label; add/update default `unknown`, push default `manual` |
| `deadline` | Passed-through deadline value; default null |
| `created_at` | UTC timestamp at creation |
| `updated_at` | UTC timestamp at creation and explicit updates |
| `recency` | Ranking signal; 1.0 at creation/update; existing decay multiplies by 0.95 |
| `history` | Array of event dictionaries with `ts`, `event`, `source`; initially a `created` event |
| `completed_at` | Optional completion timestamp written by existing GoalStore.complete and read by GoalStack completion dedupe |

The observed records contain the first 15 fields. `completed_at` is supported
by implementation even though absent from that snapshot. History dictionaries
and records may carry extra fields; the serializer does not whitelist or strip
them. Legacy title aliases `goal`, `text`, and `name` are recognized by some
existing readers and retained as-is by persistence.

GoalStore's existing business methods have their own pre-existing defaults:
`add` delegates to GoalStack with priority 0.55, confidence/urgency 0.5, source
`manual` (manage/add supplies `manage_goal_stack`). These methods were not
introduced or expanded in Phase 1C.

## Legacy compatibility and error contract

The deterministic compatibility representation is **the original string**.
This matches the existing GoalStack's string-aware ranking and display paths.
Converting strings on load would change those semantics and invent state. No
priority, confidence, identifier, timestamps, or history are added by reading;
even string whitespace is retained. Structured records never become strings.
Mixed lists remain mixed until an existing explicit domain operation changes
an entry. Therefore Phase 1C prevents loss; it does not normalize the whole file.

Existing behavior retained:

- Missing file returns `[]` without creating directories or files.
- Empty/whitespace files and malformed JSON log a load warning and return `[]`.
  They remain byte-for-byte unchanged on read.
- Unsupported top-level values return `[]`. No additional entry validation is
  imposed; persistence is not a domain validator.
- Legacy dictionary envelopes containing a list under `goals`, `items`, or
  `stack` are read using the existing truthy-fallback precedence. An explicit
  save writes the canonical array; envelope-level metadata is not part of the
  returned goal list. Unknown fields **inside records** are retained.
- `load()` does not rewrite, repair, back up, purge, or auto-migrate anything.
- Save exceptions propagate from the facade. Existing GoalStack `_save` still
  catches/logs them; it does not roll back its in-memory changes.

For clarity, defaults on existing explicit string promotion are also unchanged:
GoalStack starts a migrated record with new UUID/current timestamps, empty
rationale/next action, priority/confidence/urgency 0.5, progress 0, active status,
caller source, recency 1, and a migrated history event, then applies its normal
update rules. Its migration branch has no default deadline field. Existing
GoalStore.update uses similar values with source `system`; complete uses
progress 1, completed status/time, recency 0, and migrated/completed history.
These defaults apply only to explicit operations, never to persistence loading.

## Minimal change and ownership

`erisia_goal_store.py` already owned serialization before this phase. Its
`load_goals_raw` / `save_goals_raw` functions remain the single implementation
used by GoalStore.load/save and GoalStack. No caller migration was performed.

The previous writer serialized into a shared `<goal-file>.tmp`, closed it, and
replaced the goal file. Although this protected the original against ordinary
serialization failure, it left partial temporary files and overwrote/consumed
an existing sidecar with that name. It did not explicitly sync before replacement.

The writer now:

1. Serializes the snapshot in memory before filesystem changes.
2. Creates a uniquely named sibling temporary file in the destination directory.
3. Writes UTF-8 JSON, flushes, and calls `os.fsync`.
4. Closes the temporary file before `os.replace` (including on Windows).
5. Cleans up only that call's temporary path on success or failure. A cleanup
   failure is logged without masking the original failure. No backups or
   pre-existing temporary files are deleted.

Existing per-path in-process locks remain. No new singleton state, decision
logic, ranking, planning, or schema fields were added. The already-existing
GoalStore business methods and singleton accessor are retained for compatibility;
splitting those responsibilities would exceed this phase.

The path contract is unchanged: explicit paths are honored, while the existing
`get_goal_store()` accessor uses its bound store or `get_config().paths.goal_stack_file`.
Core still passes its own configured `GOAL_STACK_FILE`; the common default is
`data/erisia_goal_stack.json`. Identity's separate environment/path resolver is
unchanged. Path ownership was not consolidated.

## GoalStore API and serialization format

The persistence API is unchanged:

| API | Contract |
| --- | --- |
| `GoalStore(path)` | Retains the supplied path; construction performs no file I/O |
| `store.load() -> list[Any]` | Returns structured records and legacy entries using the compatibility behavior above |
| `store.save(goals) -> None` | Persists the complete supplied snapshot; serialization/I/O errors propagate |
| `load_goals_raw(path) -> list[Any]` | Canonical reader used by the class and existing GoalStack integration |
| `save_goals_raw(path, goals) -> None` | Canonical serializer and atomic writer used by both paths |
| `get_goal_store(path=None)` | Existing explicit-path/configured-default accessor |
| `bind_goal_store(store)` | Existing runtime binding; unchanged |

Existing compatibility APIs also remain: `get_stack`, `bind_stack`, `list`,
`list_titles`, `get`, `add`, `update`, `remove`, `complete`, and `manage`.
Their domain behavior was not added or modified in this phase.

Writes serialize `list(goals)` as a UTF-8, top-level JSON array with two-space
indentation and `ensure_ascii=False`. Record fields and entry order are retained;
there is no wrapper, schema-version marker, field whitelist, or automatic string
promotion. Reads still use the existing JSON parser. An explicit save replaces
the full snapshot; it does not merge records with a newer on-disk snapshot.

GoalStack integration was already present: `_load` calls `load_goals_raw` and
`_save` calls `save_goals_raw`. No GoalStack source changes were needed. The
integration regression test checks delegation, no write on construction, field
round-trip preservation, and unchanged summary output after reloading.

## Tests and fixture

Added `tests/test_phase1c_goal_store.py` with 30 cases covering missing/empty/
malformed input, all structured fields, multiple goals, optional completed state,
unknown nested fields, strings, mixed lists, three legacy envelopes, repeat saves,
configuration selection, and integration through the existing GoalStack boundary.

Failure injection covers serialization (including circular input), partial
write, flush, fsync, and replacement. Tests assert that previous valid bytes
survive and temporary files are removed. Replacement is inspected to confirm
it sees complete JSON in a synced sibling file while the original is still
intact. Existing fixed-name sidecars and backups must survive untouched.

`tests/fixtures/phase1c_mixed_goals.json` is synthetic: it mirrors the observed
structured/string shape and history keys, with an additional completed record
and unknown nested extension to exercise supported round trips. It contains
no copied production goal text or IDs.

The existing isolated runtime fixture now includes Phase 1C. A read-only,
session-wide SHA-256 guard checks the real production goal file before and
after the whole suite. Runtime/persistence tests use temporary paths only;
no actual goal file is passed to a store or cognition object. The guard also
has a focused test. No auto-migration or production application startup ran.

## Boundaries and Phase 1D follow-up

There are **no known remaining direct goal-file writers outside this module**
in current repository Python code. This differs from the suggested expected
answer because many callers were already migrated before Phase 1C. MCP's
`erisia://goals` resource still reads raw text directly; it remains unchanged
for Phase 1D consideration. Audit/briefing/doctor/identity call the canonical
module functions directly rather than the class, but do not duplicate I/O.

Safe replacement is not a multi-process transaction or stale-snapshot merge.
Independent cached GoalStacks may still overwrite each other's logically newer
snapshots. The path locks are process-local. Power-loss durability of directory
metadata is filesystem/platform-dependent; no portable directory fsync or
backup/recovery policy is introduced. Large snapshots require serialization
memory. These are documented limits, not claims of full transactional storage.

Malformed reads still return `[]`; a later explicit save can overwrite the
malformed file. Recovery policy and fail-closed domain operations were not
redesigned. Core's pre-existing `initialize_advanced_cognition` explicitly calls
`purge_corrupted_goals` and may save when matching goals exist; this phase does
not introduce or invoke that behavior on production data. Constructing a
GoalStore or GoalStack alone does not write. Revisiting startup purge, existing
business methods, cached-state ownership, and caller policies belongs to a
separately scoped phase.

## Files changed

- Production: `src/erisia/erisia_goal_store.py` only (writer hardening and contract docstrings).
- Tests: `tests/conftest.py`, new `tests/test_phase1c_goal_store.py`, new synthetic JSON fixture.
- Documentation: this report, whose read/write map was recorded before production edits.

GoalStack source/external behavior, core helpers, brain, planner, APIs, router,
memory, identity, and skill code are unchanged. Successful saves preserve the
same JSON data model and existing goal semantics. Only failure handling,
temporary-file ownership, and sync-before-replace behavior changed.

## Verification results

Command from the nested repository root:

```powershell
python -m pytest tests -q -ra
```

| Suite | Passed | Failed/errors | Expected failures |
| --- | ---: | ---: | ---: |
| Existing Oracle/backtester | 12 | 0 | 0 |
| Existing Phase 1 state/routing | 24 | 0 | 0 |
| Phase 1A | 47 | 0 | 2 |
| Phase 1B | 10 | 0 | 0 |
| Phase 1C | 30 | 0 | 0 |
| **Total** | **123** | **0** | **2** |

125 cases; no ordinary skips or unexpected passes. Final run: 65.53 seconds,
16 deprecation warnings (existing UTC timestamp and Chroma asyncio usage).
The two strict expected failures remain tool JSON-string brace extraction and
self-inspection's incorrect source mapping; neither was repaired.

The initial pre-change Phase 1C run had 20 passes and 10 failures: four exposed
temporary-file side effects, five targeted the new fault-injection/write boundary
not yet implemented, and one was a test trying to mutate frozen configuration.
That test now substitutes a configured-path accessor instead. One full-suite
attempt terminated with Windows interruption status before reporting results;
the complete rerun above is the verification result.

The suite-wide goal-file guard passed. A separate before/after hash comparison
over all 52 production Python files and the production goal JSON confirmed that
only `src/erisia/erisia_goal_store.py` changed. Production goal bytes are unchanged;
no production migration, backup deletion, or goal-model conversion occurred.

Phase 1C stops here. No Phase 1D migration or unrelated repair was performed.
