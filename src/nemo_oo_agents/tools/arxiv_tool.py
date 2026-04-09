# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ArXiv API tool for paper search and download.

This tool provides stateless access to the arXiv API. Rate limiting state
is managed by the agent, not the tool itself.
"""

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ArxivTool:
    """Stateless tool for arXiv API access.

    All methods that make API requests accept a last_request_time parameter
    and return (result, new_timestamp) tuples for the agent to manage rate limiting.

    arXiv API requires minimum 3 seconds between requests.
    """

    BASE_URL = "https://export.arxiv.org/api/query"
    MIN_REQUEST_INTERVAL = 3.0  # arXiv requirement

    def __init__(self, paper_class: type):
        """Initialize with the ArxivPaper class to use for constructing papers.

        Args:
            paper_class: The ArxivPaper Pydantic model class
        """
        self.paper_class = paper_class

    async def fetch_recent_papers(
        self,
        categories: list[str],
        days_back: int = 1,
        max_results: int = 100,
        last_request_time: float | None = None,
    ) -> tuple[list[Any], float]:
        """Fetch recent papers from specified categories.

        Args:
            categories: List of arXiv categories (e.g., ['cs.AI', 'cs.LG'])
            days_back: Number of days to look back
            max_results: Maximum number of results to return
            last_request_time: Unix timestamp of last API request (for rate limiting)

        Returns:
            Tuple of (papers, new_last_request_time)
        """
        # Build date range query
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        date_str = start_date.strftime("%Y%m%d")

        # Build category query
        category_queries = [f"cat:{cat}" for cat in categories]
        category_query = " OR ".join(category_queries)

        # Combine with date filter
        query = f"({category_query}) AND submittedDate:[{date_str}* TO *]"

        logger.info(f"Fetching papers: categories={categories}, days_back={days_back}")

        # Use search with the constructed query
        return await self.search_papers(
            query=query,
            max_results=max_results,
            last_request_time=last_request_time,
        )

    async def search_papers(
        self,
        query: str,
        max_results: int = 100,
        last_request_time: float | None = None,
    ) -> tuple[list[Any], float]:
        """Search arXiv papers with a query string.

        Args:
            query: arXiv API query string (e.g., 'cat:cs.AI AND ti:transformers')
            max_results: Maximum number of results
            last_request_time: Unix timestamp of last API request

        Returns:
            Tuple of (papers, new_last_request_time)
        """
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        response_text, new_timestamp = await self._rate_limited_request(
            params=params,
            last_request_time=last_request_time,
        )

        papers = self._parse_atom_response(response_text)
        logger.info(f"Found {len(papers)} papers")

        return papers, new_timestamp

    async def download_paper(
        self,
        paper: Any,  # ArxivPaper instance
        download_dir: Path,
        last_request_time: float | None = None,
    ) -> tuple[str | None, float]:
        """Download paper PDF to specified directory.

        Args:
            paper: ArxivPaper object with pdf_url
            download_dir: Directory to save PDF
            last_request_time: Unix timestamp of last API request

        Returns:
            Tuple of (pdf_path or None, new_last_request_time)
        """
        download_dir.mkdir(parents=True, exist_ok=True)

        # Create filename from paper ID
        filename = f"{paper.arxiv_id.replace('/', '_')}.pdf"
        pdf_path = download_dir / filename

        # Check if already downloaded
        if pdf_path.exists():
            logger.info(f"Paper already downloaded: {pdf_path}")
            return str(pdf_path), last_request_time or time.time()

        # Enforce rate limiting
        new_timestamp = await self._wait_for_rate_limit(last_request_time)

        logger.info(f"Downloading paper {paper.arxiv_id} from {paper.pdf_url}")

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(paper.pdf_url, follow_redirects=True)
                response.raise_for_status()

                # Write PDF
                pdf_path.write_bytes(response.content)
                logger.info(f"Downloaded to {pdf_path}")

                return str(pdf_path), new_timestamp

        except Exception as e:
            logger.error(f"Failed to download paper {paper.arxiv_id}: {e}")
            return None, new_timestamp

    async def _rate_limited_request(
        self,
        params: dict[str, Any],
        last_request_time: float | None = None,
        max_retries: int = 3,
    ) -> tuple[str, float]:
        """Make rate-limited request to arXiv API with exponential backoff.

        Args:
            params: Query parameters for API request
            last_request_time: Unix timestamp of last request
            max_retries: Maximum retry attempts

        Returns:
            Tuple of (response_text, new_last_request_time)

        Raises:
            Exception: If all retries fail
        """
        # Enforce rate limiting before first request
        new_timestamp = await self._wait_for_rate_limit(last_request_time)

        retry_delay = 1.0
        last_error = None

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(self.BASE_URL, params=params)
                    response.raise_for_status()
                    return response.text, new_timestamp

            except Exception as e:
                last_error = e
                logger.warning(
                    f"arXiv API request failed (attempt {attempt + 1}/{max_retries}): {e}"
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff

        # All retries failed
        raise Exception(f"arXiv API request failed after {max_retries} attempts: {last_error}")

    async def _wait_for_rate_limit(self, last_request_time: float | None) -> float:
        """Enforce rate limiting by waiting if needed.

        Args:
            last_request_time: Unix timestamp of last request

        Returns:
            New timestamp (now)
        """
        now = time.time()

        if last_request_time is not None:
            elapsed = now - last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                wait_time = self.MIN_REQUEST_INTERVAL - elapsed
                logger.debug(f"Rate limiting: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                now = time.time()

        return now

    def _parse_atom_response(self, xml_text: str) -> list[Any]:
        """Parse arXiv Atom feed response into paper objects.

        Args:
            xml_text: XML response from arXiv API

        Returns:
            List of paper objects (using self.paper_class)
        """
        try:
            root = ET.fromstring(xml_text)
            namespace = {"atom": "http://www.w3.org/2005/Atom"}

            papers = []
            for entry in root.findall("atom:entry", namespace):
                try:
                    paper = self._parse_entry(entry, namespace)
                    papers.append(paper)
                except Exception as e:
                    logger.warning(f"Failed to parse entry: {e}")
                    continue

            return papers

        except Exception as e:
            logger.error(f"Failed to parse XML response: {e}")
            return []

    def _parse_entry(self, entry: ET.Element, namespace: dict) -> Any:
        """Parse a single entry from Atom feed.

        Args:
            entry: XML entry element
            namespace: XML namespace mapping

        Returns:
            Paper object (using self.paper_class)
        """
        # Extract ID (remove version suffix)
        id_text = self._extract_tag(entry, "atom:id", namespace) or ""
        arxiv_id = id_text.split("/")[-1].split("v")[0]

        # Extract authors
        authors = []
        for author in entry.findall("atom:author", namespace):
            name = self._extract_tag(author, "atom:name", namespace)
            if name:
                authors.append(name)

        # Extract categories
        categories = []
        for category in entry.findall("atom:category", namespace):
            term = category.get("term")
            if term:
                categories.append(term)

        # Extract links
        pdf_url = ""
        html_url = ""
        for link in entry.findall("atom:link", namespace):
            href = link.get("href", "")
            title = link.get("title", "")
            if title == "pdf":
                pdf_url = href
            elif not title:  # Default link is HTML
                html_url = href

        return self.paper_class(
            arxiv_id=arxiv_id,
            title=self._extract_tag(entry, "atom:title", namespace) or "",
            authors=authors,
            abstract=self._extract_tag(entry, "atom:summary", namespace) or "",
            published=self._parse_date(self._extract_tag(entry, "atom:published", namespace)),
            updated=self._parse_date(self._extract_tag(entry, "atom:updated", namespace)),
            categories=categories,
            pdf_url=pdf_url,
            html_url=html_url,
            comment=self._extract_tag(entry, "atom:comment", namespace),
        )

    def _extract_tag(self, element: ET.Element, tag: str, namespace: dict) -> str | None:
        """Extract text from XML tag.

        Args:
            element: XML element
            tag: Tag name (with namespace prefix)
            namespace: Namespace mapping

        Returns:
            Tag text or None
        """
        found = element.find(tag, namespace)
        if found is not None and found.text:
            return found.text.strip()
        return None

    def _parse_date(self, date_str: str | None) -> str:
        """Parse date string to ISO format.

        Args:
            date_str: Date string from arXiv

        Returns:
            ISO format date string or empty string
        """
        if not date_str:
            return ""

        try:
            # arXiv uses ISO format already
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.isoformat()
        except Exception:
            return date_str  # Return as-is if parsing fails
