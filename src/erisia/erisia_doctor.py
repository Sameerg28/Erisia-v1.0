"""
Erisia v0.2 — System Doctor
=============================
Self-diagnostic tool inspired by OpenJarvis's `jarvis doctor` command.

Reports on:
  - API key availability
  - Ollama status
  - ChromaDB health
  - SQLite databases
  - Graph memory
  - Goal stack
  - Skills registry
  - Docker availability
  - System resources (RAM, disk)
  - Telemetry summary

Usage:
  python -m erisia.erisia_doctor          # Full diagnostic
  python -m erisia.erisia_doctor --json   # JSON output (for LLM consumption)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure paths
_BASE_DIR = Path(__file__).resolve().parents[2]
_SRC_DIR = _BASE_DIR / "src"
for _p in (_BASE_DIR, _SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


def _check_api_key(name: str, value: Optional[str]) -> dict:
    """Check if an API key is configured."""
    if value and len(value) > 4:
        masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "...configured"
        return {"name": name, "status": "ok", "display": masked}
    return {"name": name, "status": "missing", "display": "not configured"}


def _check_ollama(base_url: str) -> dict:
    """Check if Ollama is running and which models are available."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                return {
                    "name": "Ollama",
                    "status": "ok",
                    "display": f"running ({len(models)} models)",
                    "models": models,
                }
    except Exception:
        pass
    return {
        "name": "Ollama",
        "status": "not_running",
        "display": "not running (install: ollama.com)",
    }


