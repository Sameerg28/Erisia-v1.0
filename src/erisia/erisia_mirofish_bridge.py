from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any
from urllib import error, request


logger = logging.getLogger("erisia_mirofish")


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "mirofish_call",
        "description": (
            "Call MiroFish through Erisia orchestrator. "
            "Use for simulation and forecasting workflows when needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": (
                        "MiroFish API endpoint path (must start with '/'). "
                        "Example: '/health' or '/api/simulation/create'."
                    ),
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST"],
                    "description": "HTTP method. Defaults to GET.",
                },
                "payload_json": {
                    "type": "string",
                    "description": (
                        "Optional JSON object string for POST body. "
                        "Example: '{\"project_id\":\"proj_123\"}'."
                    ),
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Request timeout in seconds. Defaults to 60.",
                },
                "auto_start": {
                    "type": "boolean",
                    "description": (
                        "If true and connection fails, Erisia tries to auto-start "
                        "MiroFish from ERISIA_MIROFISH_BACKEND_DIR."
                    ),
                },
            },
            "required": ["endpoint"]
        }
    }
}


MIROFISH_BASE_URL = os.environ.get("ERISIA_MIROFISH_BASE_URL", "http://localhost:8080").rstrip("/")
MIROFISH_BACKEND_DIR = os.environ.get("ERISIA_MIROFISH_BACKEND_DIR", "")


def _health_check() -> bool:
    """Ping MiroFish /health endpoint."""
    try:
        req = request.Request(f"{MIROFISH_BASE_URL}/health", method="GET")
        with request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _try_autostart_mirofish() -> str:
    """Attempt to auto-start MiroFish from configured backend directory."""
    if not MIROFISH_BACKEND_DIR or not os.path.isdir(MIROFISH_BACKEND_DIR):
        return "[MIROFISH]: Cannot auto-start - ERISIA_MIROFISH_BACKEND_DIR not set or not a directory."

    startup_script = os.path.join(MIROFISH_BACKEND_DIR, "start.sh")
    if not os.path.exists(startup_script):
        startup_script = os.path.join(MIROFISH_BACKEND_DIR, "start.bat")
    if not os.path.exists(startup_script):
        return f"[MIROFISH]: Cannot auto-start - no start.sh or start.bat found in {MIROFISH_BACKEND_DIR}"

    try:
        subprocess.Popen(
            [sys.executable, "-m", "mirofish"],
            cwd=MIROFISH_BACKEND_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("MiroFish auto-start triggered.")
        time.sleep(3)
        return "[MIROFISH]: Auto-start initiated. Will retry health check..."
    except Exception as exc:
        return f"[MIROFISH]: Auto-start failed - {exc}"


def call_mirofish(
    endpoint: str,
    method: str = "GET",
    payload_json: str | None = None,
    timeout_seconds: float = 60.0,
    auto_start: bool = True,
) -> str:
    """
    Make an HTTP request to MiroFish API through Erisia's orchestrator.

    Returns a standardized result string. On success: "[MIROFISH SUCCESS]: <body>".
    On failure: "[MIROFISH ERROR]: <reason>".
    """
    if not endpoint.startswith("/"):
        return "[MIROFISH ERROR]: endpoint must start with '/'."

    url = f"{MIROFISH_BASE_URL}{endpoint}"

    if not _health_check():
        if auto_start:
            auto_msg = _try_autostart_mirofish()
            logger.info(auto_msg)
            if not _health_check():
                return "[MIROFISH ERROR]: MiroFish is not running. Set ERISIA_MIROFISH_BASE_URL or run MiroFish backend."
        else:
            return "[MIROFISH ERROR]: MiroFish is not running. Set ERISIA_MIROFISH_BASE_URL or enable auto_start."

    body: str | bytes | None = None
    if payload_json:
        try:
            body = json.dumps(json.loads(payload_json)).encode("utf-8")
        except json.JSONDecodeError as exc:
            return f"[MIROFISH ERROR]: Invalid payload_json - not valid JSON: {exc}"

    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    try:
        req = request.Request(url, data=body, headers=headers, method=method.upper())
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
            try:
                data = json.loads(raw)
                return f"[MIROFISH SUCCESS]: {json.dumps(data, ensure_ascii=False)}"
            except json.JSONDecodeError:
                return f"[MIROFISH SUCCESS]: {raw.decode('utf-8', errors='replace')}"

    except error.HTTPError as exc:
        try:
            err_body = exc.fp.read().decode("utf-8", errors="replace")
            return f"[MIROFISH ERROR] (HTTP {exc.code}): {err_body[:500]}"
        except Exception:
            return f"[MIROFISH ERROR] (HTTP {exc.code}): {exc.reason}"

    except error.URLError as exc:
        reason = str(exc.reason)
        if "Connection refused" in reason:
            return "[MIROFISH ERROR]: Connection refused. Is MiroFish running?"
        return f"[MIROFISH ERROR]: {reason}"

    except Exception as exc:
        return f"[MIROFISH ERROR]: {exc}"


if __name__ == "__main__":
    print(call_mirofish("/health"))
