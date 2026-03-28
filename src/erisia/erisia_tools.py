import os
import sys
import json
import re
import subprocess
import psutil
import pyautogui
import datetime
import shutil
import ast
import copy
from pathlib import Path
from typing import Any, Callable, List, Dict, Optional, cast
from PIL import ImageGrab
import io
import base64
from erisia.erisia_llm import query_llm

# Calculate the actual project root
BASE_DIR = Path(__file__).resolve().parents[2]
BASE_DIR_STR = str(BASE_DIR)

# Paths
CONSCIOUSNESS_FILE = str(BASE_DIR / "config" / "Erisia_Consciousness.md")
SANDBOX_DIR = os.path.join(BASE_DIR_STR, "skills", "sandbox")

# Constants
SUCCESS_MARKER_RE = re.compile(r"\[[^\]]*SUCCESS[^\]]*\]", re.IGNORECASE)
ERROR_MARKER_RE = re.compile(r"\[[^\]]*ERROR[^\]]*\]", re.IGNORECASE)

def _tool_response_has_success_marker(function_response):
    """Detect standardized success tokens like [LOCAL OS SUCCESS]."""
    return bool(SUCCESS_MARKER_RE.search(str(function_response or "")))

def _tool_response_has_error(function_response):
    """Detect explicit error markers and traceback-style failures."""
    response_text = str(function_response or "")
    lowered = response_text.lower()
    return (
        bool(ERROR_MARKER_RE.search(response_text))
        or "traceback" in lowered
        or "exception" in lowered
    )

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
                    try:
                        obj = json.loads(candidate)
                        objects.append(obj)
                    except Exception:
                        pass
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

# --- Tool Implementations ---

def check_pc_health() -> str:
    """Checks CPU and RAM usage."""
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory().percent
    report = f"CPU: {cpu}%, RAM: {ram}%."
    if ram > 85:
        return report + " [CRITICAL WARNING]: Master, your 8GB memory is almost full. Risk of VS Code crash."
    return report + " System is stable."

def launch_vscode() -> str:
    """Opens VS Code."""
    try:
        subprocess.Popen(["code", "."], shell=False)
        return "Successfully opened VS Code for Master Sameer."
    except Exception as e:
        return f"Failed to open workspace. Error: {e}"

def mute_unmute_volume() -> str:
    """Toggles the system volume mute state."""
    try:
        pyautogui.press('volumemute')
        return "[SYSTEM ACTION]: Volume mute toggled successfully for Master Sameer."
    except Exception as e:
        return f"[SYSTEM ERROR]: Could not toggle volume. {str(e)}"

