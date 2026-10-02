# Erisia Architectural Baseline

**Date:** 2026-09-25  
**Scope:** Runtime behavior of Erisia v1.0 as implemented in this checkout  
**Method:** Code-path tracing (imports, call sites, persistence I/O). No architecture rewrite was performed.  
**Repo root (actual):** `Erisia-v1.0-main/` (nested under the outer zip folder of the same name)

---

## Legend — evidence classes

Throughout this document:

| Tag | Meaning |
|-----|---------|
| **IMPLEMENTED** | Code path exists and is reachable from a real entry point |
| **CLAIMED** | README / module docstring / comment asserts capability |
| **INTENDED / UNWIRED** | Code exists but is not started, not called, or missing a producer/consumer link |
| **STUB** | Explicitly incomplete (prints “not implemented”, returns early, placeholder `None`) |
| **COMPETING** | Two+ writers/readers for the same conceptual state |

Do not treat names (`MemoryManager`, `IdentityLayer`, `CausalReasoningEngine`) as proof of capability.

---

## Executive snapshot

Erisia is a **monolithic Python agent** centered on `src/erisia/erisia_core.py`. Interactive chat (`erisia_complete_brain` + LLM tool loop) is the primary cognition path. Several background threads run in parallel (brain loop, passive cognition, world-state tracker, episodic maintenance). A second skill/memory/API surface (`erisia_skills`, `erisia_memory_manager`, FastAPI, MCP) exists as a **sidecar** that does **not** share the full chat/tool brain.

README’s “five-layer memory / subconscious daemon / multi-LLM / EventBus telemetry” is **partly true, partly duplicated, partly unwired**.

---

# A. Runtime entry-point map

| Entry | Path | How it boots | Wired to full brain? |
|-------|------|--------------|----------------------|
| **Primary agent** | `src/erisia/erisia_core.py` `__main__` | Clear screen → `initialize_advanced_cognition()` → WorldState + PassiveCognition + Brain thread + episodic thread → voice or `input()` → `erisia_complete_brain()` | **Yes** |
| Brain-only | `src/erisia/brain_loop.py` `__main__` | `_run_brain_loop()` without core module | Partial (direct dispatch fallback) |
| REST API | `src/erisia/erisia_server.py` `__main__` | `create_app()` + uvicorn `:8420` | **No** — `query_handler=None` → `/ask` returns 503 |
| MCP server | `src/erisia/erisia_mcp.py` `__main__` | `create_mcp_server()` stdio | **No** — `erisia_ask` = bare `query_llm([{user}])` |
| MCP SSE | same file | transport flag | **STUB** — prints “not yet implemented” |
| FS MCP sidecar | `erisia_fs_mcp.py` | async stdin JSON-RPC loop | Isolated filesystem tools |
| Think MCP sidecar | `erisia_think_mcp.py` | async stdin JSON-RPC loop | Isolated thinking state |
| MCP bridge smoke | `erisia_mcp_bridge.py` `__main__` | Init child MCP connections | Client only |
| Runtime services | `erisia_runtime_services.py` | Optional MCP `Popen` + voice | Called from core import path |
| Voice smoke | `erisia_voice.py` `__main__` | TTS/STT check | Standalone |
| Doctor CLI | `erisia_doctor.py` `__main__` | Health diagnostic | Read-only probes |
| Graph smoke | `erisia_graph.py` `__main__` | Sample triples | Standalone |
| MiroFish probe | `erisia_mirofish_bridge.py` `__main__` | `/health` | Standalone |
| Reasoning smoke | `erisia_reasoning_engine.py` `__main__` | Demo BFS | Standalone |
| Oracle CLIs | `src/oracle/oracle_*.py` | Independent trading stack | Also phrase-launched from core |
| Backtester | `backtester.py` | Standalone + lazy import from core | Trading only |
| Tests | `tests/test_*.py` | pytest | **Oracle/backtester only** — not agent runners |

**Not started by core despite existing modules:**

