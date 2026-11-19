"""Tavily search that shows the investigator the results, not Tavily's answer.

Inspect's web_search Tavily provider forces include_answer and returns only that
answer as text; the result pages go into citations, which most providers drop. An
investigator auditing a key needs the pages, so this tool lists them.
"""

import os
from typing import Any

import httpx
from inspect_ai.tool import Tool, ToolError, tool

TAVILY_URL = "https://api.tavily.com/search"


def format_results(results: list[dict[str, Any]]) -> str:
    """Each result as title, URL and snippet."""
    if not results:
        return "No results."
    return "\n\n".join(
        f"[{n}] {r.get('title', '')}\n{r.get('url', '')}\n{r.get('content', '')}".strip()
        for n, r in enumerate(results, 1)
    )


@tool(name="web_search")
def tavily_results(max_results: int = 8, transport: httpx.AsyncBaseTransport | None = None) -> Tool:
    async def execute(query: str) -> str:
        """Search the web. Returns result titles, URLs and snippets; open the pages to read them.

        Args:
            query: Search query.
        """
        key = os.environ.get("TAVILY_API_KEY")
        if not key:
            raise ToolError("TAVILY_API_KEY is not set")
        try:
            async with httpx.AsyncClient(transport=transport, timeout=60) as client:
                response = await client.post(
                    TAVILY_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"query": query, "max_results": max_results, "include_answer": False},
                )
        except httpx.HTTPError as ex:
            raise ToolError(f"Tavily search failed: {ex}") from ex
        if response.status_code >= 400:
            raise ToolError(f"Tavily search failed ({response.status_code}): {response.text[:300]}")
        return format_results(response.json().get("results") or [])

    return execute
