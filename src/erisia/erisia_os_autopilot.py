from __future__ import annotations

import json
import re
from typing import Any, Callable


def looks_like_os_control_request(user_input: Any) -> bool:
    text = str(user_input or "").strip().lower()
    if not text:
        return False

    conversational_openers = (
        "what ",
        "why ",
        "how ",
        "who ",
        "tell me",
        "can you tell",
        "do you know",
        "what do you know",
    )
    if text.startswith(conversational_openers):
        return False

    verbs = [
        "open", "launch", "start", "run", "play", "close",
        "kill", "mute", "unmute", "search", "find",
    ]
    devices_or_apps = [
        "chrome", "browser", "youtube", "spotify", "vscode",
        "notepad", "calculator", "process", "volume", ".exe",
    ]

    if re.match(r"^(please\s+)?(can you\s+)?(open|launch|start|run|play|close|kill|mute|unmute|search|find)\b", text):
        return True
    if any(v in text for v in verbs) and any(k in text for k in devices_or_apps):
        return True
    return False


def fallback_os_script_from_request(user_input: Any) -> str | None:
    text = str(user_input or "").strip()
    lowered = text.lower()
    if not text:
        return None

    if "play " in lowered or "youtube" in lowered:
        query = text
        play_index = lowered.find("play ")
        if play_index >= 0:
            query = text[play_index + 5:].strip() or text
        return (
            "import webbrowser\n"
            "from urllib.parse import quote_plus\n"
            f"query = {json.dumps(query)}\n"
            "url = 'https://www.youtube.com/results?search_query=' + quote_plus(query)\n"
            "webbrowser.open(url)\n"
            "print('Opened YouTube search results.')\n"
        )

    if "open chrome" in lowered or "open browser" in lowered:
        return (
            "import webbrowser\n"
            "webbrowser.open('https://www.google.com')\n"
            "print('Opened browser.')\n"
        )

    if "open vscode" in lowered or "launch vscode" in lowered or "open vs code" in lowered:
        return (
            "import subprocess\n"
            "subprocess.Popen(['code', '.'], shell=False)\n"
            "print('Opened VS Code.')\n"
        )

    return None


def generate_os_control_script_spec(
    user_input: Any,
    *,
    query_llm: Callable[..., Any],
    safe_json_parse: Callable[[Any], Any],
    derive_skill_name_from_request: Callable[[Any], str],
) -> dict[str, Any] | None:
    lowered = str(user_input or "").lower()
    default_save = not any(
        token in lowered
        for token in [
            "temporary", "temporarily", "one time", "one-time",
            "just this time", "dont save", "don't save", "do not save",
        ]
    )
    planner_system = (
        "You are a Windows OS automation compiler. "
        "Return ONLY valid JSON with keys: script_code (string), skill_name (string), save_for_future (boolean). "
        "Use Python stdlib only. Do not output markdown. "
        "skill_name must be a short generic capability name in snake_case (e.g., youtube_search, browser_open), not a slug of the full user prompt."
    )
    planner_user = (
        f"Master request: {user_input}\n"
        f"default_save_for_future: {str(default_save).lower()}\n"
        "Write a short, safe script that performs the request and prints a status line."
    )

    try:
        raw = query_llm(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": planner_system},
                {"role": "user", "content": planner_user},
            ],
            max_tokens=450,
        ).choices[0].message.content
        data = safe_json_parse(raw)
    except Exception:
        data = None

    if not isinstance(data, dict):
        fallback_script = fallback_os_script_from_request(user_input)
        if not fallback_script:
            return None
        return {
            "script_code": fallback_script,
            "skill_name": derive_skill_name_from_request(user_input),
            "save_for_future": default_save,
        }

    script_code = str(data.get("script_code") or "").strip() or fallback_os_script_from_request(user_input)
    if not script_code:
        return None

    skill_name = str(data.get("skill_name")).strip() or derive_skill_name_from_request(user_input)
    raw_save = data.get("save_for_future", default_save)
    save_for_future = (
        raw_save if isinstance(raw_save, bool)
        else (raw_save.strip().lower() in {"1", "true", "yes", "y"}) if isinstance(raw_save, str)
        else bool(raw_save) if isinstance(raw_save, (int, float))
        else default_save
    )
    return {
        "script_code": script_code,
        "skill_name": skill_name,
        "save_for_future": save_for_future,
    }


def build_pending_skill_module(skill_name: Any, script_code: Any, user_input: Any) -> str:
    safe_tool_name = re.sub(r"[^a-z0-9_]+", "_", str(skill_name).lower()).strip("_") or "os_control_skill"
    description = f"Autogenerated OS control skill for request: {str(user_input).strip()[:80]}"
    return (
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "import tempfile\n\n"
        "TOOL_SCHEMA = {\n"
        "    \"type\": \"function\",\n"
        "    \"function\": {\n"
        f"        \"name\": {json.dumps(safe_tool_name)},\n"
        f"        \"description\": {json.dumps(description)},\n"
        "        \"parameters\": {\n"
        "            \"type\": \"object\",\n"
        "            \"properties\": {}\n"
        "        }\n"
        "    }\n"
        "}\n\n"
        "def execute_skill(**kwargs):\n"
        f"    script_code = {json.dumps(str(script_code or ''))}\n"
        f"    file_path = os.path.join(tempfile.gettempdir(), {json.dumps(safe_tool_name + '_runtime.py')})\n"
        "    with open(file_path, 'w', encoding='utf-8') as f:\n"
        "        f.write(script_code)\n"
        "    try:\n"
        "        result = subprocess.run([sys.executable, file_path], capture_output=True, text=True, timeout=20)\n"
        "        if result.returncode == 0:\n"
        "            return '[SKILL SUCCESS]:\\n' + result.stdout\n"
        "        return '[SKILL ERROR]:\\n' + result.stderr\n"
        "    except subprocess.TimeoutExpired:\n"
        "        return '[SKILL ERROR]: Runtime exceeded 20 seconds.'\n"
        "    except Exception as e:\n"
        "        return '[SKILL ERROR]: ' + str(e)\n"
    )


def run_os_control_autopilot(
    user_input: Any,
    *,
    looks_like_request: Callable[[Any], bool],
    generate_spec: Callable[[Any], dict[str, Any] | None],
    execute_local_os_command: Callable[[Any], str],
    tool_response_has_success_marker: Callable[[Any], bool],
    tool_response_has_error: Callable[[Any], bool],
    build_pending_module: Callable[[Any, Any, Any], str],
    forge_pending_skill: Callable[[Any, Any], str],
) -> tuple[str | None, list[str]]:
    if not looks_like_request(user_input):
        return None, []

    print("\n[System: OS-control request detected. Routing through local execution...]")
    spec = generate_spec(user_input)
    if not spec:
        return None, []

    script_code = spec["script_code"]
    skill_name = spec["skill_name"]
    save_for_future = bool(spec["save_for_future"])

    execution_result = execute_local_os_command(script_code)
    invoked_tools = ["execute_local_os_command"]
    reply_lines = [execution_result]

    if save_for_future and tool_response_has_success_marker(execution_result) and not tool_response_has_error(execution_result):
        pending_module = build_pending_module(skill_name, script_code, user_input)
        save_result = forge_pending_skill(skill_name, pending_module)
        invoked_tools.append("forge_pending_skill")
        reply_lines.append(save_result)

    return "\n".join(reply_lines), invoked_tools
