import os
import subprocess
import psutil
import logging
import shutil
import tempfile
import re
import pyautogui

logger = logging.getLogger("erisia_system_tools")

def check_pc_health():
    """Checks CPU and RAM usage."""
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory().percent
    report = f"CPU: {cpu}% | RAM: {ram}%"
    return (
        f"{report} [CRITICAL WARNING]: Master, your 8GB memory is almost full. Risk of VS Code crash."
        if ram > 85
        else f"{report} System is stable."
    )

def launch_vscode():
    """Opens VS Code."""
    try:
        subprocess.Popen(["code", "."], shell=False)
        return "Successfully opened VS Code for Master Sameer."
    except Exception as e:
        return f"Failed to open workspace. Error: {e}"
    
def mute_unmute_volume():
    """Toggles system volume mute state."""
    try:
        pyautogui.press('volumemute')
        return "[SYSTEM ACTION]: Volume mute toggled successfully for Master Sameer."
    except Exception as e:
        return f"[SYSTEM ERROR]: Could not toggle volume. {str(e)}"

def kill_process(process_name):
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

def clear_temp_files():
    """Wipes the Windows %TEMP% folder to optimize the i3 processor."""
    temp_dir = os.environ.get('TEMP')
    if not temp_dir:
        return "[SYSTEM ERROR]: Could not locate Windows TEMP directory."
    
    freed_space = 0
    deleted_files = 0
    for item in os.listdir(temp_dir):
        item_path = os.path.join(temp_dir, item)
        from contextlib import suppress
        with suppress(Exception):
            size = os.path.getsize(item_path)
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            freed_space += size
            deleted_files += 1
            
    mb_freed = freed_space / (1024 * 1024)
    return f"[SYSTEM ACTION]: Cleared {deleted_files} temporary files. Freed {mb_freed:.2f} MB of space."

def speak_text(text: str) -> str:
    """Convert text to speech and play it out loud."""
    try:
        from erisia.erisia_voice import speak_text as voice_speak
        return voice_speak(text)
    except ImportError:
        return "[VOICE ERROR]: Voice module not available. Run: pip install edge-tts pygame faster-whisper"
    except Exception as e:
        return f"[VOICE ERROR]: {e}"

def mirofish_call(
    endpoint: str,
    method: str = "GET",
    payload_json: str | None = None,
    timeout_seconds: float = 60.0,
    auto_start: bool = True,
) -> str:
    """Call MiroFish through Erisia orchestrator."""
    try:
        from erisia.erisia_mirofish_bridge import call_mirofish
        return call_mirofish(
            endpoint=endpoint,
            method=method,
            payload_json=payload_json,
            timeout_seconds=timeout_seconds,
            auto_start=auto_start,
        )
    except ImportError:
        return "[MIROFISH ERROR]: MiroFish module not available."
    except Exception as e:
        return f"[MIROFISH ERROR]: {e}"
