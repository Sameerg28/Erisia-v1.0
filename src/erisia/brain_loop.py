import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .erisia_llm import ErisiaCognitiveEngine

logger = logging.getLogger("erisia_brain")

class ErisiaBrain:
    """
    The Observe-Think-Execute-Learn cognitive cycle.
    Replaces the legacy background_daemon_loop with a structured
    async architecture that integrates with all Erisia subsystems.
    
    Phases:
    0. Awaken  - Boot sequence + Cryosleep state recovery
    1. Observe - Cheap local monitoring (CPU, RAM, mission file, goals)
    2. Think   - LLM reasoning via query_llm + memory retrieval
    3. Execute - Tool routing via _execute_tool_call
    4. Learn   - Memory integration via graph_memory + memory_system
    """

    def __init__(self, core_module=None):
        self.core = core_module
        self.is_running = False
        self.current_mission = None
        self.stress_level = 0.0
        self.cycle_count = 0
        self.last_observation = {}

        # Resolve paths relative to project root
        self._base_dir = Path(__file__).resolve().parents[2]
        self._data_dir = self._base_dir / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.cryosleep_file = str(self._data_dir / "cryosleep_state.json")
        self.pulse_log = str(self._data_dir / "erisia_pulse.log")

    # ─── Phase 0: Boot ──────────────────────────────────────────────────

    async def awaken(self):
        """Boot sequence and Cryosleep state recovery."""
        self.is_running = True
        logger.info("Brain loop boot sequence initiated.")

        if os.path.exists(self.cryosleep_file):
            self._log_pulse("Recovering from Cryosleep...")
            try:
                with open(self.cryosleep_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.current_mission = state.get("current_mission")
                self.stress_level = float(state.get("stress_level", 0.0))
                os.remove(self.cryosleep_file)
                self._log_pulse(
                    f"Cryosleep recovery complete. "
                    f"Mission={self.current_mission!r}, Stress={self.stress_level:.2f}"
                )
            except Exception as exc:
                self._log_pulse(f"Cryosleep corruption detected: {exc}. Cold boot.")
                self.current_mission = None
                self.stress_level = 0.0
        else:
            self._log_pulse("Cold boot initiated. Systems nominal.")

    def initiate_cryosleep(self, signum=None, frame=None):
        """Graceful shutdown with state serialization."""
        if not self.is_running:
            return

        self._log_pulse("Cryosleep sequence initiated. Serializing state...")
        self.is_running = False

        state = {
            "current_mission": self.current_mission,
            "stress_level": round(self.stress_level, 4),
            "cycle_count": self.cycle_count,
            "last_observation": self.last_observation,
            "timestamp": time.time(),
        }

        tmp = f"{self.cryosleep_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.cryosleep_file)

        self._log_pulse("State serialized safely. Goodnight.")

        if signum is not None:
            sys.exit(0)

    # ─── Phase 1: Observe ───────────────────────────────────────────────

    async def observe(self):
        """
        Cheap local monitoring. Zero API cost.
        Returns (needs_attention, observation_context) tuple.
        """
        observation = {}

        # System health
        try:
            import psutil
            observation["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            observation["ram_percent"] = psutil.virtual_memory().percent
            observation["battery"] = self._get_battery_info()
        except Exception as exc:
            logger.debug("System health check failed: %s", exc)

        # Mission file check
        mission_file = self._resolve_mission_file()
        if mission_file and os.path.exists(mission_file):
            try:
                if os.path.getsize(mission_file) > 0:
                    with open(mission_file, "r", encoding="utf-8") as f:
                        observation["mission_text"] = f.read().strip()[:500]
            except Exception:
                pass

        # Goal stack check
        goal_stack_text = self._get_goal_stack_summary()
        if goal_stack_text:
            observation["active_goals"] = goal_stack_text

        # Pending skills check
        pending_skills = self._get_pending_skills()
        if pending_skills:
            observation["pending_skills"] = pending_skills

        # Stress-driven urgency
        if self.stress_level > 0.7:
            observation["stress_urgency"] = True

        self.last_observation = observation
        needs_attention = self._should_act(observation)

        return needs_attention, observation

    def _get_battery_info(self):
        """Return battery percentage or None."""
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery is not None:
                return {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                }
        except Exception:
            pass
        return None

    def _resolve_mission_file(self):
        """Resolve mission file path from core or default."""
        if self.core is not None:
            try:
                paths_fn = getattr(self.core, "_get_erisia_paths", None)
                if callable(paths_fn):
                    return paths_fn().get("MISSION_FILE")
            except Exception:
                pass
        default = self._base_dir / "config" / "erisia_missions.txt"
        return str(default) if default.exists() else None

    def _get_goal_stack_summary(self):
        """Read goal stack file and return active goals summary."""
        goal_stack_file = self._base_dir / "data" / "erisia_goal_stack.json"
        if not goal_stack_file.exists():
            return None
        try:
            with open(goal_stack_file, "r", encoding="utf-8") as f:
                goals = json.load(f)
            if isinstance(goals, list) and goals:
                active = [g for g in goals if isinstance(g, dict)
                          and str(g.get("status", "active")).lower() not in
                          {"completed", "done", "abandoned", "cancelled"}]
                if active:
                    return f"{len(active)} active goals"
        except Exception:
            pass
        return None

    def _get_pending_skills(self):
        """Check for pending skills awaiting approval."""
        pending_dir = self._base_dir / "skills" / "pending"
        if not pending_dir.exists():
            return None
        try:
            files = [f for f in os.listdir(pending_dir) if f.endswith(".py")]
            return files if files else None
        except Exception:
            return None

    def _should_act(self, observation):
        """Decision function: should the brain engage its expensive layers?"""
        if observation.get("mission_text"):
            return True
        if observation.get("pending_skills"):
            return True
        if observation.get("stress_urgency"):
            return True
        cpu = observation.get("cpu_percent", 0)
        ram = observation.get("ram_percent", 0)
        if cpu > 90 or ram > 95:
            return True
        return False

    # ─── Phase 2: Think ─────────────────────────────────────────────────

    async def think(self, observation):
        """
        The Pre-Frontal Cortex.
        Queries the LLM with observation context to decide next action.
        """
        self._log_pulse("Anomaly/goal detected. Engaging Thinker...")

        context_parts = []
        if observation.get("mission_text"):
            context_parts.append(
                f"ACTIVE MISSION: {observation['mission_text']}"
            )
        if observation.get("active_goals"):
            context_parts.append(
                f"GOAL STACK: {observation['active_goals']}"
            )
        if observation.get("pending_skills"):
            context_parts.append(
                f"PENDING SKILLS: {', '.join(observation['pending_skills'])}"
            )
        if observation.get("cpu_percent") is not None:
            context_parts.append(
                f"SYSTEM: CPU={observation['cpu_percent']:.0f}%, "
                f"RAM={observation['ram_percent']:.0f}%"
            )
        if observation.get("battery"):
            batt = observation["battery"]
            context_parts.append(
                f"BATTERY: {batt['percent']}% "
                f"({'plugged' if batt['power_plugged'] else 'on battery'})"
            )

        context = "\n".join(context_parts) if context_parts else "No anomalies detected."

        think_prompt = (
            f"You are Erisia's autonomous reasoning layer. "
            f"Current system observation:\n{context}\n\n"
            f"Stress level: {self.stress_level:.2f}\n\n"
            f"Decide the best autonomous action to take. "
            f"Return ONLY a JSON object with keys: "
            f'"action" (tool name string), "args" (dict), '
            f'"rationale" (short string explaining why).'
        )

        decision = {"action": "check_pc_health", "args": {}, "rationale": "routine health check"}

        if self.core is not None:
            try:
                query_llm = getattr(self.core, "query_llm", None)
                if callable(query_llm):
                    response = query_llm(
                        messages=[{"role": "user", "content": think_prompt}],
                        model="llama-3.1-8b-instant",
                        max_tokens=256,
                    )
                    raw = response.choices[0].message.content.strip()
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and "action" in parsed:
                        decision["action"] = str(parsed["action"])
                        decision["args"] = parsed.get("args", {})
                        if not isinstance(decision["args"], dict):
                            decision["args"] = {}
                        decision["rationale"] = str(parsed.get("rationale", ""))
                        self._log_pulse(f"LLM decision: {decision['action']} — {decision['rationale']}")
            except Exception as exc:
                self._log_pulse(f"Think phase LLM fallback: {exc}")
                # Default to health check on failure
        else:
            self._log_pulse("Think phase: core module not wired, using default action.")

        return decision

    # ─── Phase 3: Execute ───────────────────────────────────────────────

    async def execute(self, decision):
        """
        The Motor Cortex.
        Routes the decision through the tool router.
        """
        action = decision["action"]
        args = decision.get("args", {})
        self._log_pulse(f"Executing: {action} with args={args}")

        outcome = None

        if self.core is not None:
            try:
                from erisia.erisia_tool_router import _execute_tool_call
                outcome = _execute_tool_call(action, args, user_input="autonomous brain loop")
            except ImportError:
                self._log_pulse("Tool router not available, using direct dispatch.")
                outcome = await self._direct_dispatch(action, args)
            except Exception as exc:
                self._log_pulse(f"Tool execution error: {exc}")
                outcome = f"[BRAIN EXEC ERROR]: {exc}"
        else:
            outcome = await self._direct_dispatch(action, args)

        return outcome or "[BRAIN EXEC]: No result returned."

    async def _direct_dispatch(self, action, args):
        """Fallback dispatcher when tool router is unavailable."""
        try:
            if action == "check_pc_health":
                import psutil
                cpu = psutil.cpu_percent(interval=1)
                ram = psutil.virtual_memory()
                return (
                    f"[HEALTH CHECK] CPU: {cpu}%, "
                    f"RAM: {ram.percent}% used ({ram.available / (1024**3):.1f}GB free)"
                )
            return f"[DIRECT DISPATCH]: Action '{action}' not directly supported."
        except Exception as exc:
            return f"[DIRECT DISPATCH ERROR]: {exc}"

    # ─── Phase 4: Learn ─────────────────────────────────────────────────

    async def learn(self, outcome, decision):
        """
        The Hippocampus.
        Integrates the outcome into memory and adjusts stress.
        """
        success = "SUCCESS" in str(outcome).upper() and "ERROR" not in str(outcome).upper()

        # Stress adjustment
        if success:
            self.stress_level = max(0.0, self.stress_level - 0.05)
        else:
            self.stress_level = min(1.0, self.stress_level + 0.1)

        # Memory integration if core is wired
        if self.core is not None:
            try:
                memory_system = getattr(self.core, "memory_system", None)
                if memory_system is not None and hasattr(memory_system, "add_memory"):
                    memory_system.add_memory(
                        f"[Brain Loop #{self.cycle_count}] "
                        f"Action: {decision['action']} | "
                        f"Outcome: {str(outcome)[:300]} | "
                        f"Stress: {self.stress_level:.2f}"
                    )
            except Exception as exc:
                logger.debug("Memory integration failed: %s", exc)

            try:
                graph_memory = getattr(self.core, "graph_memory", None)
                if graph_memory is not None and hasattr(graph_memory, "add_memory_relation"):
                    action_entity = str(decision["action"])[:40]
                    relation = "resulted_in_success" if success else "resulted_in_failure"
                    graph_memory.add_memory_relation(
                        action_entity, relation, f"autonomous_cycle_{self.cycle_count}"
                    )
            except Exception as exc:
                logger.debug("Graph memory integration failed: %s", exc)

        self._log_pulse(
            f"Learn phase: stress={self.stress_level:.2f}, "
            f"outcome={'success' if success else 'failure'}"
        )

    # ─── Utilities ──────────────────────────────────────────────────────

    def _log_pulse(self, message):
        """Silent logging for the background daemon. Does not spam terminal."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"

        try:
            with open(self.pulse_log, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

        # Autonomic Garbage Collection: Rotate log if it exceeds 1MB
        try:
            if os.path.getsize(self.pulse_log) > 1024 * 1024:
                old = f"{self.pulse_log}.old"
                if os.path.exists(old):
                    os.remove(old)
                os.rename(self.pulse_log, old)
        except Exception:
            pass

    # ─── The Continuous Cycle ───────────────────────────────────────────

    async def heartbeat(self):
        """The Observe-Think-Execute-Learn continuous cycle."""
        await self.awaken()

        while self.is_running:
            self.cycle_count += 1
            try:
                # Phase 1: Observe (cheap)
                needs_attention, observation = await self.observe()

                if needs_attention:
                    # Phase 2: Think (expensive, API call)
                    decision = await self.think(observation)

                    # Phase 3: Execute (physical/OS action)
                    outcome = await self.execute(decision)

                    # Phase 4: Learn (memory integration)
                    await self.learn(outcome, decision)

                    # Fast loop when active
                    await asyncio.sleep(5)
                else:
                    # Resting state — slow poll to save CPU
                    self._log_pulse("Resting state — all systems nominal.")
                    await asyncio.sleep(60)

            except Exception as exc:
                self._log_pulse(f"CRITICAL ERROR in Brain Loop: {exc}")
                self.stress_level = min(1.0, self.stress_level + 0.15)
                await asyncio.sleep(15)  # Backoff to prevent crash loops


def _run_brain_loop(core_module=None):
    """
    Thread entry point.
    Creates an event loop and runs the brain heartbeat.
    Designed to be launched from a threading.Thread.
    """
    brain = ErisiaBrain(core_module=core_module)

    # Wire OS kill signals to Cryosleep
    try:
        signal.signal(signal.SIGINT, brain.initiate_cryosleep)
        signal.signal(signal.SIGTERM, brain.initiate_cryosleep)
    except ValueError:
        # Signals can only be set from the main thread;
        # the parent thread will handle shutdown.
        pass

    try:
        asyncio.run(brain.heartbeat())
    except KeyboardInterrupt:
        brain.initiate_cryosleep()
    except Exception as exc:
        logger.error("Brain loop terminated unexpectedly: %s", exc)


if __name__ == "__main__":
    _run_brain_loop()
