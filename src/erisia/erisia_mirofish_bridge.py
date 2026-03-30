from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib import error, request


MIROFISH_TOOL_SCHEMA = {
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
            "required": ["endpoint"],
        },
    },
}

_MIROFISH_PROCESS: subprocess.Popen | None = None


def _health_check(base_url: str, timeout_value: float) -> bool:
    try:
        req = request.Request(
            url=base_url.rstrip("/") + "/health",
            method="GET",
            headers={"Accept": "application/json"},
        )
        with request.urlopen(req, timeout=timeout_value) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except Exception:
        return False


def _try_autostart_mirofish() -> str:
    global _MIROFISH_PROCESS
    if _MIROFISH_PROCESS is not None and _MIROFISH_PROCESS.poll() is None:
        return "[MIROFISH AUTOSTART]: Existing managed process is already running."

    backend_dir = os.environ.get("ERISIA_MIROFISH_BACKEND_DIR", "").strip()
    if not backend_dir:
        return (
            "[MIROFISH AUTOSTART ERROR]: ERISIA_MIROFISH_BACKEND_DIR is not set. "
            "Set it to MiroFish/backend path."
        )
    run_py = os.path.join(backend_dir, "run.py")
    if not os.path.exists(run_py):
        return f"[MIROFISH AUTOSTART ERROR]: run.py not found at {run_py}"

    try:
        _MIROFISH_PROCESS = subprocess.Popen(
            [sys.executable, run_py],
            cwd=backend_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return "[MIROFISH AUTOSTART]: Start command issued."
    except Exception as exc:
        return f"[MIROFISH AUTOSTART ERROR]: {exc}"


def call_mirofish(
    endpoint: str,
    method: str = "GET",
    payload_json: str = "",
    timeout_seconds: float = 60.0,
    auto_start: bool = False,
) -> str:
    """
    Erisia -> MiroFish bridge call.

    MiroFish base URL is read from ERISIA_MIROFISH_BASE_URL and defaults
    to http://127.0.0.1:5001.
    """
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return "[MIROFISH ERROR]: Missing 'endpoint'."
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    method = str(method or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        return "[MIROFISH ERROR]: method must be GET or POST."

    base_url = os.environ.get("ERISIA_MIROFISH_BASE_URL", "http://127.0.0.1:5001").strip()
    if not base_url:
        base_url = "http://127.0.0.1:5001"
    base_url = base_url.rstrip("/")
    url = base_url + endpoint

    try:
        timeout_value = max(1.0, float(timeout_seconds))
    except Exception:
        timeout_value = 60.0

    if auto_start and not _health_check(base_url, timeout_value=min(timeout_value, 5.0)):
        auto_msg = _try_autostart_mirofish()
        # Short warmup for first boot
        for _ in range(12):
            if _health_check(base_url, timeout_value=2.0):
                break
            time.sleep(0.5)
        else:
            return (
                f"{auto_msg}\n"
                "[MIROFISH ERROR]: Auto-start attempted, but /health is still unreachable."
            )

    body = None
    headers = {"Accept": "application/json"}
    if method == "POST":
        payload_obj = {}
        if payload_json:
            try:
                payload_obj = json.loads(payload_json)
            except Exception as exc:
                return f"[MIROFISH ERROR]: Invalid payload_json. {exc}"
            if not isinstance(payload_obj, dict):
                return "[MIROFISH ERROR]: payload_json must decode to a JSON object."
        body = json.dumps(payload_obj).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, method=method, headers=headers, data=body)

    try:
        with request.urlopen(req, timeout=timeout_value) as response:
            status = getattr(response, "status", 200)
            response_text = response.read().decode("utf-8", errors="replace")
        return (
            f"[MIROFISH OK] {method} {endpoint} -> HTTP {status}\n"
            f"{response_text[:4000]}"
        )
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        return (
            f"[MIROFISH HTTP ERROR] {method} {endpoint} -> HTTP {exc.code}\n"
            f"{detail[:4000]}"
        )
    except Exception as exc:
        return (
            f"[MIROFISH CONNECTION ERROR] {method} {endpoint}\n"
            f"Base URL: {base_url}\n"
            f"{exc}"
        )
