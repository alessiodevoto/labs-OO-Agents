# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LoCoMo (long-context memory) agent for nemo-oo-agents-benchmarks.

Ported from agent006 history:
  git show 9a4f888~1:experiments/evaluation-ablations/agents/long_memory_agent.py
  (class LongMemoryAgent)

LoCoMo benchmark: long-term conversational memory QA.
- 10 conversations, ~600 turns, ~16K tokens each
- 5 question types: single-hop, temporal, open-domain, multi-hop, adversarial
- Scored by F1 against reference answers

Key design:
- Retrieval-augmented context window management (recent + relevant sessions)
- Temporal index for date-based queries
- Keyword-overlap retrieval for multi-hop questions
- Single PredictStrategy LLM call: context assembled deterministically, then
  one LLM call produces the final answer string.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from nemo_oo_agents import Agent, PredictStrategy, strategy
from nemo_oo_agents.config.truncation_config import TruncationConfig
from unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM


class LoCoMoAgent(Agent, llm=FakeLLMClient()):
    """Agent with explicit long-term memory management for LoCoMo conversational QA.

    You are a personal AI assistant with access to a long history of past
    conversations. Your task is to answer questions by accurately recalling
    information from these conversations.

    Rules:
    1. Answer ONLY from information in the conversation history
    2. Be precise and direct in your answers
    3. For temporal questions, reference specific dates when available
    4. If you cannot find the answer, say "I cannot find that in our conversations"

    ## Question Types

    - **single-hop**: Answer from a single dialogue turn
    - **multi-hop**: Combine information across multiple sessions
    - **temporal**: Questions about dates, order, or time intervals
    - **open-domain**: General recall from any session
    - **adversarial**: Questions that may not be answerable -- state clearly when
      the answer is not in the history
    """

    def __init__(
        self,
        llm: UnifiedLLM | None = None,
        recent_sessions_count: int = 3,
        max_retrieved_sessions: int = 5,
        **kwargs: Any,
    ) -> None:
        super().__init__(llm=llm, **kwargs)

        # Retrieval settings
        self.recent_sessions_count = recent_sessions_count
        self.max_retrieved_sessions = max_retrieved_sessions

        # Per-task state (populated before each generation call)
        self.sessions: list[dict] = []
        self.session_index: dict[str, Any] = {}
        self.current_question: str = ""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner.

        The Harbor runner passes the full instruction.md as ``user_message``.
        The instruction format is:
            [preamble + conversation history]
            ---
            ## Question
            **Type:** <type>
            <question text>
            Write your answer to `/app/answer.txt`. ...

        We split on ``---`` to separate the conversation context from the
        question, then pass both to ``_generate_answer``.
        """
        full_text = (
            task_input.get("user_message")
            or task_input.get("user_prompt")
            or task_input.get("question")
            or task_input.get("description", "")
        )

        if not full_text:
            return {"response": "", "success": False, "error": "No instruction provided"}

        # Split instruction into conversation context and question
        conversation_text, question = self._parse_instruction(full_text)

        sessions = (
            task_input.get("sessions")
            or task_input.get("haystack_sessions")
            or task_input.get("conversation_history")
            or []
        )

        try:
            answer = await self.answer_from_memory(
                question=question,
                sessions=sessions,
                context_override=conversation_text,
            )
            return {"response": str(answer), "success": True, "answer": answer}
        except Exception as e:
            return {"response": "", "success": False, "error": str(e)}

    @staticmethod
    def _parse_instruction(text: str) -> tuple[str, str]:
        """Split instruction.md into (conversation_context, question).

        If there is a ``---`` separator, everything before is the conversation
        and the text after ``## Question`` is the actual question.
        Otherwise the full text is used as the question.
        """
        if "\n---\n" in text:
            parts = text.split("\n---\n", 1)
            conversation = parts[0].strip()
            question_block = parts[1].strip()
            # Extract the question line(s): skip "## Question", "**Type:** ...",
            # and the trailing "Write your answer..." instruction.
            lines = question_block.splitlines()
            question_lines = []
            in_question = False
            for line in lines:
                if line.startswith("## Question"):
                    in_question = True
                    continue
                if in_question:
                    if line.startswith("**Type:**"):
                        continue
                    if line.startswith("Write your answer") or line.startswith("If the question"):
                        break
                    question_lines.append(line)
            question = "\n".join(question_lines).strip()
            return conversation, question or question_block
        return "", text

    # ------------------------------------------------------------------
    # Orchestration (real Python -- no ellipsis)
    # ------------------------------------------------------------------

    async def answer_from_memory(
        self,
        question: str,
        sessions: list | None = None,
        context_override: str | None = None,
    ) -> str:
        """Assemble retrieval context and call the LLM to answer a memory question.

        If ``context_override`` is provided (e.g. the raw conversation text from
        the instruction.md), it is used as-is instead of building from sessions.
        Otherwise sessions are used to build a retrieval-augmented context.
        """
        if sessions is None:
            sessions = []

        # Store per-task state used by retrieval helpers
        self.sessions = sessions
        self.current_question = question
        self.session_index = self._build_session_index(sessions)

        if context_override:
            # Use the raw conversation text from the instruction directly
            context_text = context_override
        else:
            # Assemble context: recent sessions always included
            parts: list[str] = []

            recent = self._format_sessions(
                self.sessions[-self.recent_sessions_count :], "Recent Conversations"
            )
            if recent:
                parts.append(recent)

            # Add keyword-retrieved sessions (excluding recent ones)
            if len(self.sessions) > self.recent_sessions_count:
                relevant = self.get_relevant_sessions()
                if relevant:
                    parts.append(relevant)

            # Add temporal timeline for date-sensitive questions
            if self.is_temporal_query():
                temporal = self.get_temporal_context()
                if temporal:
                    parts.append(temporal)

            context_text = "\n\n".join(parts)

        return await self._generate_answer(question=question, context=context_text)

    # ------------------------------------------------------------------
    # Generation method (single LLM call via PredictStrategy)
    # ------------------------------------------------------------------

    @strategy(PredictStrategy(), truncation=TruncationConfig(max_block_chars=120_000))
    async def _generate_answer(self, question: str, context: str = "") -> str:
        """Answer the following question using the provided conversation history.

        Question: {question}

        Conversation history and context:
        {context}

        Answer ONLY from information in the conversation history above.
        Give a short, direct answer — a word, phrase, or single sentence.
        Do NOT start with "Based on", "According to", or any other preamble.
        For temporal questions, include the specific date or time period.
        If the answer is not present in the history, say exactly:
        "I cannot find that in our conversations"
        """
        ...

    # ------------------------------------------------------------------
    # Retrieval helpers (deterministic)
    # ------------------------------------------------------------------

    def get_relevant_sessions(self) -> str:
        """Return formatted sessions relevant to the current question.

        Excludes sessions already covered by the recent-sessions window.
        """
        relevant_indices = self._retrieve_relevant_session_indices()

        recent_start = max(0, len(self.sessions) - self.recent_sessions_count)
        non_recent = [i for i in relevant_indices if i < recent_start]

        if not non_recent:
            return ""

        sessions = [self.sessions[i] for i in non_recent[: self.max_retrieved_sessions]]
        return self._format_sessions(sessions, "Related Past Conversations")

    def get_temporal_context(self) -> str:
        """Return a brief timeline summary for date-based queries."""
        temporal = self.session_index.get("temporal", {})
        if not temporal:
            return ""

        lines = [f"- {date}: sessions {indices}" for date, indices in sorted(temporal.items())]
        return "## Timeline\n" + "\n".join(lines[-10:])

    def is_temporal_query(self) -> bool:
        """Return True when the current question involves temporal reasoning."""
        patterns = [
            r"\bwhen\b",
            r"\bdate\b",
            r"\btime\b",
            r"\bbefore\b",
            r"\bafter\b",
            r"\blast\b",
            r"\bfirst\b",
            r"\brecently\b",
            r"\byesterday\b",
            r"\blast week\b",
            r"\blast month\b",
            r"\d{4}",
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
        ]
        q = self.current_question.lower()
        return any(re.search(p, q) for p in patterns)

    # ------------------------------------------------------------------
    # Index / formatting helpers (deterministic)
    # ------------------------------------------------------------------

    def _build_session_index(self, sessions: list[dict]) -> dict[str, Any]:
        """Build temporal and keyword indices over all sessions."""
        index: dict[str, Any] = {"temporal": {}, "keywords": {}}

        for i, session in enumerate(sessions):
            date: str = str(
                (session.get("date") or session.get("timestamp") or f"session_{i}")
                if isinstance(session, dict)
                else f"session_{i}"
            )
            date_key = date[:10]
            index["temporal"].setdefault(date_key, []).append(i)

            text = self._session_text(session).lower()
            for word in set(re.findall(r"\b\w{4,}\b", text)):
                index["keywords"].setdefault(word, []).append(i)

        return index

    def _retrieve_relevant_session_indices(self) -> list[int]:
        """Return session indices ranked by keyword overlap with the current question."""
        q_words = set(re.findall(r"\b\w{4,}\b", self.current_question.lower()))
        scores: dict[int, float] = {}
        for word in q_words:
            for idx in self.session_index.get("keywords", {}).get(word, []):
                scores[idx] = scores.get(idx, 0) + 1
        return [idx for idx, _ in sorted(scores.items(), key=lambda x: -x[1]) if scores[idx] > 0]

    def _session_text(self, session: dict | list) -> str:
        """Extract plain text from a session (handles both dict and list formats)."""
        turns: list = (
            session
            if isinstance(session, list)
            else (session.get("turns") or session.get("dialogue") or session.get("messages") or [])
        )
        return " ".join(
            (t.get("content") or t.get("text") or "") if isinstance(t, dict) else str(t)
            for t in turns
        )

    def _format_sessions(self, sessions: Sequence[dict | list], title: str) -> str:
        """Format a list of sessions into a readable context block."""
        if not sessions:
            return ""

        parts = [f"## {title}\n"]
        for i, session in enumerate(sessions):
            if isinstance(session, dict):
                date = session.get("date") or session.get("timestamp") or f"Session {i + 1}"
                turns: list = (
                    session.get("turns") or session.get("dialogue") or session.get("messages") or []
                )
            else:
                date = f"Session {i + 1}"
                turns = list(session)

            parts.append(f"\n### {date}\n")
            for turn in turns:
                if isinstance(turn, dict):
                    role = turn.get("role") or turn.get("speaker") or "Unknown"
                    content = turn.get("content") or turn.get("text") or ""
                    prefix = "User" if role.lower() in ("user", "human") else "Assistant"
                    parts.append(f"{prefix}: {content}\n")
        return "".join(parts)
