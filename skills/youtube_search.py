import webbrowser
from urllib.parse import quote_plus

# Erisia Skill - youtube_search v2
# Improved: accepts dynamic query parameter

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "youtube_search",
        "description": (
            "Search YouTube for any video or song. "
            "Opens the search results in the default browser."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search term to look up on YouTube",
                }
            },
            "required": ["query"],
        },
    },
}


def execute_skill(**kwargs) -> str:
    """Open YouTube search results for the given query."""
    query = str(kwargs.get("query", "")).strip()
    if not query:
        return "[SKILL ERROR]: No search query provided."

    url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    try:
        webbrowser.open(url)
        return f"[SKILL SUCCESS]: Opened YouTube search for '{query}'"
    except Exception as exc:
        return f"[SKILL ERROR]: {exc}"
