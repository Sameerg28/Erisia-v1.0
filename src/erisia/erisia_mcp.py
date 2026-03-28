"""
Erisia v0.2 — Model Context Protocol (MCP) Server
====================================================
Exposes Erisia's tools and knowledge as MCP resources,
allowing external AI agents (Copilot, Claude, Cursor) to
use Erisia as a tool provider.

This transforms Erisia from a tool *user* into a tool *provider*,
a key step toward multi-agent AGI.

MCP Specification: https://modelcontextprotocol.io/

Exposed Tools:
  - erisia_ask          — Query Erisia's LLM with persona
  - erisia_search       — Search Erisia's vector memory
  - erisia_goals        — Get/set goals
  - erisia_health       — System diagnostic
  - erisia_forge_skill  — Create a new skill
  - erisia_telemetry    — Get cost/performance stats
  - erisia_learning     — Get learning insights

Exposed Resources:
  - erisia://memory      — Vector memory search
  - erisia://goals       — Goal stack
  - erisia://skills      — Skill registry
  - erisia://consciousness — Consciousness file

Usage:
  python -m erisia.erisia_mcp                    # Start stdio MCP server
  python -m erisia.erisia_mcp --transport sse    # SSE transport

Requirements:
  pip install mcp
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("erisia.mcp")

# Ensure paths
_BASE_DIR = Path(__file__).resolve().parents[2]
_SRC_DIR = _BASE_DIR / "src"
for _p in (_BASE_DIR, _SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


def create_mcp_server():
    """
    Create and configure the MCP server.
    Returns None if the mcp package is not installed.
    """
    try:
        from mcp.server import Server
        from mcp.types import (
            Tool,
            TextContent,
            Resource,
            ResourceTemplate,
        )
    except ImportError:
        logger.warning(
            "MCP package not installed. Run: pip install mcp"
        )
        return None

    server = Server("erisia")

    # ── Tool Definitions ─────────────────────────────────────────────

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="erisia_ask",
                description=(
                    "Ask Erisia a question or give a command. "
                    "Erisia will respond in her Yandere persona."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The question or command",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="erisia_search_memory",
                description="Search Erisia's vector memory for relevant knowledge",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "n_results": {
                            "type": "integer",
                            "description": "Number of results (default: 5)",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="erisia_goals",
                description="Get Erisia's current goal stack",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max goals to return",
                            "default": 5,
                        },
                    },
                },
            ),
            Tool(
                name="erisia_health",
                description="Run Erisia's system diagnostic",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="erisia_telemetry",
                description="Get Erisia's inference cost and performance statistics",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Days to analyze (default: 7)",
                            "default": 7,
                        },
                    },
                },
            ),
            Tool(
                name="erisia_learning",
                description="Get learning insights from Erisia's training data",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="erisia_forge_skill",
                description=(
                    "Request Erisia to forge a new Python skill. "
                    "The skill will be placed in pending for approval."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name for the new skill",
                        },
                        "python_code": {
                            "type": "string",
                            "description": "Python code for the skill",
                        },
                    },
                    "required": ["skill_name", "python_code"],
                },
            ),
        ]

    # ── Tool Handlers ────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "erisia_ask":
                from erisia.erisia_llm import query_llm
                query = arguments.get("query", "")
                response = query_llm(
                    messages=[{"role": "user", "content": query}],
                )
                text = response.choices[0].message.content
                return [TextContent(type="text", text=text)]

            elif name == "erisia_search_memory":
                from erisia.erisia_memory_manager import query_memory_documents
                query = arguments.get("query", "")
                n = arguments.get("n_results", 5)
                results = query_memory_documents(query, n_results=n)
                docs = results.get("documents", [[]])[0] if results else []
                return [TextContent(
                    type="text",
                    text=json.dumps(docs, indent=2),
                )]

            elif name == "erisia_goals":
                from erisia.erisia_config import get_config
                from erisia.erisia_cognition import GoalStack
                cfg = get_config()
                gs = GoalStack(str(cfg.paths.goal_stack_file))
                limit = arguments.get("limit", 5)
                summary = gs.summary_text(limit=limit)
                return [TextContent(type="text", text=summary)]

            elif name == "erisia_health":
                from erisia.erisia_doctor import doctor_as_tool
                return [TextContent(type="text", text=doctor_as_tool())]

            elif name == "erisia_telemetry":
                from erisia.erisia_telemetry import get_telemetry_store
                store = get_telemetry_store()
                days = arguments.get("days", 7)
                summary = store.daily_summary(days=days)
                dist = store.engine_distribution(days=days)
                result = {"summary": summary, "distribution": dist}
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )]

            elif name == "erisia_learning":
                from erisia.erisia_learning import get_learning_loop
                loop = get_learning_loop()
                return [TextContent(
                    type="text",
                    text=loop.as_tool_output(),
                )]

            elif name == "erisia_forge_skill":
                from erisia.erisia_skills import forge_pending_skill
                skill_name = arguments.get("skill_name", "")
                python_code = arguments.get("python_code", "")
                result = forge_pending_skill(skill_name, python_code)
                return [TextContent(type="text", text=result)]

            else:
                return [TextContent(
                    type="text",
                    text=f"Unknown tool: {name}",
                )]

        except Exception as exc:
            return [TextContent(
                type="text",
                text=f"Error executing {name}: {exc}",
            )]

    # ── Resource Definitions ─────────────────────────────────────────

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="erisia://consciousness",
                name="Erisia Consciousness",
                description="Erisia's permanent consciousness and identity file",
                mimeType="text/markdown",
            ),
            Resource(
                uri="erisia://goals",
                name="Goal Stack",
                description="Current active goals with priorities",
                mimeType="application/json",
            ),
            Resource(
                uri="erisia://skills",
                name="Skill Registry",
                description="All registered skills and their metadata",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        try:
            from erisia.erisia_config import get_config
            cfg = get_config()

            if uri == "erisia://consciousness":
                path = cfg.paths.consciousness_file
                if path.exists():
                    return path.read_text(encoding="utf-8")
                return "Consciousness file not found."

            elif uri == "erisia://goals":
                path = cfg.paths.goal_stack_file
                if path.exists():
                    return path.read_text(encoding="utf-8")
                return "[]"

            elif uri == "erisia://skills":
                reg_file = cfg.paths.skills_dir / "_registry.json"
                if reg_file.exists():
                    return reg_file.read_text(encoding="utf-8")
                return "{}"

            return f"Unknown resource: {uri}"

        except Exception as exc:
            return f"Error reading {uri}: {exc}"

    return server


# ═══════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Erisia MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mechanism (default: stdio)",
    )
    args = parser.parse_args()

    server = create_mcp_server()
    if server is None:
        print("MCP package not installed. Run: pip install mcp")
        sys.exit(1)

    import asyncio

    if args.transport == "stdio":
        from mcp.server.stdio import stdio_server

        async def main():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream)

        asyncio.run(main())
    else:
        print(f"Transport '{args.transport}' not yet implemented.")
        sys.exit(1)