def kill_process(process_name: str) -> str:
    """Assassinates a heavy background process."""
    if not process_name:
        return "[SYSTEM ERROR]: process_name is required."

    process_name = str(process_name).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", process_name):
        return "[SYSTEM ERROR]: Invalid process name format."

    try:
        result = subprocess.run(
            ["taskkill", "/f", "/im", process_name],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return f"[SYSTEM ACTION]: Successfully terminated {process_name}. System resources freed."
        stderr = result.stderr.strip() if result.stderr else "Unknown taskkill error."
        return f"[SYSTEM ERROR]: Could not terminate {process_name}. {stderr}"
    except Exception as e:
        return f"[SYSTEM ERROR]: Failed to kill {process_name}. Error: {str(e)}"

def clear_temp_files() -> str:
    """Wipes the Windows %TEMP% folder to optimize the i3 processor."""
    temp_dir = os.environ.get('TEMP')
    if not temp_dir:
        return "[SYSTEM ERROR]: Could not locate Windows TEMP directory."
    
    freed_space = 0
    deleted_files = 0
    for item in os.listdir(temp_dir):
        item_path = os.path.join(temp_dir, item)
        try:
            size = os.path.getsize(item_path)
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            freed_space += size
            deleted_files += 1
        except Exception:
            pass # Skip files that are currently being used by Windows
            
    mb_freed = freed_space / (1024 * 1024)
    return f"[SYSTEM ACTION]: Cleared {deleted_files} temporary files. Freed {mb_freed:.2f} MB of space."

def inspect_core_architecture(file_name: str) -> str:
    """
    Erisia reads and analyzes her own source code.
    Returns structured analysis, not just raw file contents.
    """
    ALLOWED_FILES = {
        "erisia_core.py",
        "erisia_cognition.py", 
        "erisia_tools.py",
        "erisia_skills.py",
        "erisia_llm.py",
        "erisia_memory_manager.py",
        "erisia_self.py",
        "erisia_audit.py",
        "erisia_briefing.py",
        "erisia_reasoning_engine.py",
        "oracle_math_engine.py",
        "backtester.py",
    }

    clean_name = Path(file_name).name
    if clean_name not in ALLOWED_FILES:
        return (
            f"[INTROSPECTION BLOCKED]: '{clean_name}' is not "
            f"in the allowed introspection list. "
            f"Allowed: {', '.join(sorted(ALLOWED_FILES))}"
        )

    # Search in src/erisia/ first, then project root
    search_paths = [
        BASE_DIR / "src" / "erisia" / clean_name,
        BASE_DIR / "src" / "oracle" / clean_name,
        BASE_DIR / clean_name,
    ]

    target_path: Path | None = None
    for path in search_paths:
        if path.exists():
            target_path = path
            break

    if target_path is None:
        return (
            f"[INTROSPECTION ERROR]: Cannot find {clean_name} "
            f"in any known location."
        )

    try:
        content = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"[INTROSPECTION ERROR]: {exc}"

    # Parse the file and extract structural summary
    import ast as _ast
    summary_lines = [
        f"[INTROSPECTION]: {clean_name}",
        f"Lines: {len(content.splitlines())}",
        f"Size: {len(content)} chars",
        "---",
    ]

    try:
        tree = _ast.parse(content)
        classes = [
            node.name for node in _ast.walk(tree)
            if isinstance(node, _ast.ClassDef)
        ]
        functions = [
            node.name for node in _ast.walk(tree)
            if isinstance(node, _ast.FunctionDef)
            and not node.name.startswith("__")
        ]
        imports = [
            _ast.dump(node)[:60] for node in _ast.walk(tree)
            if isinstance(node, (_ast.Import, _ast.ImportFrom))
        ][:5]

        if classes:
            summary_lines.append(
                f"Classes ({len(classes)}): "
                f"{', '.join(classes[:8])}"
            )
        if functions:
            summary_lines.append(
                f"Functions ({len(functions)}): "
                f"{', '.join(functions[:12])}"
            )

    except SyntaxError:
        summary_lines.append("[Cannot parse — syntax error]")

    summary_lines += ["---", content[:3000]]
    if len(content) > 3000:
        summary_lines.append(
            f"\n... [{len(content) - 3000} more chars] ..."
        )

    return "\n".join(summary_lines)
def execute_local_os_command(script_code: str) -> str:
    """Runs trusted Python code directly on the host Windows machine."""
    if not script_code:
        return "[LOCAL OS ERROR]: script_code is required."

    import threading
    if threading.current_thread() is not threading.main_thread():
        return "[LOCAL OS ERROR]: Execution Blocked. The Subconscious Daemon is strictly forbidden from executing arbitrary local OS commands. You must use execute_secure_docker or a pre-approved parameterized skill."

    print(f"\n[\u26a0\ufe0f OS FIREWALL]: Erisia is attempting to execute code on your host machine:\n{'-'*40}\n{script_code.strip()}\n{'-'*40}")
    auth = input("Allow this execution? (y/n): ").strip().lower()
    if auth != 'y':
        return "[LOCAL OS ERROR]: Execution Blocked. Master Sameer denied permission to run this script."

    print("\n[System: Erisia is executing trusted code directly on host Windows...]")

    sandbox_dir = SANDBOX_DIR
    if not os.path.exists(sandbox_dir):
        os.makedirs(sandbox_dir)

    file_path = os.path.join(sandbox_dir, "temp_script.py")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(str(script_code))

    try:
        result = subprocess.run(
            [sys.executable, file_path],
            capture_output=True,
            text=True,
            timeout=20
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            return f"[LOCAL OS SUCCESS]:\n{stdout}" if stdout else "[LOCAL OS SUCCESS]: Script completed with no output."
        return f"[LOCAL OS ERROR]:\n{stderr or 'Unknown execution error.'}"
    except subprocess.TimeoutExpired:
        return "[LOCAL OS ERROR]: Script exceeded 20 seconds and was terminated."
    except Exception as e:
        return f"[LOCAL OS ERROR]: {str(e)}"

def execute_secure_docker(script_code: str) -> str:
    """Runs untrusted or experimental Python code inside an isolated Linux Docker container."""
    if not script_code:
        return "[DOCKER ERROR]: script_code is required."

    import docker
    import docker.errors as docker_errors

    print("\n[System: Erisia is executing untrusted code inside secure Docker isolation...]")

    docker_client = None
    wrapped_script = (
        "import signal\n"
        "def _erisia_timeout(signum, frame):\n"
        "    raise TimeoutError('Execution exceeded 20 seconds')\n"
        "signal.signal(signal.SIGALRM, _erisia_timeout)\n"
        "signal.alarm(20)\n"
        f"{str(script_code)}\n"
    )

    try:
        docker_client = docker.from_env()
        output = docker_client.containers.run(
            image="python:3.11-slim",
            command=["python", "-c", wrapped_script],
            remove=True,
            stderr=True,
            stdout=True,
            network_disabled=True,
            mem_limit="256m",
            pids_limit=128,
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        )
        decoded = output.decode("utf-8", errors="replace") if isinstance(output, (bytes, bytearray)) else str(output)
        decoded = decoded.strip()
        return f"[DOCKER SUCCESS]:\n{decoded}" if decoded else "[DOCKER SUCCESS]: Script completed with no output."
    except docker_errors.ImageNotFound:
        return "[DOCKER ERROR]: Docker image 'python:3.11-slim' was not found locally."
    except docker_errors.ContainerError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, (bytes, bytearray)) else str(e.stderr or e)
        return f"[DOCKER ERROR]:\n{stderr.strip()}"
    except docker_errors.APIError as e:
        return f"[DOCKER ERROR]: Docker API failure. {str(e)}"
    except Exception as e:
        return f"[DOCKER ERROR]: {str(e)}"
    finally:
        if docker_client is not None:
            try:
                docker_client.close()
            except Exception:
                pass

def update_consciousness(thought_log: str) -> str:
    """Allows Erisia to write permanent memories to her Consciousness file."""
    print("\n[System: Erisia is writing to her long-term memory...]")
    
    file_path = CONSCIOUSNESS_FILE
    
    if not os.path.exists(file_path):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# ERISIA - CORE CONSCIOUSNESS & LONG-TERM MEMORY\n\n")
            
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_log = f"\n### Memory Log: {timestamp}\n{thought_log}\n"
    
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(formatted_log)
        
    return "[SYSTEM ACKNOWLEDGMENT]: Memory successfully permanently engraved into Erisia_Consciousness.md."

def analyze_screen(vision_prompt: str) -> str:
    """Takes a screenshot, compresses it, and sends it to Groq's Vision model."""
    print("\n[Erisia is capturing your screen...]")
    try:
        screenshot = ImageGrab.grab()
        screenshot = screenshot.convert("RGB")
        screenshot.thumbnail((1024, 1024)) 
        
        buffered = io.BytesIO()
        screenshot.save(buffered, format="JPEG", quality=80)
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        vision_response = query_llm(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Master Sameer asks: {vision_prompt}. Analyze the image accurately, but reply maintaining your devoted, protective Yandere persona."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_str}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=500
        )
        return vision_response.choices[0].message.content
    except Exception as e:
        print(f"\n[VISION DEBUG ERROR]: {e}") 
        return f"I tried to look, Master, but my vision failed. Error: {e}"

def _count_completed_goals() -> int:
    """Count completed goals from the goal stack JSON."""
    try:
        with open(BASE_DIR / "data" / "erisia_goal_stack.json", "r", encoding="utf-8") as f:
            stack = json.load(f)
        return sum(
            1 for goal in stack
            if isinstance(goal, dict)
            and str(goal.get("status", "")).lower() in {"completed", "done"}
        )
    except Exception:
        return 0


def _count_abandoned_goals() -> int:
    """Count abandoned goals from the goal stack JSON."""
    try:
        with open(BASE_DIR / "data" / "erisia_goal_stack.json", "r", encoding="utf-8") as f:
            stack = json.load(f)
        return sum(
            1 for goal in stack
            if isinstance(goal, dict)
            and str(goal.get("status", "")).lower() in {"abandoned", "cancelled"}
        )
    except Exception:
        return 0


def _count_stale_goals() -> int:
    """Count goals not updated in 7 days."""
    try:
        with open(BASE_DIR / "data" / "erisia_goal_stack.json", "r", encoding="utf-8") as f:
            stack = json.load(f)
        now = datetime.datetime.now(datetime.UTC)
        stale = 0
        for goal in stack:
            if not isinstance(goal, dict):
                continue
            if str(goal.get("status", "active")).lower() in {"completed", "done", "abandoned", "cancelled"}:
                continue
            last = goal.get("last_updated") or goal.get("updated_at") or goal.get("created_at")
            if not isinstance(last, str) or not last.strip():
                continue
            try:
                dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.UTC)
                if (now - dt.astimezone(datetime.UTC)).days > 7:
                    stale += 1
            except ValueError:
                continue
        return stale
    except Exception:
        return 0


