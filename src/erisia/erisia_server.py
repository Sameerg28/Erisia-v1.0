"""
Erisia v0.2 — REST API Server
================================
Lightweight FastAPI server for remote control of Erisia.

Endpoints:
  POST /ask          — Send a query (returns JSON response)
  GET  /health       — System diagnostic
  GET  /goals        — Current goal stack
  GET  /skills       — Skill registry
  POST /approve/{name} — Approve a pending skill
  POST /reject/{name}  — Reject a pending skill
  GET  /telemetry    — Telemetry summary
  GET  /learning     — Learning insights
  GET  /events       — Recent EventBus events
  WS   /ws           — WebSocket for streaming (future)

Usage:
  python -m erisia.erisia_server          # Start on port 8420
  python -m erisia.erisia_server --port 9000

Requirements:
  pip install fastapi uvicorn
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("erisia.server")

# Ensure paths
_BASE_DIR = Path(__file__).resolve().parents[2]
_SRC_DIR = _BASE_DIR / "src"
for _p in (_BASE_DIR, _SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


def create_app(query_handler=None):
    """
    Create and configure the FastAPI application.
    Accepts an optional query_handler function for /ask endpoint.
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
    except ImportError:
        logger.warning(
            "FastAPI not installed. Run: pip install fastapi uvicorn"
        )
        return None

    app = FastAPI(
        title="Erisia v0.2 API",
        description="REST API for remote control of the Erisia AGI system",
        version="0.2.0",
    )

    # CORS — allow local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request/Response Models ──────────────────────────────────────

    class AskRequest(BaseModel):
        query: str
        temperature: float = 0.7
        max_tokens: Optional[int] = None

    class AskResponse(BaseModel):
        response: str
        engine: str = "unknown"
        latency_ms: float = 0.0

    class GoalItem(BaseModel):
        title: str
        priority: float = 0.5
        status: str = "active"

    # ── Endpoints ────────────────────────────────────────────────────

    @app.get("/")
    def root():
        return {
            "name": "Erisia",
            "version": "0.2",
            "status": "online",
            "endpoints": [
                "/ask", "/health", "/goals", "/skills",
                "/telemetry", "/learning", "/events",
            ],
        }

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest):
        if not query_handler:
            raise HTTPException(
                status_code=503,
                detail="Query handler not connected",
            )
        start = time.time()
        try:
            response = query_handler(req.query)
            latency = (time.time() - start) * 1000
            return AskResponse(
                response=response,
                latency_ms=round(latency, 1),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/health")
    def health():
        try:
            from erisia.erisia_doctor import run_diagnostic
            return run_diagnostic()
        except Exception as exc:
            return {"error": str(exc)}

    @app.get("/goals")
    def goals():
        try:
            from erisia.erisia_config import get_config
            from erisia.erisia_cognition import GoalStack

            cfg = get_config()
            gs = GoalStack(str(cfg.paths.goal_stack_file))
            focus = gs.focus_snapshot(limit=10)
            return {
                "total": len(gs.goals),
                "top_goals": [
                    {
                        "title": g.get("title", g) if isinstance(g, dict) else str(g),
                        "priority": g.get("priority", 0.5) if isinstance(g, dict) else 0.5,
                        "status": g.get("status", "active") if isinstance(g, dict) else "active",
                    }
                    for g in focus
                ],
            }
        except Exception as exc:
            return {"error": str(exc)}

    @app.get("/skills")
    def skills():
        try:
            from erisia.erisia_config import get_config
            cfg = get_config()
            skills_dir = cfg.paths.skills_dir
            pending_dir = cfg.paths.pending_skills_dir

            active = [s.stem for s in skills_dir.glob("*.py")] if skills_dir.exists() else []
            pending = [s.stem for s in pending_dir.glob("*.py")] if pending_dir.exists() else []

            # Load registry
            registry = {}
            reg_file = skills_dir / "_registry.json"
            if reg_file.exists():
                try:
                    registry = json.loads(reg_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            return {
                "active": active,
                "pending": pending,
                "registry_count": len(registry),
            }
        except Exception as exc:
            return {"error": str(exc)}

    @app.post("/approve/{skill_name}")
    def approve(skill_name: str):
        try:
            from erisia.erisia_skills import approve_skill
            result = approve_skill(skill_name)
            return {"result": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/reject/{skill_name}")
    def reject(skill_name: str):
        try:
            from erisia.erisia_skills import reject_skill
            result = reject_skill(skill_name)
            return {"result": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/telemetry")
    def telemetry():
        try:
            from erisia.erisia_telemetry import get_telemetry_store
            store = get_telemetry_store()
            return {
                "daily": store.daily_summary(days=1),
                "weekly": store.daily_summary(days=7),
                "engine_distribution": store.engine_distribution(days=7),
            }
        except Exception as exc:
            return {"error": str(exc)}

    @app.get("/learning")
    def learning():
        try:
            from erisia.erisia_learning import get_learning_loop
            loop = get_learning_loop()
            return loop.analyze()
        except Exception as exc:
            return {"error": str(exc)}

    @app.get("/events")
    def events():
        try:
            from erisia.erisia_events import get_event_bus
            bus = get_event_bus()
            recent = bus.recent_events(limit=30)
            return {
                "stats": bus.stats(),
                "recent": [
                    {
                        "type": e.type.value,
                        "source": e.source,
                        "timestamp": e.timestamp,
                        "data_keys": list(e.data.keys()),
                    }
                    for e in recent
                ],
            }
        except Exception as exc:
            return {"error": str(exc)}

    return app


# ═══════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Erisia REST API Server")
    parser.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    app = create_app()
    if app is None:
        print("FastAPI not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)

    try:
        import uvicorn
        print(f"\n  Erisia v0.2 API Server")
        print(f"  http://{args.host}:{args.port}")
        print(f"  Docs: http://localhost:{args.port}/docs\n")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except ImportError:
        print("uvicorn not installed. Run: pip install uvicorn")
        sys.exit(1)
