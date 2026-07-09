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
- The Harbor runner passes the whole instruction.md (conversation history +
  question) as ``user_message``. We split it into the conversation context and
  the question, then make a single ``PredictStrategy`` LLM call over the full
  conversation context.

Note: the original port carried a retrieval-augmented context builder (recent +
keyword-relevant + temporal sessions). Harbor only ever supplies the flattened
instruction text, never structured ``sessions``, so that machinery could never
influence the answer and was removed (see #343). Restore a sessions-bearing
input path here if a structured LoCoMo dataset is wired up.
"""

from __future__ import annotations

from nemo_oo_agents import Agent, PredictStrategy, strategy
from nemo_oo_agents.config.truncation_config import TruncationConfig
from nemo_oo_agents.unifiedllm import FakeLLMClient


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
        question, then answer over the conversation context.
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

        try:
            answer = await self._generate_answer(question=question, context=conversation_text)
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