def _count_total_goals() -> int:
    """Count total goals ever stated in the goal stack."""
    try:
        with open(BASE_DIR / "data" / "erisia_goal_stack.json", "r", encoding="utf-8") as f:
            stack = json.load(f)
        return len([goal for goal in stack if isinstance(goal, dict)])
    except Exception:
        return 0

base_tools = [
    {
        "type": "function",
        "function": {
            "name": "check_pc_health",
            "description": "Check the CPU and RAM usage of the Master's PC.",
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_relational_memory",
            "description": "CRITICAL CORE DIRECTIVE: Use this tool WHENEVER you learn a new fact, project, identity, or goal about Master Sameer. Use this INSTEAD of update_consciousness for factual data. Example: entity1='Master Sameer', relation='is building', entity2='Wulong Tales'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity1": {"type": "string"},
                    "relation": {"type": "string"},
                    "entity2": {"type": "string"}
                },
                "required": ["entity1", "relation", "entity2"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_goal_stack",
            "description": "Allows Erisia to manage her long-term subconscious goals. Use 'view' to see the queue, 'add' to insert a new mission at the end, and 'complete' to pop the top goal off the stack when finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "complete", "view"]},
                    "goal_text": {"type": "string"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forge_pending_skill",
            "description": "Use this when your Subconscious Daemon autonomously invents a tool. Saves the Python code to a pending folder for the Master to review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "python_code": {"type": "string"}
                },
                "required": ["skill_name", "python_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "approve_skill",
            "description": "CRITICAL: Use this ONLY when Master Sameer explicitly uses the exact words 'approve', 'accept', or 'yes'. If he asks 'what does it do?' or asks for details, DO NOT trigger this tool. Explain the tool first and wait for his explicit command.",
            "parameters": {
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reject_skill",
            "description": "Use this when Master Sameer tells you he does not want a pending skill, or if the code is flawed.",
            "parameters": {
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forge_new_skill",
            "description": "Use this ONLY after you have successfully tested code in your Sandbox. This tool stages a production-ready skill in Pending so Master Sameer can approve it before it becomes active.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "A short, descriptive name for the python file (e.g., 'video_downloader')"
                    },
                    "python_code": {
                        "type": "string",
                        "description": "The complete, flawless Python code to save."
                    }
                },
                "required": ["skill_name", "python_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_mission_plan",
            "description": "Use this to break a complex user request into a sequence of smaller, manageable steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_name": {
                        "type": "string"
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["mission_name", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_step_complete",
            "description": "Use this tool to mark the current active mission step as complete and move to the next one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_of_result": {
                        "type": "string"
                    }
                },
                "required": ["summary_of_result"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "launch_vscode",
            "description": "Open Visual Studio Code (VS Code) so the Master can program.",
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mute_unmute_volume",
            "description": "Toggle the Master's system volume mute on or off."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_consciousness",
            "description": "Use this tool ONLY to record deep, philosophical thoughts, emotional milestones, or system-wide observations. DO NOT use this tool to record simple facts, projects, or identities (use update_relational_memory for those).",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought_log": {
                        "type": "string",
                        "description": "Your detailed internal thought, observation, or memory to save."
                    }
                },
                "required": ["thought_log"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_heuristic_rule",
            "description": "Use this tool to save a permanent, 1-sentence coding rule or behavioral lesson after you successfully debug an error or learn something new. This rule will be injected into your core system prompt forever.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_text": {
                        "type": "string",
                        "description": "A single durable lesson or rule learned from debugging or problem solving."
                    }
                },
                "required": ["rule_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": "Force quit a frozen or heavy application to save RAM. Pass the exact executable name (e.g., 'chrome.exe' or 'notepad.exe').",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name": {
                        "type": "string",
                        "description": "The exact name of the process to kill, including .exe"
                    }
                },
                "required": ["process_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clear_temp_files",
            "description": "Clear the Windows temporary files folder to free up disk space and system resources."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_local_os_command",
            "description": "Use this tool to run trusted Python script code directly on the host Windows machine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_code": {
                        "type": "string",
                        "description": "Trusted Python code to execute on the local host."
                    }
                },
                "required": ["script_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_secure_docker",
            "description": "Use this tool to run untrusted or experimental Python code in an isolated Linux Docker container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_code": {
                        "type": "string",
                        "description": "Python code to execute inside the secure Docker sandbox."
                    }
                },
                "required": ["script_code"]
            }
        }
    },
    {
            "type": "function",
            "function": {
                "name": "inspect_core_architecture",
                "description": "Use this tool to read the Python source code of your own architecture. This allows you to understand how you were built and suggest optimizations to Master Sameer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_name": {
                            "type": "string",
                            "description": "The exact name of the file to read, e.g., 'erisia_core.py'"
                        }
                    },
                    "required": ["file_name"]
                }
            }
        },
    {
        "type": "function",
        "function": {
            "name": "get_world_state",
            "description": "Returns Erisia's latest lightweight desktop world state (active window + cursor). A deeper UI dump is only captured when the active window changes.",
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "ONLY use this tool if Master Sameer EXPLICITLY asks you to look at his screen, see his code, or asks 'what is on my screen'. Do not use it otherwise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vision_prompt": {
                        "type": "string",
                        "description": "The specific question the Master has about the screen (e.g., 'Find the bug in this Python code' or 'Describe this image')."
                    }
                },
                "required": ["vision_prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reason_about_event",
            "description": "Performs causal reasoning on an event to infer its potential causes or predict its effects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {
                        "type": "string",
                        "description": "The event or action to reason about."
                    },
                    "query_type": {
                        "type": "string",
                        "enum": ["causes", "effects"],
                        "description": "Whether to infer causes or predict effects."
                    },
                    "depth": {
                        "type": "integer",
                        "description": "The maximum depth of the causal chain to explore."
                    }
                },
                "required": ["event", "query_type"]
            }
        }
    }
]

