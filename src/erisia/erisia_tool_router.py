import logging
import json
import re
import contextlib

logger = logging.getLogger("erisia_tool_router")

from .erisia_system_tools import check_pc_health, launch_vscode, mute_unmute_volume, kill_process, clear_temp_files
from .erisia_execution import inspect_core_architecture, execute_local_os_command, execute_secure_docker


def _parse_tool_arguments(raw_arguments):
    """Safely parse tool arguments from JSON string/dict."""
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        stripped = raw_arguments.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_json_objects(text):
    """Collect all top-level JSON objects from free-form text."""
    if not isinstance(text, str):
        return []
    objects = []
    stack = []
    start_idx = None
    for idx, ch in enumerate(text):
        if ch == "{":
            if not stack:
                start_idx = idx
            stack.append("{")
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start_idx is not None:
                    candidate = text[start_idx : idx + 1]
                    with contextlib.suppress(Exception):
                        obj = json.loads(candidate)
                        objects.append(obj)
                    start_idx = None
    return objects


def _extract_text_tool_calls(content):
    """Fallback parser for models that emit one or more tool-call JSON blocks in plain text."""
    if not isinstance(content, str) or not content.strip():
        return []

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()

    objs = _extract_json_objects(text)
    calls = []
    for payload in objs:
        if not isinstance(payload, dict):
            continue

        func_name = payload.get("name")
        args = payload.get("parameters") or payload.get("arguments") or payload.get("args") or {}

        function_block = payload.get("function")
        if isinstance(function_block, dict):
            func_name = function_block.get("name", func_name)
            args = function_block.get("arguments", args)

        if payload.get("type") == "function" and payload.get("name"):
            func_name = payload.get("name")
            args = payload.get("parameters") or payload.get("arguments") or {}

        if not func_name:
            continue

        parsed_args = _parse_tool_arguments(args)
        calls.append({"name": str(func_name), "arguments": parsed_args})

    return calls


def _execute_tool_call(func_name, args, user_input, dynamic_skill_map=None):
    """Centralized router for built-in and dynamic tools."""
    from erisia.erisia_core import (
        daemon_system,
        get_world_state,
        analyze_screen,
        reason_about_event,
        update_consciousness,
        save_heuristic_rule,
        forge_new_skill,
        forge_pending_skill,
        approve_skill,
        reject_skill,
        graph_memory,
        custom_skill_functions,
        speak_text,
        mirofish_call,
    )

    runtime_dynamic_skills = dynamic_skill_map if isinstance(dynamic_skill_map, dict) else custom_skill_functions

    def _has_strict_approval_intent(text, skill_name):
        user_text = str(text or "").strip().lower()
        if not user_text or user_text.endswith("?"):
            return False

        blocked_context_terms = ("what", "how", "why", "explain", "details", "describe")
        if any(term in user_text for term in blocked_context_terms):
            return False

        normalized_skill = str(skill_name or "").strip().lower()
        normalized_skill_no_ext = normalized_skill[:-3] if normalized_skill.endswith(".py") else normalized_skill

        exact_allow = {
            "approve",
            "accept",
            "yes",
            "yes approve",
            "yes accept",
            "approve it",
            "accept it",
        }
        if user_text in exact_allow:
            return True

        if normalized_skill_no_ext:
            skill_pattern = re.escape(normalized_skill_no_ext)
            if re.fullmatch(rf"(please\s+)?(approve|accept)\s+{skill_pattern}(\.py)?", user_text):
                return True
            if re.fullmatch(rf"(yes[, ]+)?(approve|accept)\s+{skill_pattern}(\.py)?", user_text):
                return True

        return False

    if func_name == "create_mission_plan":
        return daemon_system.start_mission(args.get("mission_name", "Unknown"), args.get("steps", []))
    if func_name == "mark_step_complete":
        return daemon_system.mark_step_complete(args.get("summary_of_result", ""))
    if func_name == "check_pc_health":
        return check_pc_health()
    if func_name == "launch_vscode":
        return launch_vscode()
    if func_name == "open_chrome":
        url = str(args.get("url") or "").strip()
        query = str(args.get("query") or "").strip()
        if url:
            script_code = (
                "import webbrowser\n"
                f"url = {json.dumps(url)}\n"
                "webbrowser.open(url)\n"
                "print('Opened URL in browser.')\n"
            )
        elif query:
            script_code = (
                "import webbrowser\n"
                "from urllib.parse import quote_plus\n"
                f"query = {json.dumps(query)}\n"
                "url = 'https://www.google.com/search?q=' + quote_plus(query)\n"
                "webbrowser.open(url)\n"
                "print('Opened browser search query.')\n"
            )
        else:
            script_code = (
                "import webbrowser\n"
                "webbrowser.open('https://www.google.com')\n"
                "print('Opened browser.')\n"
            )
        return execute_local_os_command(script_code)
    if func_name == "get_world_state":
        return get_world_state()
    if func_name == "analyze_screen":
        return analyze_screen(args.get("vision_prompt"))
    if func_name == "reason_about_event":
        return reason_about_event(args.get("event"), args.get("query_type", "causes"), args.get("depth", 2))
    if func_name == "inspect_core_architecture":
        return inspect_core_architecture(args.get("file_name"))
    if func_name == "execute_local_os_command":
        return execute_local_os_command(args.get("script_code"))
    if func_name == "execute_secure_docker":
        return execute_secure_docker(args.get("script_code"))
    if func_name == "mute_unmute_volume":
        return mute_unmute_volume()
    if func_name == "kill_process":
        return kill_process(args.get("process_name"))
    if func_name == "clear_temp_files":
        return clear_temp_files()
    if func_name == "update_consciousness":
        return update_consciousness(args.get("thought_log"))
    if func_name == "save_heuristic_rule":
        return save_heuristic_rule(args.get("rule_text"))
    if func_name == "forge_new_skill":
        return forge_new_skill(args.get("skill_name"), args.get("python_code"))
    if func_name == "forge_pending_skill":
        return forge_pending_skill(args.get("skill_name"), args.get("python_code"))
    if func_name == "approve_skill":
        target_skill = args.get("skill_name")
        if not _has_strict_approval_intent(user_input, target_skill):
            return (
                "[ALIGNMENT INTERCEPTOR]: Blocked approve_skill. "
                "Strict explicit intent is required (e.g., 'approve', 'accept', "
                "'yes', or 'approve <skill_name>')."
            )
        return approve_skill(target_skill)
    if func_name == "reject_skill":
        return reject_skill(args.get("skill_name"))
    if func_name == "update_relational_memory":
        return graph_memory.add_memory_relation(
            args.get("entity1"),
            args.get("relation"),
            args.get("entity2"),
        )
    if func_name == "speak_text":
        return speak_text(args.get("text"))
    if func_name == "mirofish_call":
        return mirofish_call(
            endpoint=args.get("endpoint"),
            method=args.get("method", "GET"),
            payload_json=args.get("payload_json"),
            timeout_seconds=args.get("timeout_seconds", 60.0),
            auto_start=args.get("auto_start", True),
        )
    if func_name in runtime_dynamic_skills:
        try:
            return runtime_dynamic_skills[func_name](**args)
        except Exception as e:
            return f"[DYNAMIC SKILL ERROR]: {str(e)}"

    return f"[SYSTEM ERROR]: Unknown tool '{func_name}'."
