from __future__ import annotations

import json
import re
from typing import Any


def parse_tool_arguments(raw_arguments: Any) -> dict:
    """Safely parse tool arguments from JSON string/dict."""
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        stripped = raw_arguments.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extract_json_objects(text: Any) -> list[dict]:
    """Collect all top-level JSON objects from free-form text."""
    if not isinstance(text, str):
        return []
    objects: list[dict] = []
    stack: list[str] = []
    start_idx: int | None = None
    for idx, ch in enumerate(text):
        if ch == "{":
            if not stack:
                start_idx = idx
            stack.append("{")
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start_idx is not None:
                    candidate = text[start_idx : idx + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            objects.append(obj)
                    except Exception:
                        pass
                    start_idx = None
    return objects


def extract_text_tool_calls(content: Any) -> list[dict[str, Any]]:
    """
    Fallback parser for models that emit one or more tool-call JSON blocks in plain text.

    Returns: [{"name": <func_name>, "arguments": <dict>}]
    """
    if not isinstance(content, str) or not content.strip():
        return []

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            text,
            flags=re.DOTALL,
        ).strip()

    objs = extract_json_objects(text)
    calls: list[dict[str, Any]] = []
    for payload in objs:
        if not isinstance(payload, dict):
            continue

        func_name = payload.get("name")
        args = payload.get("parameters") or payload.get("arguments") or payload.get("args") or {}

        function_block = payload.get("function")
        if isinstance(function_block, dict):
            func_name = function_block.get("name", func_name)
            args = function_block.get("arguments", args)

        if payload.get("type") == "function" and payload.get("name"):
            func_name = payload.get("name")
            args = payload.get("parameters") or payload.get("arguments") or {}

        if not func_name:
            continue

        parsed_args = parse_tool_arguments(args)
        calls.append({"name": str(func_name), "arguments": parsed_args})

    return calls
