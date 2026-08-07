# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""BrowseComp-Plus benchmark integration.

Deep-research agent evaluation over a fixed ~100K-doc corpus. The agent is a
standard nooa CodeAct agent with two research methods (``search``,
``get_document``); the retriever backend (Oracle / BM25 / dense) is swappable.

Data layout (default ``NOOA_BENCH_BROWSECOMP_DIR = ~/.cache/nooa-bench/browsecomp``):

  browsecomp_plus_decrypted.jsonl   # query_id, query, answer, gold_docs, evidence_docs, negative_docs
  queries.tsv                       # query_id \\t query
  corpus/                           # HF Tevatron/browsecomp-plus-corpus (arrow, optional)
  indexes/bm25/                     # Lucene BM25 index (Pyserini)
"""

from nooa_bench.browsecomp.agent import BrowseCompAgent, BrowseCompAnswer
from nooa_bench.browsecomp.dataset import BrowseCompRecord, Document, load_records
from nooa_bench.browsecomp.grader import (
    GRADER_TEMPLATE,
    GradeResult,
    HeuristicGrader,
    LLMJudgeGrader,
)
from nooa_bench.browsecomp.retriever import BM25Retriever, OracleRetriever, Retriever
from nooa_bench.browsecomp.runner import RunReport, evaluate, evaluate_async, load_subset

__all__ = [
    "BM25Retriever",
    "BrowseCompAgent",
    "BrowseCompAnswer",
    "BrowseCompRecord",
    "Document",
    "GRADER_TEMPLATE",
    "GradeResult",
    "HeuristicGrader",
    "LLMJudgeGrader",
    "OracleRetriever",
    "Retriever",
    "RunReport",
    "evaluate",
    "evaluate_async",
    "load_records",
    "load_subset",
]