- `auto_setup_channels()` (`erisia_channels.py`) — Telegram — **UNWIRED**
- `MorningBriefingGenerator` (`erisia_briefing.py`) — **orphaned** (no imports found)
- `erisia_sandbox.py` — **orphaned** (execution uses `src/sandbox` instead)
- `background_daemon_loop()` — **defined, not started** (`daemon_thread` remains `None`; comment says brain replaces it)

---

# B. Control-loop map

| Loop | Location | Started by | Cadence | Reads | Writes | Status |
|------|----------|------------|---------|-------|--------|--------|
| Conscious chat | `erisia_core.__main__` `while True` | Human | Interactive | Voice/stdin, identity, goals, memory | Episodes, training JSONL, tools | **IMPLEMENTED** |
| Tool/LLM cycle | `erisia_complete_brain` inner `while True` | Chat turn | Until text reply | Chat history, tools, LLM | Tool results into history | **IMPLEMENTED** |
| Brain OTEL | `ErisiaBrain.heartbeat` | Thread via `_run_brain_loop` | 5s active / 60s idle | Mission file, goals, psutil, pending skills | Pulse log, cryosleep JSON, memory/graph via core | **IMPLEMENTED** (parallel instance issue — see risks) |
| Passive cognition | `PassiveCognitionEngine._loop` | `passive_cognition_engine.start()` | Default 90s (`ERISIA_PASSIVE_COGNITION_INTERVAL`) | Event queue, Chroma, GoalStack | Chroma, goals, journal MD, graph, consciousness | **IMPLEMENTED** |
| World state | `WorldStateTracker._run_loop` | `world_state_tracker.start()` | Poll/write intervals | Win32 foreground + cursor | `data/erisia_world_state.json` | **IMPLEMENTED** |
| Episodic maintenance | `episodic_memory_maintenance_loop` | Daemon thread | 300s | `erisia_memory.db` | Consolidation via Groq | **IMPLEMENTED** |
| Legacy mission daemon | `background_daemon_loop` | — | — | Missions, goals | Mission reports, goals | **UNWIRED** (not started) |
| MCP bridge reader | `MCPClientConnection._read_messages` | If MCP enabled | Continuous | Child stdout | In-memory messages | Conditional |
| FS/Think MCP | `while True` stdin | Separate processes | Continuous | stdin | stdout | Sidecar |
| Telegram | `TelegramChannel` thread | `auto_setup_channels` | Continuous | Bot API | Chat replies | **UNWIRED** |
| Optional MCP process | `start_optional_mcp_service` | `ERISIA_ENABLE_MCP=1` | Process lifetime | — | Sidecar MCP | Conditional |
| Oracle ingest | `oracle_*` | Phrase / CLI | Async when Oracle runs | Market APIs | `oracle_memory.db` | Separate subsystem |

`DaemonManager` is **not** a background loop — it is an in-memory mission/step queue injected into prompts and tools.

---

# C. State ownership map

| State concept | Canonical (intended) | Actual owner(s) | Competing? |
|---------------|----------------------|-----------------|------------|
| Chat working memory | In-process `chat_history` in `erisia_complete_brain` | `erisia_core` | No |
| Erisia persona ROM | `config/Erisia_Consciousness.md` | `IdentityManager` + `update_consciousness` | Partial — also prompt string in core |
| User psychological model | SQLite on `oracle_memory.db` | `IdentityLayer` (`erisia_self`) | Stress also in `IdentityManager` and `ErisiaBrain` |
| Semantic memory | Chroma `data/erisia_memory/` | `MemoryManager` (core) **and** `erisia_memory_manager` module globals | **Yes — dual clients** |
| Episodic memory | SQLite `erisia_memory.db` | `erisia_episodic_memory` | No |
| Relational memory | NetworkX + `data/erisia_relational_memory.json` | `ErisiaGraphMemory` | Legacy copy under `src/erisia/` |
| Goals | `data/erisia_goal_stack.json` | `GoalStack` (dicts) **and** `_load/_save_subconscious_goal_stack` / `manage_goal_stack` (string list) | **Yes — format corruption risk** |
| Skills registry | `skills/_registry.json` | `SkillRegistry` in **both** core and `erisia_skills` | **Yes — duplicated code** |
| Skill code | `skills/*.py`, `skills/pending/` | Core forge path (chat); `erisia_skills` (MCP/REST) | Dual APIs |
| World state | `data/erisia_world_state.json` | `WorldStateTracker` | No |
| Heuristics | `data/erisia_heuristics.json` | Core helpers **and** `erisia_memory_manager` | Dual writers |
| Telemetry | `data/erisia_telemetry.db` | `TelemetryStore` via EventBus | **Producers mostly missing** |
| Training data | `data/erisia_training_data.jsonl` | Appended by `erisia_complete_brain` | Reader: `LearningLoop` only |
| Plans | `data/plans/plan_*.json` | `PlanningEngine` | No |
| Brain stress / mission | `data/cryosleep_state.json`, pulse log | `ErisiaBrain` instance in thread | Main also constructs unused instance |
| Missions inbox | `config/erisia_missions.txt` | External / user; brain observes; legacy daemon would execute | Brain does not run full `execute_autonomous_mission` |
| In-memory mission queue | `DaemonManager` | Core globals | No persistence |
| Config paths | `erisia_config.PathConfig` | Claimed SSOT | Core still uses `_get_erisia_paths()` |

