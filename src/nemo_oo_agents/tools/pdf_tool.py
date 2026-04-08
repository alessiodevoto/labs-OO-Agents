# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""PDF reading tool for agents."""

from pathlib import Path


class PDFTool:
    """Tool for reading and extracting text from PDF files."""

    def __init__(self, workspace: str = "/tmp/agent_workspace"):
        """Initialize with a workspace directory."""
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def read_pdf(self, path: str, max_pages: int | None = None) -> str:
        """
        Read and extract text from a PDF file.

        Args:
            path: File path (relative to workspace or absolute)
            max_pages: Maximum number of pages to read (None for all)

        Returns:
            Extracted text from the PDF
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            return "Error: pypdf library not installed. Run: pip install pypdf"

        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        if not str(file_path).lower().endswith(".pdf"):
            raise ValueError(f"File does not appear to be a PDF: {path}")

        reader = PdfReader(file_path)
        total_pages = len(reader.pages)

        pages_to_read = total_pages
        if max_pages is not None:
            pages_to_read = min(max_pages, total_pages)

        text_parts = []
        for i in range(pages_to_read):
            page = reader.pages[i]
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

        if not text_parts:
            return f"No text could be extracted from the PDF ({total_pages} pages)"

        result = "\n\n".join(text_parts)

        if max_pages is not None and max_pages < total_pages:
            result += f"\n\n[... {total_pages - max_pages} more pages not shown ...]"

        return result

    def get_pdf_info(self, path: str) -> dict:
        """
        Get metadata and information about a PDF file.

        Args:
            path: File path (relative to workspace or absolute)

        Returns:
            Dictionary with PDF metadata
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            return {"error": "pypdf library not installed"}

        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        reader = PdfReader(file_path)
        metadata = reader.metadata or {}

        return {
            "num_pages": len(reader.pages),
            "title": metadata.get("/Title", ""),
            "author": metadata.get("/Author", ""),
            "subject": metadata.get("/Subject", ""),
            "creator": metadata.get("/Creator", ""),
            "producer": metadata.get("/Producer", ""),
            "creation_date": str(metadata.get("/CreationDate", "")),
            "modification_date": str(metadata.get("/ModDate", "")),
        }

    def read_pdf_page(self, path: str, page_number: int) -> str:
        """
        Read a specific page from a PDF file.

        Args:
            path: File path (relative to workspace or absolute)
            page_number: Page number (1-indexed)

        Returns:
            Text content of the specified page
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            return "Error: pypdf library not installed. Run: pip install pypdf"

        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        reader = PdfReader(file_path)
        total_pages = len(reader.pages)

        if page_number < 1 or page_number > total_pages:
            raise ValueError(
                f"Page number {page_number} out of range. PDF has {total_pages} pages."
            )

        page = reader.pages[page_number - 1]
        text = page.extract_text()

        if not text:
            return f"No text could be extracted from page {page_number}"

        return f"--- Page {page_number} of {total_pages} ---\n{text}"

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to workspace."""
        p = Path(path)
        if p.is_absolute():
            return p
        return self.workspace / p
