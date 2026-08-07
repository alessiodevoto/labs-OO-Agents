# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Swappable retrieval backends for BrowseComp-Plus.

Return shape mirrors upstream ``searcher/searchers/base.py`` so scores are
directly comparable to the leaderboard::

    search(query, k)  -> list[{"docid": str, "score": float, "text": str}]
    get_document(did) -> {"docid": str, "text": str} | None
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from nooa_bench.browsecomp.dataset import (
    DEFAULT_DATA_DIR,
    BrowseCompRecord,
    Document,
    load_records,
)


class Retriever(Protocol):
    """Common surface for BM25 / dense / oracle retrievers."""

    def search(self, query: str, k: int = 10) -> list[dict[str, Any]]: ...

    def get_document(self, docid: str) -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------
# Oracle retriever -- returns the record's gold documents. Smoke-test only:
# it looks up records by literal query string, so calling ``search("foo")``
# with anything other than the exact question text returns [].
# ---------------------------------------------------------------------------


class OracleRetriever:
    """Return the record's ``gold_docs`` for the current query.

    Uses the inline documents shipped with each record, so no corpus load is
    needed. Set ``use_evidence=True`` to return the (typically larger)
    ``evidence_docs`` bundle instead.
    """

    def __init__(
        self,
        records: list[BrowseCompRecord] | None = None,
        *,
        use_evidence: bool = False,
    ) -> None:
        self._records = records if records is not None else load_records()
        self._by_query = {r.query: r for r in self._records}
        self._use_evidence = use_evidence
        # Index every doc we've ever seen so get_document works across records.
        self._by_docid: dict[str, Document] = {}
        for r in self._records:
            for d in (*r.gold_docs, *r.evidence_docs, *r.negative_docs):
                self._by_docid.setdefault(d.docid, d)

    def _docs_for(self, record: BrowseCompRecord) -> list[Document]:
        return record.evidence_docs if self._use_evidence else record.gold_docs

    def search(self, query: str, k: int = 10) -> list[dict[str, Any]]:
        record = self._by_query.get(query)
        if record is None:
            return []
        return [
            {"docid": d.docid, "score": 1.0, "text": d.text}
            for d in self._docs_for(record)[:k]
        ]

    def get_document(self, docid: str) -> dict[str, Any] | None:
        doc = self._by_docid.get(docid)
        if doc is None:
            return None
        return {"docid": doc.docid, "text": doc.text}


# ---------------------------------------------------------------------------
# BM25 retriever -- Pyserini wrapper over the downloaded Lucene index.
# ---------------------------------------------------------------------------


class BM25Retriever:
    """Pyserini ``LuceneSearcher`` over the BrowseComp-Plus BM25 index.

    Requires the optional ``pyserini`` extra and Java 21 (``brew install
    openjdk@21`` or ``conda install -c conda-forge openjdk=21``). Default
    index path is ``$NOOA_BENCH_BROWSECOMP_DIR/indexes/bm25``.
    """

    def __init__(self, index_path: str | Path | None = None) -> None:
        # Lazy import: heavyweight, boots the JVM. We hit the private
        # ``_searcher`` module directly to avoid pyserini's top-level
        # ``__init__`` pulling in transformers/encoder code -- that path
        # has a tight tokenizers version pin that conflicts with nemo's.
        from pyserini.search.lucene._searcher import LuceneSearcher  # noqa: PLC0415

        index_path = Path(index_path) if index_path else DEFAULT_DATA_DIR / "indexes" / "bm25"
        if not index_path.exists():
            raise FileNotFoundError(
                f"BM25 index not found at {index_path}. Download it with: "
                "`hf download Tevatron/browsecomp-plus-indexes --repo-type=dataset "
                "--include='bm25/*' --local-dir $NOOA_BENCH_BROWSECOMP_DIR/indexes`"
            )
        self._index_path = str(index_path)
        self._searcher = LuceneSearcher(self._index_path)

    @staticmethod
    def _decode(hit: Any) -> dict[str, Any]:
        import json  # noqa: PLC0415

        raw = json.loads(hit.lucene_document.get("raw"))
        return {"docid": hit.docid, "score": float(hit.score), "text": raw["contents"]}

    def search(self, query: str, k: int = 10) -> list[dict[str, Any]]:
        return [self._decode(h) for h in self._searcher.search(query, k)]

    def get_document(self, docid: str) -> dict[str, Any] | None:
        import json  # noqa: PLC0415

        doc = self._searcher.doc(docid)
        if doc is None:
            return None
        return {"docid": docid, "text": json.loads(doc.raw())["contents"]}
