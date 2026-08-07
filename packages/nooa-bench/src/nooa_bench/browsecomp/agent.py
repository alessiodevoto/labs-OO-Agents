# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""BrowseComp-Plus deep-research agent.

Minimal CodeAct agent modelled on :class:`nooa_bench.bench_agent.BenchAgent`.
Two research tools -- :meth:`search` and :meth:`get_document` -- are exposed
as plain methods; CodeAct makes them callable from generated Python. The
retriever backend (Oracle / BM25 / dense) is a private attribute injected at
construction time, so the same agent works with every backend.
"""

from __future__ import annotations

from nooa import hidden as _hidden

_agentdoc_hidden_names = {"_hidden"}

with _hidden:
    import logging
    from typing import TYPE_CHECKING, Any

    from pydantic import BaseModel, Field

    from nooa import Agent, CodeActStrategy, strategy
    from nooa.config import CodeActConfig
    from nooa.context_blocks import DynamicContext
    from nooa.unifiedllm import FakeLLMClient

    from nooa_bench.browsecomp.retriever import Retriever

if TYPE_CHECKING:
    from nooa.unifiedllm import UnifiedLLM

_logger = logging.getLogger(__name__)


class BrowseCompAnswer(BaseModel):
    """Structured answer the agent must return when finishing a query."""

    exact_answer: str = Field(
        description=(
            "Your succinct final answer to the question -- a name, number, or short phrase. "
            "No prose."
        )
    )
    explanation: str = Field(
        description=(
            "One or two sentences justifying the answer. Cite evidence docids inline in "
            "square brackets, e.g. [5412]."
        )
    )
    evidence_docids: list[str] = Field(
        default_factory=list,
        description="Docids of the documents you actually used to derive the answer.",
    )
    confidence: int = Field(
        default=100, ge=0, le=100, description="Your confidence in the answer, 0-100."
    )


class BrowseCompAgent(
    Agent,
    llm=FakeLLMClient(),
    context={
        "task": DynamicContext(expr="self.query"),
    },
):
    """Deep-research agent for BrowseComp-Plus.

    ## Tools

    ```python
    hits = self.search("query text", k=5)      # -> [{docid, score, snippet}]
    doc  = self.get_document("42")              # -> {docid, text} or None
    ```

    ## Workflow

    1. **Search** -- call ``self.search`` with a query that decomposes the question.
       Read the top snippets to find promising docids.
    2. **Read** -- call ``self.get_document(docid)`` on hits that look on-topic to
       get the full text. The snippet from ``search`` is truncated.
    3. **Refine** -- issue new ``self.search`` calls when your first query didn't
       hit the answer. Break multi-part questions into per-part searches.
    4. **Return** -- once you have the answer, call ``return_result`` with a
       :class:`BrowseCompAnswer` citing the docids you actually used.

    ## Return format

    ```python
    return_result(BrowseCompAnswer(
        exact_answer="Queen Arwa University",
        explanation="The Yemeni institution founded in 1996 that runs the Fifth Cultural Week [5412].",
        evidence_docids=["5412", "82002"],
        confidence=90,
    ))
    ```
    """

    def __init__(
        self,
        retriever: Retriever,
        *,
        llm: "UnifiedLLM | None" = None,
        default_k: int = 5,
        snippet_max_chars: int = 2000,
        **kwargs: Any,
    ) -> None:
        super().__init__(llm=llm, **kwargs)
        self._retriever = retriever
        self._default_k = default_k
        self._snippet_max_chars = snippet_max_chars
        self.query = ""

    def _truncate(self, text: str) -> str:
        if len(text) <= self._snippet_max_chars:
            return text
        return text[: self._snippet_max_chars] + "\n...[truncated]"

    def search(self, query: str, k: int | None = None) -> list[dict[str, Any]]:
        """Search the corpus. Returns top-``k`` hits with ``docid``, ``score``, ``snippet``.

        Snippets are truncated to a fixed length; call :meth:`get_document` for
        the full text of any hit you want to read in full.
        """
        hits = self._retriever.search(query, k or self._default_k)
        return [
            {
                "docid": h["docid"],
                "score": h.get("score", 0.0),
                "snippet": self._truncate(h["text"]),
            }
            for h in hits
        ]

    def get_document(self, docid: str) -> dict[str, Any] | None:
        """Return the full ``{docid, text}`` for a document, or ``None`` if unknown."""
        return self._retriever.get_document(docid)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point matching the nooa-bench runner contract."""
        query = task_input.get("query") or task_input.get("user_message") or ""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("task_input must include a non-empty 'query' or 'user_message'")

        self.query = query.strip()

        try:
            result = await self._solve_query(self.query)
            if isinstance(result, BrowseCompAnswer):
                return {
                    "response": self._format_response(result),
                    "success": True,
                    "result": result.model_dump(),
                }
            result_str = str(result) if result is not None else ""
            return {"response": result_str, "success": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            _logger.error("BrowseCompAgent failed: %s", exc)
            return {"response": "", "success": False, "error": str(exc)}

    @staticmethod
    def _format_response(answer: BrowseCompAnswer) -> str:
        """Render a :class:`BrowseCompAnswer` in the upstream ``Explanation/Exact Answer/Confidence`` shape.

        Kept compatible with :class:`~nooa_bench.browsecomp.grader.HeuristicGrader`
        and :class:`~nooa_bench.browsecomp.grader.LLMJudgeGrader`.
        """
        return (
            f"Explanation: {answer.explanation}\n"
            f"Exact Answer: {answer.exact_answer}\n"
            f"Confidence: {answer.confidence}%"
        )

    @strategy(
        CodeActStrategy(
            config=CodeActConfig(
                max_iterations=40, max_retries=5, text_only_stop_behavior="synthetic_comment"
            )
        )
    )
    async def _solve_query(self, question: str) -> BrowseCompAnswer:
        """Answer the given BrowseComp-Plus question by iterative search.

        ## Question
        {question}

        ## Instructions
        - Use ``self.search(query, k=5)`` to survey the corpus. The returned
          snippets are truncated -- call ``self.get_document(docid)`` on any
          hit whose snippet suggests it might have the answer.
        - Issue multiple searches. BrowseComp-Plus questions are multi-clause;
          break them into per-clause searches and combine the evidence.
        - You must cite the docids you used in ``evidence_docids``.
        - Return promptly when you have the answer -- do not keep searching
          once you can justify a confident answer.

        ## Return format

        ```python
        return_result(BrowseCompAnswer(
            exact_answer="<succinct answer>",
            explanation="<justification with inline [docid] citations>",
            evidence_docids=["<docid>", ...],
            confidence=<0-100>,
        ))
        ```
        """
        ...