def _check_chromadb(memory_dir: str) -> dict:
    """Check ChromaDB health."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=memory_dir)
        collections = client.list_collections()
        total_docs = 0
        for coll in collections:
            total_docs += coll.count()
        return {
            "name": "ChromaDB",
            "status": "ok",
            "display": f"{total_docs:,} documents in {len(collections)} collections",
        }
    except Exception as exc:
        return {"name": "ChromaDB", "status": "error", "display": str(exc)[:60]}


def _check_sqlite(db_path: Path, label: str) -> dict:
    """Check if a SQLite database exists and its size."""
    if not db_path.exists():
        return {"name": label, "status": "missing", "display": "not created yet"}
    size = db_path.stat().st_size
    if size < 1024:
        display = f"{size}B"
    elif size < 1024 * 1024:
        display = f"{size / 1024:.1f}KB"
    else:
        display = f"{size / (1024 * 1024):.1f}MB"
    return {"name": label, "status": "ok", "display": display}


def _check_graph_memory(base_dir: Path) -> dict:
    """Check the NetworkX graph memory."""
    graph_file = base_dir / "data" / "erisia_graph.json"
    if not graph_file.exists():
        return {"name": "Graph Memory", "status": "empty", "display": "no graph data"}
    try:
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        nodes = len(data.get("nodes", []))
        edges = len(data.get("links", data.get("edges", [])))
        size = graph_file.stat().st_size / 1024
        return {
            "name": "Graph Memory",
            "status": "ok",
            "display": f"{nodes} nodes, {edges} edges ({size:.1f}KB)",
        }
    except Exception as exc:
        return {"name": "Graph Memory", "status": "error", "display": str(exc)[:60]}


def _check_goal_stack(base_dir: Path) -> dict:
    """Check the goal stack."""
    gs_file = base_dir / "data" / "erisia_goal_stack.json"
    if not gs_file.exists():
        return {"name": "Goal Stack", "status": "empty", "display": "no goals"}
    try:
        data = json.loads(gs_file.read_text(encoding="utf-8"))
        count = len(data) if isinstance(data, list) else len(data.get("goals", []))
        return {"name": "Goal Stack", "status": "ok", "display": f"{count} active goals"}
    except Exception:
        return {"name": "Goal Stack", "status": "error", "display": "corrupted"}


def _check_skills(base_dir: Path) -> dict:
    """Check the skills registry."""
    skills_dir = base_dir / "skills"
    pending_dir = skills_dir / "pending"
    active = len(list(skills_dir.glob("*.py"))) if skills_dir.exists() else 0
    pending = len(list(pending_dir.glob("*.py"))) if pending_dir.exists() else 0
    registry_file = skills_dir / "_registry.json"
    registered = 0
    if registry_file.exists():
        try:
            registered = len(json.loads(registry_file.read_text(encoding="utf-8")))
        except Exception:
            pass
    return {
        "name": "Skills Registry",
        "status": "ok",
        "display": f"{active} active, {pending} pending, {registered} registered",
    }


def _check_docker() -> dict:
    """Check if Docker is available."""
    try:
        import docker
        client = docker.from_env()
        info = client.info()
        return {
            "name": "Docker",
            "status": "ok",
            "display": f"v{info.get('ServerVersion', '?')}",
        }
    except Exception:
        return {"name": "Docker", "status": "not_available", "display": "not available"}


def _check_system_resources() -> dict:
    """Check RAM and disk usage."""
    results = {}
    try:
        import psutil
        mem = psutil.virtual_memory()
        results["ram"] = {
            "name": "RAM",
            "status": "ok" if mem.percent < 90 else "warning",
            "display": f"{mem.percent}% used ({mem.available / (1024**3):.1f}GB free)",
        }
        disk = psutil.disk_usage(str(_BASE_DIR))
        results["disk"] = {
            "name": "Disk",
            "status": "ok" if disk.percent < 90 else "warning",
            "display": f"{disk.free / (1024**3):.1f}GB free ({disk.percent}% used)",
        }
    except ImportError:
        results["ram"] = {"name": "RAM", "status": "unknown", "display": "psutil not installed"}
        results["disk"] = {"name": "Disk", "status": "unknown", "display": "psutil not installed"}
    return results


def _check_telemetry(base_dir: Path) -> dict:
    """Check telemetry database."""
    telem_db = base_dir / "data" / "erisia_telemetry.db"
    if not telem_db.exists():
        return {
            "name": "Telemetry",
            "status": "not_started",
            "display": "no data yet (will start recording on next session)",
        }
    try:
        import sqlite3
        conn = sqlite3.connect(str(telem_db))
        row = conn.execute("SELECT COUNT(*) FROM inference_log").fetchone()
        total = row[0] if row else 0
        conn.close()
        return {
            "name": "Telemetry",
            "status": "ok",
            "display": f"{total:,} inference calls recorded",
        }
    except Exception as exc:
        return {"name": "Telemetry", "status": "error", "display": str(exc)[:60]}


# ═══════════════════════════════════════════════════════════════════════
# MAIN DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════

def run_diagnostic() -> dict[str, Any]:
    """Run all health checks and return structured results."""
    from erisia.erisia_config import get_config

    cfg = get_config()
    checks: list[dict] = []

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append({"name": "Python", "status": "ok", "display": py_ver})

    # API Keys
    checks.append(_check_api_key("Groq API Key", cfg.llm.groq_api_key))
    checks.append(_check_api_key("Together API Key", cfg.llm.together_api_key))
    checks.append(_check_api_key("Google API Key", cfg.llm.google_api_key))
    checks.append(_check_api_key("OpenRouter API Key", cfg.llm.openrouter_api_key))
    checks.append(_check_api_key("Tavily API Key", cfg.llm.tavily_api_key))

    # Ollama
    checks.append(_check_ollama(cfg.llm.ollama_base_url))

    # Memory systems
    checks.append(_check_chromadb(str(cfg.paths.memory_dir)))
    checks.append(_check_sqlite(cfg.paths.erisia_db, "SQLite Memory"))
    checks.append(_check_graph_memory(cfg.paths.base_dir))

    # Cognition
    checks.append(_check_goal_stack(cfg.paths.base_dir))
    checks.append(_check_skills(cfg.paths.base_dir))

    # Infrastructure
    checks.append(_check_docker())
    checks.append(_check_telemetry(cfg.paths.base_dir))

    # System resources
    sys_res = _check_system_resources()
    checks.append(sys_res.get("ram", {}))
    checks.append(sys_res.get("disk", {}))

    # Summary
    ok_count = sum(1 for c in checks if c.get("status") == "ok")
    warn_count = sum(1 for c in checks if c.get("status") in ("missing", "warning", "not_running", "not_available"))
    error_count = sum(1 for c in checks if c.get("status") == "error")

    return {
        "checks": checks,
        "summary": {
            "ok": ok_count,
            "warnings": warn_count,
            "errors": error_count,
            "total": len(checks),
        },
    }


def print_diagnostic(results: dict) -> None:
    """Print a diagnostic report to terminal (ASCII-safe for Windows)."""
    STATUS_ICONS = {
        "ok": "[OK]",
        "missing": "[!!]",
        "warning": "[!!]",
        "not_running": "[XX]",
        "not_available": "[!!]",
        "not_started": "[--]",
        "error": "[XX]",
        "empty": "[--]",
        "unknown": "[??]",
    }

    print()
    print("+----------------------------------------------------------+")
    print("|            ERISIA v0.2 SYSTEM DIAGNOSTIC                 |")
    print("+----------------------------------------------------------+")

    for check in results["checks"]:
        icon = STATUS_ICONS.get(check.get("status", "unknown"), "[??]")
        name = check.get("name", "?").ljust(22)
        display = check.get("display", "?")
        # Truncate display if too long
        max_display = 28
        if len(display) > max_display:
            display = display[:max_display - 3] + "..."
        line = f"| {icon} {name} {display}"
        padding = 59 - len(line)
        if padding < 0:
            padding = 0
        print(f"{line}{' ' * padding}|")

    print("+----------------------------------------------------------+")
    s = results["summary"]
    summary = f"|  {s['ok']} OK / {s['warnings']} Warnings / {s['errors']} Errors"
    padding = 59 - len(summary)
    print(f"{summary}{' ' * max(0, padding)}|")
    print("+----------------------------------------------------------+")
    print()


def doctor_as_tool() -> str:
    """
    Run the diagnostic and return results as a string.
    Can be registered as an Erisia tool for the LLM to call.
    """
    results = run_diagnostic()
    lines = []
    for check in results["checks"]:
        status = check.get("status", "unknown")
        icon = "✓" if status == "ok" else ("⚠" if status in ("missing", "warning") else "✗")
        lines.append(f"  {icon} {check['name']}: {check['display']}")

    s = results["summary"]
    lines.append(f"\nSummary: {s['ok']} OK, {s['warnings']} warnings, {s['errors']} errors")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = run_diagnostic()

    if "--json" in sys.argv:
        # JSON output for programmatic consumption
        print(json.dumps(results, indent=2, default=str))
    else:
        print_diagnostic(results)