---

# D. Persistence / storage map

| Store | Path | Format | Primary writers | Primary readers |
|-------|------|--------|-----------------|-----------------|
| Chroma semantic | `data/erisia_memory/` | Chroma PersistentClient | `MemoryManager`, PassiveCognition, `memory_manager`, skill indexer | Core prompts, MCP search, skill retrieval |
| Episodic | `erisia_memory.db` (or `ERISIA_EPISODIC_DB_PATH`) | SQLite | `log_episode`, `prune_and_reflect` | `get_recent_context` → prompts |
| Graph | `data/erisia_relational_memory.json` (+ legacy `src/erisia/...`) | JSON NetworkX | Graph API, passive, tools | Reasoning, prompts, brain learn |
| Goals | `data/erisia_goal_stack.json` | JSON (mixed dict/string) | GoalStack, manage_goal_stack, audit ingest | Passive, brain, audit, server, MCP, identity sync |
| Heuristics | `data/erisia_heuristics.json` | JSON list | Core / memory_manager | Prompt injection |
| Consciousness | `config/Erisia_Consciousness.md` | Markdown | `update_consciousness` | IdentityManager, MCP resource |
| World state | `data/erisia_world_state.json` | JSON | WorldStateTracker | `get_world_state` tool |
| Training | `data/erisia_training_data.jsonl` | JSONL | Chat success paths | LearningLoop |
| Plans | `data/plans/` | JSON | PlanningEngine | Core “show plans” phrases |
| Skills registry | `skills/_registry.json` | JSON | SkillRegistry | Server/MCP/load |
| Skills code | `skills/`, `skills/pending/` | `.py` | Forge/approve | Dynamic load |
| Telemetry | `data/erisia_telemetry.db` | SQLite | TelemetrySubscriber (if events fire) | Learning, doctor, APIs |
| Cryosleep | `data/cryosleep_state.json` | JSON | Brain | Brain awaken |
| Pulse | `data/erisia_pulse.log` | Text log | Brain | Diagnostics |
| Missions | `config/erisia_missions.txt` | Text | User/external | Brain observe; legacy daemon |
| Reports | `reports/...` | Markdown | Missions, passive journals, audits | Audit/briefing (if used) |
| Oracle DB | `oracle_memory.db` | SQLite | Oracle + IdentityLayer tables | Oracle, audit, identity |
| Portfolio | `config/portfolio.json` | JSON | Manual / Oracle | Oracle/briefing |
| Backtests | `data/backtest_results/` | JSON/CSV/HTML | backtester | Cognition ingest hooks |

No pickle usage found under `src/erisia/`. In-memory caches: skills TTL (~30s) in core; Ollama health cache in LLM router.

---

# E. Dependency graph (runtime-relevant)

