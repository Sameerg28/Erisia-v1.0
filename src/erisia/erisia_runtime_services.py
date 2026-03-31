from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


logger = logging.getLogger("erisia_runtime")


_MCP_PROCESS: subprocess.Popen | None = None
_VOICE_INITIALIZED: bool = False


def _is_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def start_optional_mcp_service() -> str:
    """
    Start Erisia MCP server as an optional sidecar process.
    Controlled entirely by env flags to keep core behavior stable.
    """
    global _MCP_PROCESS

    if not _is_enabled(os.environ.get("ERISIA_ENABLE_MCP")):
        return "[MCP]: disabled (set ERISIA_ENABLE_MCP=1 to enable)."

    if _MCP_PROCESS is not None and _MCP_PROCESS.poll() is None:
        return "[MCP]: already running."

    transport = str(os.environ.get("ERISIA_MCP_TRANSPORT", "stdio")).strip().lower()
    if transport not in {"stdio", "sse"}:
        return f"[MCP]: invalid transport '{transport}'. Use stdio or sse."

    src_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    try:
        _MCP_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "erisia.erisia_mcp", "--transport", transport],
            cwd=str(src_dir.parent),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"MCP service started with transport={transport}")
        return f"[MCP]: started with transport={transport}."
    except Exception as exc:
        _MCP_PROCESS = None
        return f"[MCP]: failed to start ({exc})."


def stop_optional_mcp_service() -> str:
    """Stop the MCP sidecar process if running."""
    global _MCP_PROCESS

    if _MCP_PROCESS is None:
        return "[MCP]: not running."

    try:
        _MCP_PROCESS.terminate()
        _MCP_PROCESS.wait(timeout=5)
        _MCP_PROCESS = None
        return "[MCP]: stopped."
    except subprocess.TimeoutExpired:
        _MCP_PROCESS.kill()
        _MCP_PROCESS = None
        return "[MCP]: force-killed."
    except Exception as exc:
        _MCP_PROCESS = None
        return f"[MCP]: error stopping ({exc})."


def get_mcp_status() -> str:
    """Check if MCP sidecar is running."""
    global _MCP_PROCESS

    if _MCP_PROCESS is None:
        return "[MCP]: not running (set ERISIA_ENABLE_MCP=1 to start)."

    if _MCP_PROCESS.poll() is None:
        return f"[MCP]: running (PID {_MCP_PROCESS.pid})."
    else:
        return f"[MCP]: exited with code {_MCP_PROCESS.returncode}."


def initialize_optional_services() -> list[str]:
    """
    Initialize all optional services based on environment variables.
    Returns list of status messages.
    """
    results = []

    if _is_enabled(os.environ.get("ERISIA_ENABLE_MCP")):
        results.append(start_optional_mcp_service())
        try:
            from erisia.erisia_mcp_bridge import initialize_bridge_sync
            bridge_results = initialize_bridge_sync()
            for name, success in bridge_results.items():
                status = "connected" if success else "failed"
                results.append(f"[MCP BRIDGE] {name}: {status}")
        except Exception as exc:
            results.append(f"[MCP BRIDGE]: initialization failed ({exc})")

    if _is_enabled(os.environ.get("ERISIA_ENABLE_VOICE", "true")):
        try:
            from erisia.erisia_voice import get_voice_manager
            get_voice_manager()
            global _VOICE_INITIALIZED
            _VOICE_INITIALIZED = True
            results.append("[VOICE]: initialized.")
        except Exception as exc:
            results.append(f"[VOICE]: initialization failed ({exc})")

    return results


if __name__ == "__main__":
    print("Erisia Runtime Services")
    print("=" * 40)
    print(f"MCP: {get_mcp_status()}")
    print(initialize_optional_services())
