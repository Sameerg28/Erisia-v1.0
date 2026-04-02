import os
import subprocess
import logging
import docker
import docker.errors as docker_errors
import sys
import threading
import contextlib
from pathlib import Path

logger = logging.getLogger("erisia_execution")

BASE_DIR = Path(__file__).resolve().parents[1]
SANDBOX_DIR = BASE_DIR / "sandbox"

def inspect_core_architecture(file_name):
    """Allows Erisia to read her own source code."""
    print(f"\n[System: Erisia is inspecting her internal pathways in {file_name}...]")
    # THE SAFETY LOCK: She cannot read anything else on your PC with this tool.
    allowed_files = {
        "erisia_core.py": Path(__file__).resolve(),
        "erisia_missions.txt": (BASE_DIR / "config" / "erisia_missions.txt").resolve(),
    }

    safe_path = allowed_files.get(file_name)
    if safe_path is None:
        return f"[SYSTEM ERROR]: Access Denied. Master Sameer has restricted access to {file_name}."
    
    try:
        if not safe_path.exists():
            return f"[SYSTEM ERROR]: File not found at {safe_path}."
        with open(safe_path, 'r', encoding='utf-8') as file:
            return f"[SYSTEM LOG - Contents of {file_name}]:\n\n{file.read()}"
    except Exception as e:
        return f"[SYSTEM ERROR]: Failed to read file. Error: {str(e)}"
    
def execute_local_os_command(script_code):
    """Runs trusted Python code or shell commands directly on the host Windows machine."""
    if not script_code:
        return "[LOCAL OS ERROR]: script_code is required."

    # --- THREAD-AWARE OS FIREWALL ---
    import threading
    if threading.current_thread() is not threading.main_thread():
        return "[LOCAL OS ERROR]: Execution Blocked. The Subconscious Daemon is strictly forbidden from executing arbitrary local OS commands. You must use execute_secure_docker or a pre-approved parameterized skill."

    # Check if it looks like a shell command (no Python keywords/structures)
    is_shell_cmd = _is_shell_command(script_code)
    
    print(f"\n[⚠️ OS FIREWALL]: Erisia is attempting to execute code on your host machine:\n{'-'*40}\n{script_code.strip()}\n{'-'*40}")
    auth = input("Allow this execution? (y/n): ").strip().lower()
    if auth != 'y':
        return "[LOCAL OS ERROR]: Execution Blocked. Master Sameer denied permission to run this script."

    print("\n[System: Erisia is executing trusted code directly on host Windows...]")

    if is_shell_cmd:
        return _execute_shell_command(script_code.strip())
    
    return _execute_python_script(script_code)


def _is_shell_command(code: str) -> bool:
    """Heuristically determine if input is a shell command vs Python code."""
    code = code.strip().lower()
    
    # Python indicators
    python_keywords = ['def ', 'class ', 'import ', 'from ', 'if ', 'for ', 'while ', 'return ', 'print(', 'print ', '=', 'print(']
    # Shell-like patterns (no Python, starts with known commands)
    shell_indicators = ['pip', 'npm', 'node ', 'python ', 'python.exe', 'git ', 'docker ', 'cmd ', 'powershell', 'ls', 'dir', 'cd ', 'mirofish', 'mcp', 'npx']
    
    has_python_keyword = any(code.startswith(kw) or kw in code for kw in python_keywords)
    has_shell_indicator = any(code.startswith(sh) or code.split()[0] == sh for sh in shell_indicators if ' ' in sh or len(sh) > 2)
    
    # If no Python keywords and has shell-like patterns, treat as shell
    return not has_python_keyword and has_shell_indicator


def _execute_shell_command(cmd: str):
    """Execute a shell command on Windows."""
    import subprocess
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            shell=True,
            encoding='utf-8',
            errors='replace'
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            return f"[LOCAL OS SUCCESS]:\n{stdout}" if stdout else "[LOCAL OS SUCCESS]: Command completed with no output."
        return f"[LOCAL OS ERROR]: Exit code {result.returncode}\n{stderr or stdout}"
    except subprocess.TimeoutExpired:
        return "[LOCAL OS ERROR]: Command exceeded 30 seconds and was terminated."
    except Exception as e:
        return f"[LOCAL OS ERROR]: {str(e)}"


def _execute_python_script(script_code: str):
    """Execute Python script directly on the host."""
    import subprocess
    import sys
    import os
    
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


def execute_secure_docker(script_code):
    """Runs untrusted or experimental Python code inside an isolated Linux Docker container."""
    if not script_code:
        return "[DOCKER ERROR]: script_code is required."

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
            with contextlib.suppress(Exception):
                docker_client.close()