```
erisia_core
 ├─ erisia_memory (MemoryManager)
 ├─ erisia_identity (IdentityManager)
 ├─ erisia_graph + erisia_reasoning_engine
 ├─ erisia_cognition (GoalStack, PassiveCognition, Journal)
 ├─ erisia_episodic_memory
 ├─ erisia_world_state
 ├─ erisia_daemon
 ├─ erisia_tool_definitions
 ├─ erisia_system_tools → voice, mirofish_bridge
 ├─ erisia_execution
 ├─ erisia_tool_router  ←── lazy import back into erisia_core  [CYCLE]
 ├─ brain_loop  ←── tool_router ←── core  [CYCLE]
 ├─ erisia_llm ← erisia_config
 ├─ erisia_events → erisia_telemetry
 ├─ erisia_runtime_services → mcp_bridge, voice
 └─ lazy: erisia_self, erisia_planner, erisia_audit, backtester, oracle

erisia_skills → erisia_core (path constants) + erisia_memory_manager + erisia_llm
erisia_mcp → llm, memory_manager, GoalStack, skills, doctor, telemetry, learning, bridge
erisia_server → doctor, GoalStack, skills, telemetry, learning, events  (no core brain)
erisia_channels → GoalStack, doctor, learning, telemetry  (not started from core)
```

---

# F. Circular dependency map

| Cycle | Mechanism | Mitigation today | Risk |
|-------|-----------|------------------|------|
| `brain_loop` → `erisia_tool_router` → `erisia_core` → `brain_loop` | Top-level imports + lazy import inside `_execute_tool_call` | Lazy import in router | Fragile import order; hard to unit-test router/core isolation |
| `erisia_tool_router` ↔ `erisia_core` | Core imports router at top; router imports core inside function | Deferred import | Same |
| `erisia_skills` → `erisia_core` | Skills imports path constants from core | Works if core already loading | MCP forge path couples skills to monolith |

Lazy `importlib` is used for `erisia_self`, `erisia_planner`, `erisia_audit` specifically to avoid cycles.

---

# G. Duplicate implementation map

| Concern | Implementation A | Implementation B | Notes |
|---------|------------------|------------------|-------|
| Chroma access | `erisia_memory.MemoryManager` | `erisia_memory_manager` module globals | Same directory; two clients |
| Skill forge/load/approve | Large inlined copy in `erisia_core` | `erisia_skills.py` | Chat uses A; MCP/REST use B |
| `SkillRegistry` | Class in core | Class in `erisia_skills` | Duplicated |
| Heuristics R/W | Core `_write_heuristics_file` / `save_heuristic_rule` | Same names in memory_manager | Dual |
| Goal stack | `GoalStack` (structured) | `manage_goal_stack` + subconscious helpers (flat strings) | **Same file; mixed on disk** |
| Identity / stress | `IdentityManager` | `IdentityLayer` | Different domains (persona vs user) but both “identity” |
| Autonomy stress | also `ErisiaBrain.stress_level` | — | Third stress variable |
| Background autonomy | `background_daemon_loop` (dead) | `ErisiaBrain` + `PassiveCognitionEngine` | Incomplete replacement |
| Sandbox | `erisia_execution` → `src/sandbox` | `skills/sandbox`; unused `erisia_sandbox.py` | Three concepts |
| Path config | `erisia_config.PathConfig` | `erisia_core._get_erisia_paths()` | Dual SSOT claim |
| Graph file | `data/...json` | legacy `src/erisia/...json` | Resolver prefers data/ |

---

# H. Read/write paths

## H.1 Memory

| Layer | Canonical representation | Creates | Reads | Writes | Persisted | Competing |
|-------|--------------------------|---------|-------|---------|-----------|-----------|
| Volatile | `list` chat_history | `erisia_complete_brain` | Same | Same | No | — |
| Semantic | Chroma collections `erisia_knowledge`, `erisia_tools` | Core MemoryManager init | Core prompts, MCP (`memory_manager`), skills | Core, PassiveCognition, memory_manager, skill indexer | `data/erisia_memory/` | Dual clients |
| Relational | NetworkX DiGraph | Core init | Reasoning, prompts, brain | Tools, passive, brain learn | JSON graph file | Legacy path |
| Episodic | SQLite rows | `log_episode` | `get_recent_context` | maintenance prune | `erisia_memory.db` | — |
| CLAIMED “five-layer hierarchy” | README | — | — | — | — | Layers exist but are not a single owned hierarchy |