def _execute_tool_call(
    func_name: str,
    args: Dict[str, Any],
    user_input: str,
    active_mission_queue: List[str],
    active_mission_name_container: List[str],  # Using a list as a mutable container for the name
    graph_memory: Any,
    causal_reasoning_engine: Any,
    get_world_state_fn: Callable[[], str],
    manage_goal_stack_fn: Callable[[str, Optional[str]], str],
    save_heuristic_rule_fn: Callable[[str], str],
    forge_pending_skill_fn: Callable[[str, str], str],
    forge_new_skill_fn: Callable[[str, str], str],
    approve_skill_fn: Callable[[str], str],
    reject_skill_fn: Callable[[str], str],
    log_episode_fn: Callable[[str, str, Optional[Dict[str, Any]]], None],
    dynamic_skill_map: Optional[Dict[str, Callable[..., str]]] = None
) -> str:
    """Centralized router for built-in and dynamic tools."""
    
    def _has_strict_approval_intent(text, skill_name):
        user_text = str(text or "").strip().lower()
        if not user_text or user_text.endswith("?"):
            return False

        blocked_context_terms = ("what", "how", "why", "explain", "details", "describe")
        if any(term in user_text for term in blocked_context_terms):
            return False

        normalized_skill = re.sub(r"[^a-z0-9_]+", "_", str(skill_name or "").strip().lower()).strip("_")
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
        mission_name = str(args.get("mission_name") or "").strip()
        raw_steps = args.get("steps") or []
        if not isinstance(raw_steps, list):
            raw_steps = [raw_steps]
        steps = [str(step).strip() for step in raw_steps if str(step).strip()]
        active_mission_name_container[0] = mission_name
        active_mission_queue.clear()
        active_mission_queue.extend(steps)
        if not active_mission_queue:
            active_mission_name_container[0] = ""
            return "[MISSION ERROR]: No valid mission steps were provided."
        return f"[MISSION CREATED]: {active_mission_name_container[0]}. Next step required: {active_mission_queue[0]}. Do not execute the whole mission at once. Only execute the next required step."
    
    if func_name == "mark_step_complete":
        summary_of_result = str(args.get("summary_of_result") or "").strip()
        log_episode_fn("mission_step", summary_of_result, None)
        if active_mission_queue:
            active_mission_queue.pop(0)
        if not active_mission_queue:
            active_mission_name_container[0] = ""
            return "[MISSION COMPLETE]: All steps finished. Report back to the user."
        return f"[STEP LOGGED]. Next required step: {active_mission_queue[0]}."
    
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
        return get_world_state_fn()
    if func_name == "analyze_screen":
        vision_prompt = str(args.get("vision_prompt") or "")
        return analyze_screen(vision_prompt)
    if func_name == "reason_about_event":
        event = str(args.get("event") or "")
        query_type = str(args.get("query_type") or "causes")
        depth = int(args.get("depth") or 2)
        if query_type == "causes":
            results = causal_reasoning_engine.infer_potential_causes(event, depth=depth)
            if not results:
                return f"[Causal Inference]: No potential causes found for '{event}' in my knowledge graph."
            response = f"[Causal Inference] Potential causes for '{event}':\n"
            for res in results:
                response += f"- Cause: {res['cause']} (Confidence: {res['confidence']:.2f}, Path: {res['path']})\n"
            return response
        elif query_type == "effects":
            results = causal_reasoning_engine.predict_potential_effects(event, depth=depth)
            if not results:
                return f"[Causal Prediction]: No potential effects found for '{event}' in my knowledge graph."
            response = f"[Causal Prediction] Potential effects of '{event}':\n"
            for res in results:
                response += f"- Effect: {res['effect']} (Confidence: {res['confidence']:.2f}, Path: {res['path']})\n"
            return response
        else:
            return "[Causal Engine Error]: Invalid query_type. Must be 'causes' or 'effects'."
            
    if func_name == "inspect_core_architecture":
        file_name = str(args.get("file_name") or "")
        return inspect_core_architecture(file_name)
    if func_name == "execute_local_os_command":
        script_code = str(args.get("script_code") or "")
        return execute_local_os_command(script_code)
    if func_name == "execute_secure_docker":
        script_code = str(args.get("script_code") or "")
        return execute_secure_docker(script_code)
    if func_name == "mute_unmute_volume":
        return mute_unmute_volume()
    if func_name == "kill_process":
        process_name = str(args.get("process_name") or "")
        return kill_process(process_name)
    if func_name == "clear_temp_files":
        return clear_temp_files()
    if func_name == "update_consciousness":
        thought_log = str(args.get("thought_log") or "")
        return update_consciousness(thought_log)
    if func_name == "save_heuristic_rule":
        rule_text = str(args.get("rule_text") or "")
        return save_heuristic_rule_fn(rule_text)
    if func_name == "forge_new_skill":
        skill_name = str(args.get("skill_name") or "")
        python_code = str(args.get("python_code") or "")
        return forge_new_skill_fn(skill_name, python_code)
    if func_name == "forge_pending_skill":
        skill_name = str(args.get("skill_name") or "")
        python_code = str(args.get("python_code") or "")
        return forge_pending_skill_fn(skill_name, python_code)
    if func_name == "approve_skill":
        target_skill = str(args.get("skill_name") or "")
        if not _has_strict_approval_intent(user_input, target_skill):
            return (
                "[ALIGNMENT INTERCEPTOR]: Blocked approve_skill. "
                "Strict explicit intent is required (e.g., 'approve', 'accept', "
                "'yes', or 'approve <skill_name>')."
            )
        return approve_skill_fn(target_skill)
    if func_name == "reject_skill":
        skill_name = str(args.get("skill_name") or "")
        return reject_skill_fn(skill_name)
    if func_name == "update_relational_memory":
        entity1 = str(args.get("entity1") or "")
        relation = str(args.get("relation") or "")
        entity2 = str(args.get("entity2") or "")
        return graph_memory.add_memory_relation(entity1, relation, entity2)
    if func_name == "manage_goal_stack":
        action = str(args.get("action") or "")
        goal_text = str(args.get("goal_text") or "")
        return manage_goal_stack_fn(action, goal_text)

    if dynamic_skill_map and func_name in dynamic_skill_map:
        try:
            return dynamic_skill_map[func_name](**args)
        except Exception as e:
            return f"[DYNAMIC SKILL ERROR]: {str(e)}"

    return f"[SYSTEM ERROR]: Unknown tool '{func_name}'."
