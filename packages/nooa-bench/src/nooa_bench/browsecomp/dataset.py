# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""BrowseComp-Plus dataset loading.

Loads the decrypted JSONL produced by upstream's
``scripts_build_index/decrypt_dataset.py``. Each record ships with the
supporting evidence documents inline (``gold_docs`` / ``evidence_docs``) plus
hard negatives (``negative_docs``), so the oracle path needs no corpus lookup.

Record shape (from ``Tevatron/browsecomp-plus``):

    {
      "query_id":     "769",
      "query":        "...",
      "answer":       "Queen Arwa University",
      "gold_docs":    [{"docid": "5412", "text": "...", "url": "..."}, ...],
      "evidence_docs":[{"docid": "5412", "text": "...", "url": "..."}, ...],
      "negative_docs":[{"docid": "26215", "text": "...", "url": "..."}, ...],
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


DEFAULT_DATA_DIR = Path(
    os.environ.get("NOOA_BENCH_BROWSECOMP_DIR", "~/.cache/nooa-bench/browsecomp")
).expanduser()


@dataclass
class Document:
    """A single document with its BrowseComp-Plus corpus ID."""

    docid: str
    text: str
    url: str = ""


@dataclass
class BrowseCompRecord:
    """One BrowseComp-Plus query with its ground truth and evidence bundle."""

    query_id: str
    query: str
    answer: str
    gold_docs: list[Document] = field(default_factory=list)
    evidence_docs: list[Document] = field(default_factory=list)
    negative_docs: list[Document] = field(default_factory=list)

    @property
    def gold_doc_ids(self) -> list[str]:
        return [d.docid for d in self.gold_docs]

    @property
    def evidence_doc_ids(self) -> list[str]:
        return [d.docid for d in self.evidence_docs]


def _to_docs(raw: object) -> list[Document]:
    if not raw:
        return []
    out: list[Document] = []
    for entry in raw:  # type: ignore[union-attr]
        if not isinstance(entry, dict):
            continue
        out.append(
            Document(
                docid=str(entry.get("docid", "")),
                text=str(entry.get("text", "")),
                url=str(entry.get("url", "")),
            )
        )
    return out


def load_records(
    path: Path | str | None = None,
    *,
    limit: int | None = None,
    query_ids: list[str] | None = None,
) -> list[BrowseCompRecord]:
    """Load records from the decrypted JSONL file.

    Parameters
    ----------
    path:
        Defaults to ``$NOOA_BENCH_BROWSECOMP_DIR/browsecomp_plus_decrypted.jsonl``.
    limit:
        Cap on the number of records returned (in file order).
    query_ids:
        Only return records with these IDs.
    """

    path = Path(path) if path else DEFAULT_DATA_DIR / "browsecomp_plus_decrypted.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing decrypted BrowseComp-Plus file at {path}. Run upstream's "
            "`scripts_build_index/decrypt_dataset.py` to generate it."
        )

    wanted = set(query_ids) if query_ids else None
    out: list[BrowseCompRecord] = []
    with path.open() as fh:
        for line in fh:
            obj = json.loads(line)
            qid = str(obj["query_id"])
            if wanted is not None and qid not in wanted:
                continue
            out.append(
                BrowseCompRecord(
                    query_id=qid,
                    query=str(obj["query"]),
                    answer=str(obj.get("answer", "")),
                    gold_docs=_to_docs(obj.get("gold_docs")),
                    evidence_docs=_to_docs(obj.get("evidence_docs")),
                    negative_docs=_to_docs(obj.get("negative_docs")),
                )
            )
            if limit is not None and len(out) >= limit:
                break
    return out


@lru_cache(maxsize=1)
def load_corpus(corpus_dir: Path | str | None = None) -> dict[str, str]:
    """Return ``{doc_id: text}`` from the local HF corpus snapshot.

    Only needed when a retriever hands back doc IDs that aren't already in a
    record's ``gold_docs``/``negative_docs`` bundle (e.g. dense retrievers
    that don't store raw text). BM25's Lucene index stores raw contents, so
    :class:`BM25Retriever` also doesn't need this.
    """

    corpus_dir = Path(corpus_dir) if corpus_dir else DEFAULT_DATA_DIR / "corpus"
    from datasets import load_from_disk  # local: heavy import

    ds = load_from_disk(str(corpus_dir))
    return {str(row["docid"]): str(row["text"]) for row in ds}
