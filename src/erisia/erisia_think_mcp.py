"""
Erisia Sequential Thinking MCP Server — Lightweight Python Implementation
==========================================================================
Provides sequential thinking as an MCP server via stdio.
No Node.js required — pure Python, works on low-end devices.

Tool:
  - sequential_thinking — Break down complex problems into structured thinking steps

Usage:
  python -m erisia.erisia_think_mcp
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

logger = logging.getLogger("erisia.think_mcp")

_THOUGHT_LOG: list[dict] = []


async def run_server():
    """Run the sequential thinking MCP server via stdio."""
    stdin = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(stdin)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    stdout = sys.stdout.buffer

    async def send_response(response: dict):
        payload = json.dumps(response).encode("utf-8") + b"\n"
        stdout.write(payload)
        await asyncio.get_event_loop().run_in_executor(None, stdout.flush)

    async def read_message() -> dict | None:
        line = await stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    while True:
        msg = await read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            if msg_id is not None:
                await send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "erisia-sequential-thinking", "version": "0.1"},
                    },
                })

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list" and msg_id is not None:
            await send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": [
                    {
                        "name": "sequential_thinking",
                        "description": (
                            "Break down complex problems into manageable thinking steps. "
                            "Supports revision, branching, and dynamic adjustment of thought count. "
                            "Use this for complex analysis, planning, and problem-solving."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "thought": {
                                    "type": "string",
                                    "description": "The current thinking step",
                                },
                                "nextThoughtNeeded": {
                                    "type": "boolean",
                                    "description": "Whether another thought step is needed",
                                },
                                "thoughtNumber": {
                                    "type": "integer",
                                    "description": "Current thought number",
                                },
                                "totalThoughts": {
                                    "type": "integer",
                                    "description": "Estimated total thoughts needed",
                                },
                                "isRevision": {
                                    "type": "boolean",
                                    "description": "Whether this revises previous thinking",
                                },
                                "revisesThought": {
                                    "type": "integer",
                                    "description": "Which thought is being reconsidered",
                                },
                                "branchFromThought": {
                                    "type": "integer",
                                    "description": "Branching point thought number",
                                },
                                "branchId": {
                                    "type": "string",
                                    "description": "Branch identifier",
                                },
                                "needsMoreThoughts": {
                                    "type": "boolean",
                                    "description": "If more thoughts are needed",
                                },
                            },
                            "required": ["thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts"],
                        },
                    },
                ]},
            })

        elif method == "tools/call" and msg_id is not None:
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await _handle_tool(tool_name, arguments)
            await send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result,
            })

        elif msg_id is not None:
            await send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


async def _handle_tool(name: str, args: dict) -> dict:
    """Handle a tool call and return MCP content result."""
    if name != "sequential_thinking":
        return _error(f"Unknown tool: {name}")

    thought = args.get("thought", "")
    thought_num = args.get("thoughtNumber", 1)
    total = args.get("totalThoughts", 1)
    is_revision = args.get("isRevision", False)
    revises = args.get("revisesThought")
    branch_from = args.get("branchFromThought")
    branch_id = args.get("branchId")
    next_needed = args.get("nextThoughtNeeded", False)
    needs_more = args.get("needsMoreThoughts")

    entry = {
        "thoughtNumber": thought_num,
        "totalThoughts": total,
        "thought": thought,
        "isRevision": is_revision,
        "branchId": branch_id,
    }
    if revises is not None:
        entry["revisesThought"] = revises
    if branch_from is not None:
        entry["branchFromThought"] = branch_from

    _THOUGHT_LOG.append(entry)

    status_parts = []
    if is_revision:
        status_parts.append(f"Thought {thought_num} (revision of thought {revises})")
    elif branch_id:
        status_parts.append(f"Thought {thought_num} (branch: {branch_id})")
    else:
        status_parts.append(f"Thought {thought_num} of ~{total}")

    if needs_more:
        status_parts.append("More thoughts needed")
    elif not next_needed:
        status_parts.append("Thinking complete")
    else:
        status_parts.append("Continuing...")

    summary = {
        "thoughtNumber": thought_num,
        "totalThoughts": total,
        "nextThoughtNeeded": next_needed,
        "status": " | ".join(status_parts),
        "thoughtsLogged": len(_THOUGHT_LOG),
    }

    return _text(json.dumps(summary, indent=2))


def _text(content: str) -> dict:
    return {"content": [{"type": "text", "text": content}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_server())
