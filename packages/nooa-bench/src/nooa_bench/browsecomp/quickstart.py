# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Command-line smoke test for the BrowseComp-Plus baseline agent.

Mirrors ``nooa_bench.runner._run``: pulls the LLM client via
``get_llm_client``, honours ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``
overrides, builds a :class:`BrowseCompAgent`, and iterates a small subset of
records. Grader defaults to the cheap :class:`HeuristicGrader`.

Usage::

    python -m nooa_bench.browsecomp.quickstart \\
        --model claude-opus-4-7 \\
        --retriever bm25 \\
        --limit 5 \\
        --output /tmp/browsecomp_smoke.json

    # Point at a self-hosted vLLM / OpenAI-compatible endpoint:
    OPENAI_BASE_URL=https://... OPENAI_API_KEY=... \\
        python -m nooa_bench.browsecomp.quickstart --model my-model --limit 5
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from nooa_bench.browsecomp.agent import BrowseCompAgent
from nooa_bench.browsecomp.grader import HeuristicGrader
from nooa_bench.browsecomp.retriever import BM25Retriever, OracleRetriever, Retriever
from nooa_bench.browsecomp.runner import evaluate, load_subset

_logger = logging.getLogger(__name__)


def _build_retriever(name: str, records: list) -> Retriever:
    if name == "oracle":
        return OracleRetriever(records=records)
    if name == "bm25":
        return BM25Retriever()
    raise ValueError(f"Unknown retriever: {name!r} (use oracle|bm25)")


def _build_llm(model: str, api_base: str | None) -> Any:
    """Match ``nooa_bench.runner._run`` env-var handling."""
    from nooa.unifiedllm import get_llm_client

    overrides: dict[str, str] = {}
    if api_base:
        overrides["api_base"] = api_base
    elif base_url := os.environ.get("OPENAI_BASE_URL"):
        overrides["api_base"] = base_url
    if api_key := os.environ.get("OPENAI_API_KEY"):
        overrides["api_key"] = api_key
    return get_llm_client(model, **overrides)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Model name for get_llm_client.")
    parser.add_argument(
        "--retriever",
        default="bm25",
        choices=("oracle", "bm25"),
        help="Retrieval backend (default: bm25).",
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Number of queries to evaluate."
    )
    parser.add_argument(
        "--query-ids",
        default=None,
        help="Comma-separated list of query_ids to evaluate (overrides --limit).",
    )
    parser.add_argument(
        "--api-base", default=None, help="LLM endpoint base URL (overrides OPENAI_BASE_URL)."
    )
    parser.add_argument("--output", default=None, help="Write per-query JSON here.")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--k", type=int, default=5, help="Top-k hits per search call.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    query_ids = [q.strip() for q in args.query_ids.split(",")] if args.query_ids else None
    records = load_subset(limit=args.limit if not query_ids else None, query_ids=query_ids)
    _logger.info("Loaded %d records", len(records))

    retriever = _build_retriever(args.retriever, records)
    _logger.info("Retriever: %s", args.retriever)

    def _factory() -> BrowseCompAgent:
        llm = _build_llm(args.model, args.api_base)
        return BrowseCompAgent(retriever=retriever, llm=llm, default_k=args.k)

    def _progress(i: int, n: int, ok: bool) -> None:
        mark = "✓" if ok else "✗"
        _logger.info("[%d/%d] %s", i, n, mark)

    report = evaluate(
        _factory, records, grader=HeuristicGrader(), on_progress=_progress,
        concurrency=args.concurrency,
    )

    print(
        f"\naccuracy={report.accuracy:.2%}  errors={report.error_count}  n={report.n}",
        file=sys.stderr,
    )
    for row in report.per_query:
        status = "OK " if row["correct"] else "MISS"
        print(
            f"  {status} qid={row['query_id']}  "
            f"gold={row['gold_answer'][:40]!r}  "
            f"got={row['extracted_answer'][:40]!r}",
            file=sys.stderr,
        )

    if args.output:
        report.dump(args.output)
        _logger.info("wrote %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
