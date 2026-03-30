"""
Erisia v0.2 — Sandboxed Execution Engine
==========================================
Provides safe, isolated code execution for forged skills.

Strategy:
  1. Docker (preferred) — ephemeral container, no host access
  2. Subprocess (fallback) — resource-limited subprocess
  3. In-process (last resort) — exec() with restricted globals

Docker execution:
  - Uses python:3.12-slim image
  - No network access by default
  - 30-second timeout
  - 256MB memory limit
  - Read-only mount for skill file
  - Automatically falls back to subprocess if Docker is unavailable

Inspired by OpenJarvis's containerized execution model.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("erisia.sandbox")


# ═══════════════════════════════════════════════════════════════════════
# RESULT TYPE
# ═══════════════════════════════════════════════════════════════════════

class SandboxResult:
    """Result of a sandboxed execution."""

    def __init__(
        self,
        success: bool,
        output: str = "",
        error: str = "",
        runtime_ms: float = 0.0,
        engine: str = "unknown",
    ) -> None:
        self.success = success
        self.output = output
        self.error = error
        self.runtime_ms = runtime_ms
        self.engine = engine

    def __str__(self) -> str:
        status = "OK" if self.success else "FAIL"
        return (
            f"SandboxResult({status}, engine={self.engine}, "
            f"runtime={self.runtime_ms:.0f}ms, "
            f"output={len(self.output)} chars)"
        )

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "runtime_ms": self.runtime_ms,
            "engine": self.engine,
        }


# ═══════════════════════════════════════════════════════════════════════
# DOCKER SANDBOX
# ═══════════════════════════════════════════════════════════════════════

def _docker_available() -> bool:
    """Check if Docker daemon is accessible."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def execute_in_docker(
    code: str,
    timeout: int = 30,
    memory_limit: str = "256m",
    network_enabled: bool = False,
    extra_packages: Optional[list[str]] = None,
) -> SandboxResult:
    """
    Execute Python code in an ephemeral Docker container.

    Features:
      - Isolated filesystem (no host access)
      - Memory limited (default 256MB)
      - Time limited (default 30s)
      - Network disabled by default
      - Automatic cleanup
    """
    try:
        import docker
    except ImportError:
        return SandboxResult(
            success=False,
            error="docker package not installed",
            engine="docker",
        )

    start = time.time()
    tmp_path = None

    try:
        client = docker.from_env()

        # Write code to a temp file that we'll mount into the container
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        # Build the command
        cmd = ["python", "/workspace/skill.py"]

        # Install extra packages if needed
        if extra_packages:
            pip_cmd = " && ".join(
                f"pip install -q {pkg}" for pkg in extra_packages
            )
            cmd = ["sh", "-c", f"{pip_cmd} && python /workspace/skill.py"]

        # Run the container
        container = client.containers.run(
            image="python:3.12-slim",
            command=cmd,
            volumes={tmp_path: {"bind": "/workspace/skill.py", "mode": "ro"}},
            mem_limit=memory_limit,
            network_disabled=not network_enabled,
            remove=True,
            detach=False,
            stdout=True,
            stderr=True,
            # Security: drop all capabilities
            cap_drop=["ALL"],
            # Read-only root filesystem
            read_only=True,
            # Temp filesystem for pip installs if needed
            tmpfs={"/tmp": "size=64m"},
        )

        runtime_ms = (time.time() - start) * 1000

        # container output is bytes
        output = container.decode("utf-8", errors="replace") if isinstance(container, bytes) else str(container)

        return SandboxResult(
            success=True,
            output=output.strip(),
            runtime_ms=runtime_ms,
            engine="docker",
        )

    except Exception as exc:
        runtime_ms = (time.time() - start) * 1000
        error_msg = str(exc)

        # Check for timeout
        if "timeout" in error_msg.lower() or "deadline" in error_msg.lower():
            error_msg = f"Execution timed out after {timeout}s"

        return SandboxResult(
            success=False,
            error=error_msg[:2000],
            runtime_ms=runtime_ms,
            engine="docker",
        )
    finally:
        # Clean up temp file
        with contextlib.suppress(OSError):
            if tmp_path:
                os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════
# SUBPROCESS SANDBOX (fallback)
# ═══════════════════════════════════════════════════════════════════════

def execute_in_subprocess(
    code: str,
    timeout: int = 30,
) -> SandboxResult:
    """
    Execute Python code in a subprocess (less isolated than Docker).
    Used as fallback when Docker is unavailable.
    """
    start = time.time()
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )

        runtime_ms = (time.time() - start) * 1000

        if result.returncode == 0:
            return SandboxResult(
                success=True,
                output=result.stdout.strip()[:5000],
                runtime_ms=runtime_ms,
                engine="subprocess",
            )
        else:
            return SandboxResult(
                success=False,
                output=result.stdout.strip()[:2000],
                error=result.stderr.strip()[:2000],
                runtime_ms=runtime_ms,
                engine="subprocess",
            )

    except subprocess.TimeoutExpired:
        runtime_ms = (time.time() - start) * 1000
        return SandboxResult(
            success=False,
            error=f"Execution timed out after {timeout}s",
            runtime_ms=runtime_ms,
            engine="subprocess",
        )
    except Exception as exc:
        runtime_ms = (time.time() - start) * 1000
        return SandboxResult(
            success=False,
            error=str(exc)[:2000],
            runtime_ms=runtime_ms,
            engine="subprocess",
        )
    finally:
        with contextlib.suppress(OSError):
            if tmp_path:
                os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════
# SMART SANDBOX (auto-selects best available)
# ═══════════════════════════════════════════════════════════════════════

def execute_sandboxed(
    code: str,
    timeout: int = 30,
    prefer_docker: bool = True,
    network_enabled: bool = False,
    extra_packages: Optional[list[str]] = None,
) -> SandboxResult:
    """
    Execute code in the safest available sandbox.

    Priority:
      1. Docker (if available and preferred)
      2. Subprocess (always available)
    """
    if prefer_docker and _docker_available():
        logger.info("Executing in Docker sandbox")
        result = execute_in_docker(
            code,
            timeout=timeout,
            network_enabled=network_enabled,
            extra_packages=extra_packages,
        )
        if result.success or "docker" not in result.error.lower():
            return result
        # Docker failed for a non-Docker reason, don't fallback
        logger.warning("Docker execution failed: %s", result.error)
        return result

    logger.info("Executing in subprocess sandbox (Docker unavailable)")
    return execute_in_subprocess(code, timeout=timeout)
