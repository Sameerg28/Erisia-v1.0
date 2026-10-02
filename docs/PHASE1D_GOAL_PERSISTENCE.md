# Phase 1D: goal persistence migration

## Pre-migration inventory

Recorded from the current checkout before production edits. GoalStore already
owns all known goal-file writes. Its module-level persistence functions are
part of that boundary; using them does not bypass the facade implementation.

### Readers

| Module:function/class | Access path |
| --- | --- |
| `erisia_goal_store:load_goals_raw` | Direct exists/open/json.load; canonical reader |
| `erisia_goal_store:GoalStore.load/list/list_titles/get/update/remove/complete/manage` | Canonical load, or existing GoalStack operations |
| `erisia_cognition:GoalStack._load` | `load_goals_raw(self.path)` |
| `erisia_core:_load_subconscious_goal_stack` | `get_goal_store(explicit_path).list_titles(active_only=True)` |
| `erisia_core:_save_subconscious_goal_stack` | Store list_titles before adding missing titles |
| `erisia_core:_count_completed_goals/_count_abandoned_goals/_count_stale_goals/_count_total_goals` | Store load; `_sync_identity_goal_consistency` consumes these counts |
| `erisia_core:manage_goal_stack` | Store manage; view returns active titles |
| `erisia_core:build_goal_context_text/_ingest_audit_findings_into_goals` | GoalStack focus_snapshot |
| `erisia_core:initialize_advanced_cognition` | Store get_stack/bind_stack |
| `erisia_core:background_daemon_loop` | Legacy wrapper reads and core manage; no direct goal I/O |
| `brain_loop:ErisiaBrain._get_goal_stack_summary` | Default store list(active_only=True), used by observe |
| `erisia_cognition:PassiveCognitionEngine._process_batch` | Injected GoalStack summary_text |
| `erisia_server:create_app.goals` | Default store get_stack/focus_snapshot |
| `erisia_mcp:create_mcp_server.call_tool` (`erisia_goals`) | Default store get_stack/summary_text |
| `erisia_mcp:create_mcp_server.read_resource` (`erisia://goals`) | **Bypass:** configured path.exists/read_text; preserves raw JSON text |
| `erisia_channels:TelegramChannel._cmd_goals` | GoalStack(configured_path).focus_snapshot |
| `erisia_audit:SelfAuditEngine._audit_goals` | load_goals_raw(GOAL_STACK_PATH) |
| `erisia_briefing:MorningBriefingGenerator._collect_active_goals` | load_goals_raw(GOAL_STACK_PATH) |
| `erisia_doctor:_check_goal_stack` | **Metadata bypass:** gs_file.exists; content through load_goals_raw |
| `erisia_self:IdentityLayer._read_goal_stack_records` | load_goals_raw(resolved_path), used by _refresh_goal_analytics |

### Writers

| Module:function/class | Access path |
| --- | --- |
| `erisia_goal_store:save_goals_raw` | Sole JSON serializer / temp-file writer / fsync / replace |
| `erisia_goal_store:GoalStore.save/update/remove/complete` | save_goals_raw, directly or through self.save |
| `erisia_goal_store:GoalStore.add/manage` | Existing GoalStack.add_or_update_goal or store.complete |
| `erisia_cognition:GoalStack._save` | save_goals_raw(self.path, self.goals) |
| `erisia_cognition:GoalStack.add_or_update_goal/push/decay/ingest_goal_candidates/purge_corrupted_goals` | _save, directly or through add/update |
| `erisia_core:_save_subconscious_goal_stack` | Existing store.add compatibility wrapper; preserves existing records |
| `erisia_core:manage_goal_stack` | Existing store.manage wrapper |
| `erisia_core:_ingest_audit_findings_into_goals` | GoalStack.push |
| `erisia_core:erisia_complete_brain` | GoalStack.ingest_goal_candidates from meta review |
| `erisia_core:initialize_advanced_cognition` | Existing explicit GoalStack.purge_corrupted_goals |
| `erisia_core:background_daemon_loop` | Core manage and audit-ingestion helpers |
| `erisia_cognition:PassiveCognitionEngine._process_batch` | GoalStack.decay and ingest_goal_candidates |
| `erisia_tool_router:_execute_tool_call` / brain execute | Bound core manage handler for goal tool; no direct I/O |

Planner's `Plan.goal` is injected intent and its file writes concern separate
plan JSON, not goal-stack persistence. `erisia_daemon.DaemonManager` holds an
in-memory mission queue, not goal-file storage. Neither requires migration.
REST/MCP goal views and channel commands do not directly write goal state.

### Goal path definitions

- `erisia_config.PathConfig.goal_stack_file`: default data/erisia_goal_stack.json.
- Core `_get_erisia_paths`: GOAL_STACK_FILE plus derived GOAL_STACK_PATH and
  compatibility module constants; explicit path passed into its bound store.
- Audit/briefing: module GOAL_STACK_PATH under project data.
- Doctor: gs_file derived from its base_dir argument.
- Identity `_resolve_goal_stack_path`: ERISIA_GOAL_STACK_FILE override or path
  relative to the identity database directory.
- Legacy memory-manager GOAL_STACK_FILE: unused constant.
- MCP/REST/channels use existing configured/default-store paths.

These path contracts remain authoritative for their callers; migration will
not silently consolidate configuration or change the selected file.
