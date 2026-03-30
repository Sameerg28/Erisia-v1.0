from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_MCP_PROCESS: subprocess.Popen | None = None


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
        return f"[MCP]: started with transport={transport}."
    except Exception as exc:
        _MCP_PROCESS = None
        return f"[MCP]: failed to start ({exc})."


def stop_optional_mcp_service() -> None:
    """Stop optional MCP sidecar if it was started by runtime services."""
    global _MCP_PROCESS
    proc = _MCP_PROCESS
    _MCP_PROCESS = None
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