**Who assumes ownership:** Core assumes `memory_system` is the live agent memory. MCP/skills often assume `erisia_memory_manager`.

## H.2 Identity

| Aspect | Canonical | Creates | Reads | Writes | Persisted | Competing |
|--------|-----------|---------|-------|--------|-----------|-----------|
| Erisia persona | Consciousness MD + `IdentityManager` | Core init | Prompts, MCP resource | `update_consciousness` | `config/Erisia_Consciousness.md` | Also hardcoded system prompt in core |
| User model | `IdentityLayer` tables | Core `__main__` | Phrase “who am I”, sync | `observe_interaction` | `oracle_memory.db` (+ optional Chroma `erisia_identity`) | Name collision with IdentityManager |
| Stress | Three floats | Various | Prompts / brain | IdentityManager, IdentityLayer, Brain | Mostly RAM (+ identity DB) | **COMPETING** |

**CLAIMED (README):** “Identity Layer” = user psychological modeling — that matches `erisia_self.IdentityLayer`, **not** `erisia_identity.IdentityManager`.

## H.3 Goals

| Path | Representation | Writers | Readers | Status |
|------|----------------|---------|---------|--------|
| GoalStack | list of dicts (id, title, priority, status, history…) | PassiveCognition, audit ingest, server/MCP GoalStack ctor | Passive, brain observe, channels, MCP, server | **Rich model** |
| manage_goal_stack / subconscious helpers | list of strings | Tool (if routed), legacy daemon | Legacy daemon, counts sync | **Flat model** |
| On-disk file today | **Mixed** dicts + strings | Both | Both (normalize differently) | **Corruption risk IMPLEMENTED in data** |

**Critical gap:** `manage_goal_stack` is in `base_tools` and defined in core, but **`erisia_tool_router` does not route it** → runtime “Unknown tool” if the LLM calls it.

## H.4 Skills

| Path | Creates | Loads | Executes | Persists |
|------|---------|-------|----------|----------|
| Chat path | Core `forge_pending_skill` → pending | Core `load_dynamic_skills` | Router → `runtime_dynamic_skills` or builtins | `skills/`, `_registry.json`, Chroma tools |
| MCP/REST path | `erisia_skills.forge_pending_skill` | Server approve/reject | Not the chat loop | Same dirs |

## H.5 World state

| | |
|--|--|
| Canonical | JSON snapshot from `WorldStateTracker` |
| Creates | Tracker thread |
| Reads | `get_world_state` / prompt context builders |
| Writes | Tracker (poll/write intervals); optional heavy dump if `ERISIA_ENABLE_HEAVY_UI_DUMP` |
| Competing | None found |

## H.6 Telemetry

| | |
|--|--|
| Canonical | SQLite via `TelemetryStore` |
| Design | Subscribe to `INFERENCE_*` on EventBus |
| Actual | `query_llm` **does not emit** inference events |
| Result | Store stays empty unless something else emits; learning/telemetry APIs mostly empty |
| Core does emit | `SYSTEM_STARTUP`, some goal/skill/mission events |

**CLAIMED (core comment):** “Telemetry records every inference call” — **false in current `query_llm`**.

## H.7 Training data

| | |
|--|--|
| Canonical | Append-only JSONL lines |
| Writes | `erisia_complete_brain` success paths (`TRAINING_DATA_FILE`) |
| Reads | `LearningLoop.analyze()` (offline pattern extraction) |
| Not | A closed training / fine-tune loop |

---

# I. Tool execution paths

### Primary interactive path (IMPLEMENTED)

```
user input
  → erisia_complete_brain
    → optional OS autopilot / phrase routers (Oracle, planner, audit, identity report)
    → load_dynamic_skills(base_tools, query)
    → query_llm(..., tools=runtime_tools)
    → for each tool_call:
         _parse_tool_arguments
         _execute_tool_call(name, args, user_input, runtime_dynamic_skills)
            → builtins (system_tools / execution / core helpers / graph / voice / mirofish)
            → or runtime_dynamic_skills[name](**args)
    → log_episode / passive publish / meta review / training append
```

### Autonomous brain path (IMPLEMENTED)

