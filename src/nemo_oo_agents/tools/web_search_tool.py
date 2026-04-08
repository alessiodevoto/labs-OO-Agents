# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Web search tool for agents.

Provides web search capabilities using DuckDuckGo (no API key required).
"""

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nemo_oo_agents.config.tool_configs import WebSearchConfig


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str

    def __str__(self) -> str:
        return f"{self.title}\n{self.url}\n{self.snippet}"


class WebSearchTool:
    """Web search tool using DuckDuckGo.

    Example:
        search = WebSearchTool()
        results = search.search("python async programming")
        for r in results:
            print(f"{r.title}: {r.url}")
    """

    def __init__(self, config: "WebSearchConfig | None" = None):
        """Initialize web search tool.

        Args:
            config: WebSearchConfig instance. Use WebSearchConfig(field=value) to override.
        """
        from nemo_oo_agents.config.tool_configs import WebSearchConfig as _WC

        self.config = config or _WC()

    def search(self, query: str, num_results: int | None = None) -> list[SearchResult]:
        """Search the web for a query.

        Args:
            query: Search query string
            num_results: Number of results to return (default: from config)

        Returns:
            List of SearchResult objects with title, url, snippet

        Example:
            results = search.search("best python web frameworks 2024")
            for r in results:
                print(f"- {r.title}")
                print(f"  {r.snippet}")
        """
        num = num_results or self.config.default_num_results
        return self._search_duckduckgo(query, num)

    def _search_duckduckgo(self, query: str, num_results: int) -> list[SearchResult]:
        """Search using DuckDuckGo HTML interface."""
        params = {"q": query}
        url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode(params)}"

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; nemo_oo_agents/1.0)",
        }

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.config.request_timeout) as response:
                html_content = response.read().decode("utf-8", errors="ignore")
        except Exception:
            return self._search_duckduckgo_api(query, num_results)

        return self._parse_duckduckgo_html(html_content, num_results)

    def _parse_duckduckgo_html(self, html_content: str, num_results: int) -> list[SearchResult]:
        """Parse DuckDuckGo HTML results page."""
        results = []

        result_pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>([^<]*)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        alt_pattern = re.compile(
            r'<h2[^>]*class="result__title"[^>]*>.*?'
            r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        for pattern in [result_pattern, alt_pattern]:
            matches = pattern.findall(html_content)
            for match in matches[:num_results]:
                if len(match) >= 3:
                    url, title, snippet = match[0], match[1], match[2]

                    if "uddg=" in url:
                        url_match = re.search(r"uddg=([^&]+)", url)
                        if url_match:
                            url = urllib.parse.unquote(url_match.group(1))

                    title = self._clean_html(title)
                    snippet = self._clean_html(snippet)

                    if title and url:
                        results.append(SearchResult(title=title, url=url, snippet=snippet))

            if results:
                break

        return results[:num_results]

    def _search_duckduckgo_api(self, query: str, num_results: int) -> list[SearchResult]:
        """Fallback: Use DuckDuckGo instant answer API."""
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        url = f"https://api.duckduckgo.com/?{urllib.parse.urlencode(params)}"

        headers = {"User-Agent": "Mozilla/5.0 (compatible; nemo_oo_agents/1.0)"}

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.config.request_timeout) as response:
                data = json.loads(response.read().decode())
        except Exception:
            return []

        results = []

        if data.get("Abstract"):
            results.append(
                SearchResult(
                    title=data.get("Heading", "Result"),
                    url=data.get("AbstractURL", ""),
                    snippet=data.get("Abstract", ""),
                )
            )

        for topic in data.get("RelatedTopics", [])[:num_results]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append(
                    SearchResult(
                        title=topic.get("Text", "")[:100],
                        url=topic.get("FirstURL", ""),
                        snippet=topic.get("Text", ""),
                    )
                )

        return results[:num_results]

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags and decode entities."""
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = " ".join(text.split())
        return text.strip()

    def fetch_url(self, url: str, timeout: float | None = None) -> str:
        """Fetch content from a URL.

        Args:
            url: URL to fetch
            timeout: Request timeout in seconds. Defaults to config.request_timeout.

        Returns:
            Page content as string
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; nemo_oo_agents/1.0)",
        }

        effective_timeout = timeout if timeout is not None else self.config.request_timeout
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=effective_timeout) as response:
            return response.read().decode("utf-8", errors="ignore")

    def fetch_url_text(self, url: str, timeout: float | None = None) -> str:
        """Fetch URL and extract text content (removes HTML tags).

        Args:
            url: URL to fetch
            timeout: Request timeout in seconds. Defaults to config.request_timeout.

        Returns:
            Plain text content from the page
        """
        html_content = self.fetch_url(url, timeout)
        return self._extract_text_from_html(html_content)

    def _extract_text_from_html(self, html_content: str) -> str:
        """Extract readable text from HTML."""
        html_content = re.sub(
            r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.I
        )
        html_content = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.I)
        html_content = re.sub(r"<!--.*?-->", "", html_content, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
