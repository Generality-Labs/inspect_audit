"""The Tavily tool lists result pages and never asks Tavily for an answer."""

import asyncio
import json

import httpx
import pytest

from inspect_audit._search import TAVILY_URL, tavily_results


def test_tavily_results_lists_pages_without_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TAVILY_URL
        assert request.headers["Authorization"] == "Bearer test-key"
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "query": "q",
                "answer": "an LLM summary that must not be shown",
                "results": [
                    {
                        "title": "Niels Erik Norlund",
                        "url": "https://en.wikipedia.org/wiki/N",
                        "content": "set up stations in 1927",
                        "score": 0.9,
                    },
                    {
                        "title": "Seismology",
                        "url": "https://example.org/s",
                        "content": "Greenland stations",
                        "score": 0.5,
                    },
                ],
            },
        )

    search = tavily_results(transport=httpx.MockTransport(handler))
    text = asyncio.run(search(query="Norlund seismographic stations"))

    assert sent == [
        {"query": "Norlund seismographic stations", "max_results": 8, "include_answer": False}
    ]
    assert (
        "[1] Niels Erik Norlund\nhttps://en.wikipedia.org/wiki/N\nset up stations in 1927" in text
    )
    assert "https://example.org/s" in text
    assert "LLM summary" not in text