```
ErisiaBrain.think → JSON {action, args}
  → _execute_tool_call(action, args, "autonomous brain loop")
  → execute_local_os_command blocked off main thread (firewall)
```

### Planner path (IMPLEMENTED, phrase-triggered)

```
should_plan keywords → PlanningEngine.execute_plan
  → custom step executor (Tavily / file / LLM / forge) — NOT full tool router
```

### MCP path (IMPLEMENTED but incomplete brain)

```
erisia_ask → query_llm only (no tools, no persona ROM injection matching chat)
erisia_forge_skill → erisia_skills (not core forge)
erisia_search_memory → erisia_memory_manager (not MemoryManager instance)
```

### Skill forge approval path

```
forge_pending_skill → skills/pending/
  → user approve_skill (strict intent check in router)
  → move to skills/ → next load_dynamic_skills
```

---

# J. Background-thread / process lifecycle

On `erisia_core` `__main__` start:

1. **Module import time:** EventBus + telemetry init; MemoryManager; IdentityManager; Graph; optional runtime services (MCP/voice if env).
2. `initialize_advanced_cognition()` → GoalStack + Journal + PassiveCognitionEngine (not started yet).
3. `WorldStateTracker.start()` → daemon thread.
4. `passive_cognition_engine.start()` → daemon thread.
5. IdentityLayer + PlanningEngine constructed on main (no threads).
6. `ErisiaBrain(...)` constructed on main **and** `_run_brain_loop` creates a **second** `ErisiaBrain` in the worker thread; only the second runs `heartbeat()`. Cryosleep signal wiring on the first instance does not stop the live loop.
7. Episodic maintenance daemon thread.
8. Main thread: voice listen or stdin forever.
9. On exit/`shutdown_event`: chat loop breaks; daemon threads are daemons (may die with process).

Optional: `Popen(-m erisia.erisia_mcp)` if `ERISIA_ENABLE_MCP=1`.

---

# K. Configuration / path ownership

### Claimed SSOT

`erisia_config.get_config()` — LLM keys, `PathConfig`, feature flags. Docstring claims it eliminates duplicated key loaders.

### Still authoritative for core chat paths

`erisia_core._get_erisia_paths()` and module-level path constants (`MEMORY_DIR`, `GOAL_STACK_FILE`, etc.).

### Important environment variables (non-exhaustive)

