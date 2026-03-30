"""
Erisia Planning Engine — erisia_planner.py

Transforms complex multi-step goals into structured,
executable plans with dependency tracking, feasibility
checking, and adaptive re-planning on failure.

This is the cognitive layer between intent and execution.
It answers: not just WHAT to do, but HOW to do it safely,
in what order, and what to do when something goes wrong.

Called by erisia_core.py when a request requires
multiple steps or when the daemon executes a mission.
"""

from __future__ import annotations
import json
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

# --- CONSTANTS ---
LOGGER_NAME = "erisia.planner"
BASE_DIR = Path(__file__).resolve().parents[2]
PLANS_DIR = BASE_DIR / "data" / "plans"
MAX_STEPS_PER_PLAN = 10
MAX_REPLAN_ATTEMPTS = 3
STEP_TIMEOUT_SECONDS = 30.0
COMPLEXITY_THRESHOLD = 2  # requests with > N steps need planning

# --- ENUMS ---

class StepStatus(StrEnum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"

class PlanStatus(StrEnum):
    DRAFT     = "DRAFT"
    APPROVED  = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    REPLANNED = "REPLANNED"

# --- DATACLASSES ---

@dataclass
class PlanStep:
    step_id: str
    step_number: int
    description: str          # plain English what this step does
    tool_hint: str            # which tool/skill to use (may be empty)
    dependencies: list[str]   # list of step_ids this depends on
    expected_output: str      # what success looks like
    fallback: str             # what to do if this step fails
    status: str               # StepStatus value
    result: str               # actual output after execution
    error: str                # error message if failed
    started_at: str           # ISO timestamp
    completed_at: str         # ISO timestamp
    replan_count: int         # how many times this step was replanned

@dataclass
class ExecutionPlan:
    plan_id: str
    goal: str                     # the original user intent
    context: str                  # relevant context injected
    steps: list[PlanStep]
    status: str                   # PlanStatus value
    created_at: str
    completed_at: str
    replan_count: int
    total_steps: int
    completed_steps: int
    failed_steps: int
    success_rate: float
    erisia_assessment: str        # Erisia's own verdict on the plan

@dataclass
class PlanningResult:
    plan: ExecutionPlan
    succeeded: bool
    final_output: str
    lessons_learned: list[str]   # what Erisia learned from this plan

ERISIA_NATIVE_CAPABILITIES = {
    "datetime", "tavily_search", "file_write",
    "file_read", "llm_summarize", "llm", "llm_call",
    "execute_local_os_command", "check_pc_health",
    "oracle_backtest", "forge_pending_skill",
    "string_manipulation", "python",
}


# --- CLASS: PlanningEngine ---

class PlanningEngine:
    """
    Erisia's deliberate planning and execution system.
    Converts complex goals into structured, executable plans.
    """

    def __init__(
        self,
        llm_caller: Callable,
        logger: logging.Logger | None = None,
    ) -> None:
        """
        llm_caller: the query_llm function from erisia_llm.py
                    passed in to avoid circular imports
        """
        self._llm = llm_caller
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        PLANS_DIR.mkdir(parents=True, exist_ok=True)

    def should_plan(self, user_input: str) -> bool:
        """
        Determine if a request needs planning or can be 
        handled reactively.
        
        Returns True if the request:
        - Contains multiple distinct actions ("and then", "after")
        - References a sequence ("first...then...finally")
        - Has conditional logic ("if...then")
        - Involves creating, building, or researching something
        - Is a multi-part question
        
        Returns False for:
        - Single action requests ("open chrome", "check battery")
        - Questions ("what is...", "how much...")
        - Conversational messages
        """
        PLANNING_TRIGGERS = [
            r"\band then\b",
            r"\bafter that\b",
            r"\bfirst.*then\b",
            r"\bfinally\b",
            r"\bstep by step\b",
            r"\bcreate.*and\b",
            r"\bbuild.*and\b",
            r"\bif.*then\b",
            r"\bfollowed by\b",
            r"\bonce.*done\b",
        ]
        PLANNING_KEYWORDS = [
            "research and", "analyze and", "find and",
            "check and", "download and", "install and",
        ]
        lower = user_input.lower()
        for pattern in PLANNING_TRIGGERS:
            if re.search(pattern, lower):
                return True
        for kw in PLANNING_KEYWORDS:
            if kw in lower:
                return True
        # Count likely action verbs — if > threshold, plan
        action_verbs = [
            "open", "check", "find", "create", "build",
            "run", "search", "download", "send", "read",
            "write", "analyze", "compare", "generate",
        ]
        verb_count = sum(1 for v in action_verbs if v in lower)
        return verb_count >= COMPLEXITY_THRESHOLD

    def _enrich_with_causal_context(
        self,
        goal: str,
        reasoning_engine: Any | None,
    ) -> str:
        """
        Query the causal reasoning engine for context
        relevant to the goal before creating the plan.
        Returns a context string to inject into planning.
        """
        if reasoning_engine is None:
            return ""
        
        causal_context_lines = []
        
        # Extract key nouns from goal as query entities
        import re
        words = re.findall(r'\b[A-Z]{2,}\b|\b\w{5,}\b', goal)
        query_entities = list(set(words))[:3]
        
        for entity in query_entities:
            try:
                causes = reasoning_engine.infer_potential_causes(
                    entity, depth=2
                )
                effects = reasoning_engine.predict_potential_effects(
                    entity, depth=2
                )
                if causes:
                    causal_context_lines.append(
                        f"Known causes of '{entity}': " +
                        ", ".join(
                            c.get("cause", "") 
                            for c in causes[:2]
                        )
                    )
                if effects:
                    causal_context_lines.append(
                        f"Known effects of '{entity}': " +
                        ", ".join(
                            e.get("effect", "")
                            for e in effects[:2]
                        )
                    )
            except Exception:
                pass
        
        if not causal_context_lines:
            return ""
        
        return (
            "Causal context from knowledge graph:\n" +
            "\n".join(causal_context_lines)
        )

    def _get_stress_adapted_max_steps(
        self,
        identity_layer: Any | None,
    ) -> int:
        """
        Return max steps based on Sameer's stress level.
        High stress = prefer simpler plans.
        """
        if identity_layer is None:
            return MAX_STEPS_PER_PLAN
        try:
            stress_score = float(
                identity_layer.get_stress_level()
            )
            if stress_score > 0.7:
                return 3   # High stress: max 3 steps
            elif stress_score > 0.4:
                return 6   # Moderate: max 6 steps
            else:
                return MAX_STEPS_PER_PLAN
        except Exception:
            return MAX_STEPS_PER_PLAN

    def create_plan(
        self,
        goal: str,
        context: str = "",
        available_tools: list[str] | None = None,
        reasoning_engine: Any | None = None,
        identity_layer: Any | None = None,
    ) -> ExecutionPlan:
        """
        Use LLM to decompose a goal into ordered steps.
        Returns a complete ExecutionPlan before any execution.
        """
        causal_context = self._enrich_with_causal_context(
            goal, reasoning_engine
        )
        if causal_context:
            context = f"{context}\n\n{causal_context}".strip()

        adapted_max_steps = self._get_stress_adapted_max_steps(
            identity_layer
        )

        tools_str = (
            ", ".join(available_tools)
            if available_tools
            else "general system tools"
        )
        
        decomposition_prompt = [
            {
                "role": "system",
                "content": (
                    "You are Erisia's planning engine. Decompose goals "
                    "into precise executable steps using ONLY Erisia's "
                    "actual built-in capabilities.\n\n"
                    "ERISIA'S ACTUAL CAPABILITIES:\n"
                    "- datetime: get current date/time\n"
                    "- tavily_search: search the web for any topic\n"
                    "- file_write: save text content to a file\n"
                    "- file_read: read content from a file\n"
                    "- llm_summarize: summarize or analyze text\n"
                    "- execute_local_os_command: run Python code\n"
                    "- check_pc_health: system metrics\n"
                    "- oracle_backtest: run financial backtests\n"
                    "- forge_pending_skill: create new Python tools\n\n"
                    "CRITICAL RULES:\n"
                    "- NEVER use shell commands (curl, wget, touch, "
                    "echo, cat, ls, rm, grep, mv, date)\n"
                    "- NEVER use 'curl or wget' as a tool hint\n"
                    "- For web search: tool_hint = 'tavily_search'\n"
                    "- For getting time: tool_hint = 'datetime'\n"
                    "- For saving files: tool_hint = 'file_write'\n"
                    "- For summarizing: tool_hint = 'llm_summarize'\n"
                    "- For running code: tool_hint = "
                    "'execute_local_os_command'\n"
                    "- Keep plans to 5 steps maximum for efficiency\n"
                    "- Each step must be achievable with the tools listed\n\n"
                    "Output format (strict JSON array, no markdown):\n"
                    "[\n"
                    "  {\n"
                    '    "step_number": 1,\n'
                    '    "description": "what this step does",\n'
                    '    "tool_hint": "exact tool name from the list",\n'
                    '    "dependencies": [],\n'
                    '    "expected_output": "what success looks like",\n'
                    '    "fallback": "what to do if this fails"\n'
                    "  }\n"
                    "]\n"
                )
            },
            {
                "role": "user",
                "content": (
                    f"Goal: {goal}\n"
                    f"Context: {context or 'No additional context.'}"
                )
            }
        ]

        try:
            response = self._llm(
                messages=decomposition_prompt,
                max_tokens=1000,
                temperature=0.3,
                model="llama-3.3-70b-versatile",
            )
            raw = response.choices[0].message.content or ""
            raw = raw.strip()
            # Strip markdown fences if present
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            steps_data = json.loads(raw)
        except (json.JSONDecodeError, AttributeError,
                IndexError, Exception) as exc:
            self._logger.error(
                "Plan decomposition failed: %s", exc
            )
            # Return a single-step fallback plan
            steps_data = [{
                "step_number": 1,
                "description": goal,
                "tool_hint": "",
                "dependencies": [],
                "expected_output": "Task completed",
                "fallback": "Report failure to Master Sameer"
            }]

        steps = []
        for item in steps_data[:adapted_max_steps]:
            step = PlanStep(
                step_id=str(uuid.uuid4())[:8],
                step_number=int(item.get("step_number", 1)),
                description=str(item.get("description", "")),
                tool_hint=str(item.get("tool_hint", "")),
                dependencies=[
                    str(d) for d in item.get("dependencies", [])
                ],
                expected_output=str(
                    item.get("expected_output", "")
                ),
                fallback=str(item.get("fallback", "")),
                status=StepStatus.PENDING.value,
                result="",
                error="",
                started_at="",
                completed_at="",
                replan_count=0,
            )
            steps.append(step)

        plan = ExecutionPlan(
            plan_id=str(uuid.uuid4())[:12],
            goal=goal,
            context=context,
            steps=steps,
            status=PlanStatus.DRAFT.value,
            created_at=datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            completed_at="",
            replan_count=0,
            total_steps=len(steps),
            completed_steps=0,
            failed_steps=0,
            success_rate=0.0,
            erisia_assessment="",
        )

        self._save_plan(plan)
        self._logger.info(
            "Plan created: %s (%d steps) for goal: %s",
            plan.plan_id, len(steps), goal[:60]
        )
        return plan

    def _check_tool_exists(
        self,
        tool_hint: str,
        active_skills: list[str],
        base_tool_names: list[str],
    ) -> bool:
        if not tool_hint:
            return True
        tool_lower = tool_hint.lower().replace(" ", "_")
        
        # Always available native capabilities
        if tool_lower in ERISIA_NATIVE_CAPABILITIES:
            return True
        if any(
            native in tool_lower 
            for native in ERISIA_NATIVE_CAPABILITIES
        ):
            return True
            
        all_tools = [
            t.lower() for t in active_skills + base_tool_names
        ]
        return any(tool_lower in t for t in all_tools)

    def _request_skill_forge(
        self,
        tool_hint: str,
        step_description: str,
        forge_fn: Any | None,
        logger: logging.Logger,
    ) -> None:
        """
        Request a new skill be forged for a missing tool.
        """
        if forge_fn is None or not tool_hint:
            return
        try:
            skill_name = (
                tool_hint.lower()
                .replace(" ", "_")
                .replace("-", "_")
            )
            skill_description = (
                f"Tool needed for plan step: {step_description}"
            )
            logger.info(
                "Planner requesting skill forge: %s",
                skill_name
            )
            # Call the forge function (usually forge_pending_skill in core)
            forge_fn(skill_name, skill_description)
        except Exception as exc:
            logger.error(
                "Skill forge request failed: %s", exc
            )

    def execute_plan(
        self,
        plan: ExecutionPlan,
        executor: Callable[[str, str, str], str],
        active_skills: list[str] | None = None,
        base_tool_names: list[str] | None = None,
        forge_fn: Callable | None = None,
    ) -> PlanningResult:
        """
        Execute a plan step by step.
        
        executor: a callable that takes (step_description, tool_hint, context)
                  and returns the result string.
        active_skills: list of currently available skill names.
        base_tool_names: list of built-in tool names.
        forge_fn: function to call when a tool is missing.
        """
        plan.status = PlanStatus.EXECUTING.value
        outputs: list[str] = []
        lessons: list[str] = []
        step_context = ""

        for step in plan.steps:
            # Check dependencies
            if not self._dependencies_met(step, plan.steps):
                step.status = StepStatus.SKIPPED.value
                self._logger.warning(
                    "Step %d skipped — unmet dependencies",
                    step.step_number
                )
                continue

            # Check if required tool exists
            if (step.tool_hint and
                    not self._check_tool_exists(
                        step.tool_hint,
                        active_skills or [],
                        base_tool_names or [],
                    )):
                self._request_skill_forge(
                    tool_hint=step.tool_hint,
                    step_description=step.description,
                    forge_fn=forge_fn,
                    logger=self._logger,
                )
                step.result = (
                    f"[SKILL FORGE REQUESTED]: "
                    f"'{step.tool_hint}' queued for creation. "
                    f"Awaiting Master Sameer's approval. "
                    f"Continuing plan with remaining steps."
                )
                step.status = StepStatus.COMPLETED.value
                step.completed_at = datetime.now(UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                plan.completed_steps += 1
                outputs.append(
                    f"Step {step.step_number}: {step.result}"
                )
                step_context += (
                    f"Step {step.step_number} result: "
                    f"{step.result[:200]}\n"
                )
                continue

            step.status = StepStatus.RUNNING.value
            step.started_at = datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            self._logger.info(
                "Executing step %d: %s",
                step.step_number, step.description
            )

            try:
                result = executor(
                    step.description, step.tool_hint, step_context
                )
                step.result = str(result)
                step.status = StepStatus.COMPLETED.value
                step.completed_at = datetime.now(UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                plan.completed_steps += 1
                outputs.append(
                    f"Step {step.step_number}: {step.result}"
                )
                step_context += (
                    f"Step {step.step_number} result: "
                    f"{step.result[:200]}\n"
                )
                self._logger.info(
                    "Step %d completed successfully",
                    step.step_number
                )

            except Exception as exc:
                step.error = str(exc)
                step.status = StepStatus.FAILED.value
                step.completed_at = datetime.now(UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                plan.failed_steps += 1
                lessons.append(
                    f"Step {step.step_number} failed: "
                    f"{step.description} — {step.error}. "
                    f"Fallback was: {step.fallback}"
                )
                self._logger.error(
                    "Step %d failed: %s", 
                    step.step_number, exc
                )

                # Attempt fallback
                if step.fallback and step.replan_count < MAX_REPLAN_ATTEMPTS:
                    step.replan_count += 1
                    try:
                        fallback_result = executor(
                            step.fallback, step.tool_hint, step_context
                        )
                        step.result = (
                            f"[FALLBACK]: {fallback_result}"
                        )
                        step.status = StepStatus.COMPLETED.value
                        plan.completed_steps += 1
                        plan.failed_steps -= 1
                        outputs.append(
                            f"Step {step.step_number} "
                            f"(via fallback): {step.result}"
                        )
                    except Exception as fb_exc:
                        self._logger.error(
                            "Fallback also failed for step %d: %s",
                            step.step_number, fb_exc
                        )

        # Compute final metrics
        plan.success_rate = (
            plan.completed_steps / plan.total_steps
            if plan.total_steps > 0 else 0.0
        )
        plan.status = (
            PlanStatus.COMPLETED.value
            if plan.success_rate >= 0.5
            else PlanStatus.FAILED.value
        )
        plan.completed_at = datetime.now(UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        plan.erisia_assessment = self._assess_plan(plan)

        self._save_plan(plan)

        final_output = "\n".join(outputs) if outputs else (
            "Plan execution produced no output."
        )

        result = PlanningResult(
            plan=plan,
            succeeded=plan.status == PlanStatus.COMPLETED.value,
            final_output=final_output,
            lessons_learned=lessons,
        )

        self._logger.info(
            "Plan %s finished. Status: %s. "
            "Success rate: %.0f%%",
            plan.plan_id, plan.status,
            plan.success_rate * 100
        )
        return result

    def replan(
        self,
        plan: ExecutionPlan,
        failed_step: PlanStep,
        executor: Callable[[str, str], str],
    ) -> ExecutionPlan:
        """
        Generate a revised plan when a critical step fails.
        Preserves completed steps and replans from failure point.
        """
        if plan.replan_count >= MAX_REPLAN_ATTEMPTS:
            self._logger.warning(
                "Max replan attempts reached for plan %s",
                plan.plan_id
            )
            return plan

        completed = [
            s for s in plan.steps
            if s.status == StepStatus.COMPLETED.value
        ]
        completed_descriptions = [s.description for s in completed]

        replan_prompt = [
            {
                "role": "system",
                "content": (
                    "You are Erisia's adaptive re-planner. "
                    "A step in the current plan has failed. "
                    "Generate ONLY the remaining steps needed "
                    "to complete the original goal. "
                    "Output ONLY valid JSON array — same format "
                    "as before. No explanation."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Original goal: {plan.goal}\n"
                    f"Completed steps: "
                    f"{json.dumps(completed_descriptions)}\n"
                    f"Failed step: {failed_step.description}\n"
                    f"Failure reason: {failed_step.error}\n"
                    f"Generate the remaining steps to still "
                    f"achieve the goal."
                )
            }
        ]

        try:
            response = self._llm(
                messages=replan_prompt,
                max_tokens=800,
                temperature=0.3,
                model="llama-3.3-70b-versatile",
            )
            raw = response.choices[0].message.content or ""
            raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
            raw = re.sub(r'\s*```$', '', raw)
            new_steps_data = json.loads(raw)
        except Exception as exc:
            self._logger.error("Replan failed: %s", exc)
            return plan

        # Keep completed steps, replace rest
        new_steps = list(completed)
        offset = len(completed) + 1
        for i, item in enumerate(
            new_steps_data[:MAX_STEPS_PER_PLAN - len(completed)]
        ):
            step = PlanStep(
                step_id=str(uuid.uuid4())[:8],
                step_number=offset + i,
                description=str(item.get("description", "")),
                tool_hint=str(item.get("tool_hint", "")),
                dependencies=[],
                expected_output=str(
                    item.get("expected_output", "")
                ),
                fallback=str(item.get("fallback", "")),
                status=StepStatus.PENDING.value,
                result="",
                error="",
                started_at="",
                completed_at="",
                replan_count=0,
            )
            new_steps.append(step)

        plan.steps = new_steps
        plan.total_steps = len(new_steps)
        plan.status = PlanStatus.REPLANNED.value
        plan.replan_count += 1
        self._save_plan(plan)

        self._logger.info(
            "Plan %s replanned (attempt %d)",
            plan.plan_id, plan.replan_count
        )
        return plan

    def format_plan_for_display(
        self, plan: ExecutionPlan
    ) -> str:
        """
        Format a plan for terminal display before execution.
        Shows Sameer the plan so he can confirm or abort.
        """
        lines = [
            f"\n{'═' * 52}",
            f"  Erisia Planning Engine — Plan {plan.plan_id}",
            f"{'─' * 52}",
            f"  Goal: {plan.goal}",
            f"  Steps: {plan.total_steps}",
            f"{'─' * 52}",
        ]
        for step in plan.steps:
            lines.append(
                f"  {step.step_number}. {step.description}"
            )
            if step.tool_hint:
                lines.append(
                    f"     Tool: {step.tool_hint}"
                )
            if step.dependencies:
                lines.append(
                    f"     Depends on: "
                    f"steps {step.dependencies}"
                )
        lines.append(f"{'═' * 52}")
        lines.append(
            "  Proceed with this plan? (y/n): "
        )
        return "\n".join(lines)

    def _dependencies_met(
        self,
        step: PlanStep,
        all_steps: list[PlanStep],
    ) -> bool:
        """Check if all dependencies for a step are met."""
        if not step.dependencies:
            return True

        completed_numbers: set[str] = {
            str(s.step_number)
            for s in all_steps
            if s.status in (
                StepStatus.COMPLETED.value,
                StepStatus.SKIPPED.value,
            )
        }

        clean_deps: list[str] = []
        for dep in step.dependencies:
            dep_str = str(dep).strip()
            dep_str = dep_str.strip("[]'\"")
            for part in dep_str.split(","):
                part = part.strip().strip("'\"")
                if part:
                    clean_deps.append(part)

        return all(
            dep in completed_numbers
            for dep in clean_deps
        )

    def _assess_plan(self, plan: ExecutionPlan) -> str:
        """Generate Erisia's honest assessment of the plan."""
        if plan.success_rate == 1.0:
            return (
                f"Plan executed flawlessly. All "
                f"{plan.total_steps} steps completed."
            )
        elif plan.success_rate >= 0.75:
            return (
                f"Plan mostly successful. "
                f"{plan.completed_steps}/{plan.total_steps} "
                f"steps completed. "
                f"{plan.failed_steps} steps required fallback."
            )
        elif plan.success_rate >= 0.5:
            return (
                f"Plan partially executed. "
                f"{plan.completed_steps}/{plan.total_steps} "
                f"steps completed. Goal may be partially achieved."
            )
        else:
            return (
                f"Plan failed. Only "
                f"{plan.completed_steps}/{plan.total_steps} "
                f"steps succeeded. Goal not achieved. "
                f"Manual intervention recommended."
            )

    def _save_plan(self, plan: ExecutionPlan) -> None:
        """Persist plan to JSON file in data/plans/."""
        plan_path = PLANS_DIR / f"plan_{plan.plan_id}.json"
        try:
            plan_path.write_text(
                json.dumps(asdict(plan), indent=2,
                           ensure_ascii=False),
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self._logger.error(
                "Failed to save plan %s: %s",
                plan.plan_id, exc
            )