`GROQ_API_KEY`, `TOGETHER_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `ERISIA_PREFER_LOCAL`, `ERISIA_ENABLE_META_REVIEW`, `ERISIA_ENABLE_HEAVY_UI_DUMP`, `ERISIA_PASSIVE_COGNITION_INTERVAL`, `ERISIA_MAX_DYNAMIC_TOOLS`, `ERISIA_MISSION_FILE`, `ERISIA_CONSCIOUSNESS_FILE`, `ERISIA_REPORT_DIR`, `ERISIA_WORLD_STATE_*`, `ERISIA_EPISODIC_DB_PATH`, `ERISIA_GRAPH_FILE`, `ERISIA_ENABLE_MCP`, `ERISIA_MCP_TRANSPORT`, `ERISIA_ENABLE_VOICE`, `ERISIA_MIROFISH_*`, `TELEGRAM_*`, `ALPACA_*`.

Keys also load from `config/api.txt` / `api.txt` via `_load_api_key`. `.env` expected at `config/.env` (not present in this checkout).

`BASE_DIR` = `Path(__file__).resolve().parents[2]` for modules under `src/erisia` → project root.

---

# Known-module dossier (requested files)

| Module | What code actually does | Ownership claim | Gaps vs name/docs |
|--------|-------------------------|-----------------|-------------------|
| `erisia_core.py` | Monolith: paths, prompts, skills forge (inlined), chat brain, mission logic, ignition | Owns live runtime globals | Too large; duplicates skills/heuristics/goals |
| `brain_loop.py` | Async Observe–Think–Execute–Learn | Claims to replace daemon | Does not run `execute_autonomous_mission` / Mission_Reports; double instance |
| `erisia_cognition.py` | Salience, GoalStack, Journal, PassiveCognition | Owns rich goals + passive loop | Competes with flat goal helpers |
| `erisia_memory.py` | Class-based Chroma for core | “Episodic and Semantic” in docstring | Class is **semantic/tools only**; episodic is elsewhere |
| `erisia_memory_manager.py` | Module-level Chroma + heuristics | Used by skills/MCP | Duplicate of MemoryManager |
| `erisia_episodic_memory.py` | SQLite episodes + LLM prune | Episodic layer | Separate from MemoryManager |
| `erisia_graph.py` | NetworkX relational JSON | Relational memory | Shared with “causal” engine |
| `erisia_self.py` | User IdentityLayer | User psychology | Lazy-loaded; stores on oracle DB |
| `erisia_identity.py` | Persona ROM reader + stress float | Erisia persona | Not the README “Identity Layer” |
| `erisia_tool_router.py` | Central `_execute_tool_call` | Tool dispatch SSOT | Missing `manage_goal_stack`; cycles with core |
| `erisia_skills.py` | Extracted forge/load/approve | Skills SSOT for APIs | Not used by core chat path |
| `erisia_planner.py` | Multi-step PlanningEngine | Planning | Custom executor, not tool router |
| `erisia_execution.py` | OS command + docker + inspect | Host execution | `inspect_core_architecture` maps `erisia_core.py` → **execution module path** (bug) |
| `erisia_world_state.py` | Win32 tracker thread | Perception snapshot | — |
| `erisia_llm.py` | SmartRouter + `query_llm` | Inference SSOT | No EventBus inference emits; Ollama preferred when available |
| `erisia_events.py` | EventBus pub/sub | Neural connective tissue | Most event types have no producers |
| `erisia_telemetry.py` | SQLite + subscriber | Cost/perf awareness | Starved of events |
| `erisia_audit.py` | SelfAuditEngine → reports | Self-evaluation | Weekly schedule only in dead daemon; phrase-triggered works |
| `erisia_server.py` | FastAPI surface | Remote API | `/ask` unwired |
| `erisia_mcp.py` | MCP tools/resources | External agent interface | Not full brain |
| `erisia_mirofish_bridge.py` | HTTP client to MiroFish | External simulation bridge | Tool-exposed |

---

# Claimed vs implemented vs intended

| Claim | Reality |
|-------|---------|
| Five-layer hybrid memory | Layers exist as separate stores; dual Chroma + dual goals undermine unified hierarchy |
| Subconscious daemon always on | `background_daemon_loop` **not started** |
| Brain fully replaces daemon | Partial — observe/tools/learn only; no mission report pipeline |
| Multi-LLM failover | **IMPLEMENTED** (Ollama prefer → Groq → Together → Gemini) |
| Telemetry on every inference | Subscriber exists; **`query_llm` never emits** |
| EventBus emergent behavior | Mostly unused schema |
| Telegram auto-start | **Never called** |
| Morning briefing on startup | Module **orphaned** |
| REST remote chat | Server runs; `/ask` **503** |
| MCP = full Erisia | Bare LLM ask |
| MCP SSE | Explicit stub |
| `manage_goal_stack` tool | Schema + function; **missing from router** |
| Bayesian causal model | `probabilistic_model = None` placeholder in reasoning engine |
| Daily/weekly audit schedule | Only in unused daemon; on-demand phrases work |
| Tests cover agent | Only Oracle/backtester unit tests |

---

# Test baseline (2026-09-25)

### Environment notes

- Host Python: **3.14.4**
- Initial state: `pytest` and several `requirements.txt` packages were not installed
- After installing `pytest`, `yfinance`, `pandas`, `pytest-mock`:

### Results

```
12 passed, 0 failed
```

| File | Result |
|------|--------|
| `tests/test_firewall.py` (4) | PASSED |
| `tests/test_math_engine.py` (1) | PASSED |
| `tests/test_position_manager.py` (3) | PASSED |
| `tests/test_trade_recorder.py` (4) | PASSED |

### Collection failures observed before deps installed

- `ModuleNotFoundError: yfinance` (blocked 3 test modules)
- Earlier: `No module named pytest`

### Import / runtime gaps relevant to agent (not covered by tests)

- Full `erisia_core` / `erisia_tool_router` import requires packages such as `psutil`, `chromadb`, etc. (not all verified installed in this environment)
- **No tests** for: chat brain, tool router, memory layers, GoalStack integrity, skills forge, brain loop, passive cognition, world state, MCP, server `/ask`, EventBus/telemetry wiring, identity layers

### Important behavior with zero automated tests

Memory hierarchy, goal stack format safety, tool routing completeness, brain↔daemon mission parity, skill dual-path consistency, inference telemetry emission, channel/API wiring.

---

# Top architectural risks (ordered)

1. **Dual goal writers** on one JSON file (structured GoalStack vs flat string list) — observed mixed content on disk.
2. **Dual Chroma clients** on one persistent directory — lock/corruption risk under concurrent passive + chat + MCP.
3. **Circular core ↔ router ↔ brain** dependency — blocks clean testing and replaceable cognition.
4. **Brain double-instantiation** — cryosleep/shutdown may not control the live loop.
5. **`manage_goal_stack` unwired in router** — advertised tool fails at runtime.
6. **Telemetry claim false** — learning/doctor/MCP telemetry surfaces appear healthy but empty.
7. **Skill subsystem fork** (core vs `erisia_skills`) — MCP forge ≠ chat forge semantics over time.
8. **Legacy mission daemon dead** while still containing unique useful behavior (Mission_Reports, weekly audit schedule).
9. **`inspect_core_architecture` wrong file mapping** — self-inspection returns wrong source.
10. **Sidecar APIs (REST/MCP/Telegram) incomplete** — invite false confidence that remote I/O equals the agent.

---

# What MUST NOT be changed yet

1. Do **not** rewrite `erisia_complete_brain` or replace the LLM tool loop.
2. Do **not** delete `background_daemon_loop`, Oracle, or “redundant” memory modules — inventory first, migrate via adapters.
3. Do **not** introduce LLM+SSM+world-model cognition stack.
4. Do **not** change user-facing chat behavior, persona prompt, or skill forge approval UX unless a later phase explicitly requires it.
5. Do **not** wholesale merge Chroma clients or GoalStack formats without a migration + tests (risk of data loss).
6. Do **not** “clean up” EventBus event types or telemetry schema while producers are still missing — prefer adding emitters later.
7. Do **not** force Telegram/REST/MCP to become the primary I/O without an adapter that calls `erisia_complete_brain`.

---

# Recommended next refactoring boundary

**Boundary:** Make **tool routing + goal access + memory access** coherent *without* changing chat behavior.

Concrete first slice (for a later phase — not this document’s implementation):

1. Treat `erisia_tool_router._execute_tool_call` as the single dispatch boundary; add the missing `manage_goal_stack` route **or** remove it from `base_tools` (choose one after product decision).
2. Introduce a thin **GoalStore adapter** that both GoalStack and legacy helpers call — stop writing incompatible JSON shapes.
3. Introduce a thin **MemoryStore adapter** over one Chroma client; have `erisia_memory_manager` become a facade.
4. Fix brain lifecycle so one `ErisiaBrain` instance is shared (no behavior change intended).
5. Add characterization tests around: goal file round-trip, tool router known-tool set, MemoryManager query smoke.

Do **not** yet: extract cognition interface, unify REST/MCP with full brain, or delete the legacy daemon.

---

# Appendix — runtime topology (as actually started)

```
erisia_core.__main__
├── WorldStateTracker thread ──► erisia_world_state.json
├── PassiveCognitionEngine thread ──► Chroma + GoalStack + journal + graph
├── Thread → ErisiaBrain.heartbeat (async) ──► tools / memory / pulse
├── Thread → episodic_memory_maintenance_loop ──► erisia_memory.db
├── Optional: MCP Popen + voice init (env)
└── Main: voice/input loop ──► erisia_complete_brain ──► query_llm + _execute_tool_call

NOT in that tree:
  Telegram, briefing, REST chat, legacy mission daemon,
  erisia_sandbox module, filled telemetry from query_llm
```

---

*End of baseline. No implementation plan beyond the recommended next boundary is included, per task instructions.*
