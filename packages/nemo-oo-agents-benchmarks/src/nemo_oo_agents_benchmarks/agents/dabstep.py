# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""DABStep agent opt63 — ported from agent006.

Ported from:
  agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py
  (class RSCDABAgentHardOpt63)

Key fix (OPT62): capture_delay range matching (numeric string vs "<3"/"3-5"/>5").
Key fix (OPT61): ACI (single letters A-G) vs card_scheme clarification.
Key insight (OPT59): 14-decimal rule — use round_eur() ONLY for delta questions.
Key insight (OPT31): intracountry = issuing_country vs acquirer_country.

Architecture:
- RulesLawyer: finds relevant business rules from markdown documentation
- SolutionVerifier: validates computed answers
- DABStepAgent (RSCDABAgentHardOpt63): orchestrator; CodeActStrategy, max_iterations=100
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import BaseModel, Field

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.unifiedllm import CompletionClient as _CompletionClient
from nemo_oo_agents.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

# Module-level imports available to LLM-generated code at runtime
import json  # noqa: F401
import math  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sys  # noqa: F401

from nemo_oo_agents_benchmarks.agents.markdown_helpers import (  # noqa: F401
    find_sections_matching_regex,
    find_sections_with_content_matching_regex,
    get_markdown_section,
    get_markdown_section_sizes,
    list_markdown_sections,
)


def _default_llm() -> _CompletionClient:
    """Default LLM: DeepSeek V3.2 on NVIDIA NIM."""
    return _CompletionClient(
        model="nvidia_nim/deepseek-ai/deepseek-v3.2",
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
        api_base="https://integrate.api.nvidia.com/v1",
        temperature=0.0,
        max_tokens=4096,
    )


# ============================================================================
# Helper Functions for Fee Matching (available to LLM-generated code)
# ============================================================================


def applies_to_all(value: Any) -> bool:
    """Check if a fee field value means 'applies to all'.

    CRITICAL: Both null AND empty list mean 'applies to all values'.

    Examples:
        applies_to_all(None)  # True
        applies_to_all([])    # True
        applies_to_all(['A', 'B'])  # False
    """
    return value is None or value == []


def volume_matches(fee_volume: str | None, actual_volume: float) -> bool:
    """Check if actual monthly volume matches fee's volume constraint.

    Args:
        fee_volume: '<100k', '100k-1m', '1m-5m', '>5m', or None
        actual_volume: Calculated monthly transaction volume in EUR
    """
    if fee_volume is None:
        return True
    if fee_volume == "<100k":
        return actual_volume < 100000
    elif fee_volume == "100k-1m":
        return 100000 <= actual_volume < 1000000
    elif fee_volume == "1m-5m":
        return 1000000 <= actual_volume < 5000000
    elif fee_volume == ">5m":
        return actual_volume >= 5000000
    return False


def fraud_level_matches(fee_fraud: str | None, actual_fraud_pct: float) -> bool:
    """Check if actual fraud rate matches fee's fraud level constraint.

    Args:
        fee_fraud: '<7.2%', '7.2%-7.7%', '7.7%-8.3%', '>8.3%', or None
        actual_fraud_pct: Fraud rate as percentage (e.g. 7.83 for 7.83%)
    """
    if fee_fraud is None:
        return True
    if fee_fraud == "<7.2%":
        return actual_fraud_pct < 7.2
    elif fee_fraud == "7.2%-7.7%":
        return 7.2 <= actual_fraud_pct < 7.7
    elif fee_fraud == "7.7%-8.3%":
        return 7.7 <= actual_fraud_pct < 8.3
    elif fee_fraud == ">8.3%":
        return actual_fraud_pct >= 8.3
    return False


def capture_delay_matches(fee_delay: str | None, merchant_delay: str) -> bool:
    """Check if merchant's capture_delay matches fee rule's constraint.

    OPT62 CRITICAL FIX: merchant has a numeric string (e.g. "1", "3", "7");
    fee rules have ranges or special values — must do NUMERIC comparison.

    Args:
        fee_delay: '<3', '3-5', '>5', 'immediate', 'manual', or None
        merchant_delay: merchant's capture_delay as string (e.g. "1", "3")

    Examples:
        capture_delay_matches(None, "1")        # True — null matches all
        capture_delay_matches("<3", "1")        # True — 1 < 3
        capture_delay_matches("<3", "3")        # False — 3 is not < 3
        capture_delay_matches("3-5", "4")       # True
        capture_delay_matches(">5", "7")        # True
        capture_delay_matches("immediate", "0") # True — immediate = 0
        capture_delay_matches("manual", "1")    # False
    """
    if fee_delay is None:
        return True
    try:
        d = int(merchant_delay)
        if fee_delay == "immediate":
            return d == 0
        elif fee_delay == "manual":
            return False
        elif fee_delay == "<3":
            return d < 3
        elif fee_delay == "3-5":
            return 3 <= d <= 5
        elif fee_delay == ">5":
            return d > 5
        else:
            return fee_delay == merchant_delay
    except ValueError:
        return fee_delay == merchant_delay


def calc_fee(fee: dict, transaction_amount: float) -> float:
    """Calculate fee amount.  Formula: fixed_amount + rate * amount / 10000."""
    return fee["fixed_amount"] + fee["rate"] * transaction_amount / 10000


def find_lowest_fee(matching_fees: list[dict], transaction_amount: float) -> dict | None:
    """Return the fee with lowest calculated amount from a list of matching fees."""
    if not matching_fees:
        return None
    return min(matching_fees, key=lambda f: calc_fee(f, transaction_amount))


def fee_matches(
    fee: dict,
    card_scheme: str,
    account_type: str,
    capture_delay: str,
    is_credit: bool,
    aci: str,
    mcc: int,
    intracountry: bool,
    monthly_volume: float,
    fraud_rate: float,
) -> bool:
    """Check if a fee rule matches a transaction/merchant combination.

    OPT62: complete fee matching including capture_delay range logic.
    """
    if fee.get("card_scheme") != card_scheme:
        return False
    if not applies_to_all(fee.get("account_type")):
        if account_type not in fee.get("account_type", []):
            return False
    if not capture_delay_matches(fee.get("capture_delay"), capture_delay):
        return False
    if fee.get("is_credit") is not None:
        if fee.get("is_credit") != is_credit:
            return False
    if not applies_to_all(fee.get("aci")):
        if aci not in fee.get("aci", []):
            return False
    if not applies_to_all(fee.get("merchant_category_code")):
        if mcc not in fee.get("merchant_category_code", []):
            return False
    if fee.get("intracountry") is not None:
        fee_intra = fee.get("intracountry")
        if fee_intra == 1.0 and not intracountry:
            return False
        if fee_intra == 0.0 and intracountry:
            return False
    if not volume_matches(fee.get("monthly_volume"), monthly_volume):
        return False
    if not fraud_level_matches(fee.get("monthly_fraud_level"), fraud_rate):
        return False
    return True


def round_eur(value: float) -> float:
    """Round EUR amount to cents.  OPT59: use ONLY for 14-decimal (delta) questions."""
    return round(value, 2)


def format_numeric_answer(value: float, guidelines: str) -> str:
    """Format numeric answer to the number of decimals specified in guidelines."""
    match = re.search(r"rounded to (\d+) decimals?", guidelines.lower())
    if match:
        decimals = int(match.group(1))
        rounded = round(value, decimals)
        if decimals == 0:
            return str(int(rounded))
        return f"{rounded:.{decimals}f}"
    if value == int(value):
        return str(int(value))
    return str(round(value, 6))


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class DABStepInput:
    """Typed input for DABStep evaluation tasks."""

    question: str
    data_dir: str
    guidelines: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> DABStepInput:
        if "user_message" in d:
            # Unified runner interface: parse question/guidelines from instruction.md text.
            msg = d["user_message"]
            q_match = re.search(
                r"Here is the question you need to answer:\s*(.+?)(?:\n\nHere are the guidelines|$)",
                msg,
                re.DOTALL,
            )
            g_match = re.search(
                r"Here are the guidelines you MUST follow.*?:\s*(.+?)(?:\n\n|$)",
                msg,
                re.DOTALL,
            )
            return cls(
                question=q_match.group(1).strip() if q_match else msg,
                data_dir=d.get("data_dir", "/app/data"),
                guidelines=g_match.group(1).strip() if g_match else "",
            )
        return cls(
            question=d.get("question", ""),
            data_dir=d.get("data_dir", ""),
            guidelines=d.get("guidelines", ""),
        )


class RelevantRule(BaseModel):
    """A business rule extracted from documentation."""

    rule: str = Field(description="The rule text or definition")
    file: str = Field(description="Source file name (e.g. 'manual.md')")
    section: str = Field(description="Section header where the rule was found")
    reasoning: str = Field(description="Why this rule is relevant to the question")


class AnswerResult(BaseModel):
    """Result from compute_answer: the final answer and its explanation."""

    answer: str = Field(description="Final answer matching the guidelines format exactly")
    explanation: str = Field(description="Key calculation steps and rules applied")


class VerifyResult(BaseModel):
    """Result from SolutionVerifier.verify: acceptance decision and reasoning."""

    accepted: bool = Field(description="True if the answer is correct, False otherwise")
    reasoning: str = Field(
        description="If accepted: confirmation. If rejected: specific guidance on what to fix."
    )


# ============================================================================
# Shared system prompt
# ============================================================================


def _base_system_prompt(class_name: str) -> str:
    return f"""You are an expert data scientist and Python programmer who analyzes data to answer business questions.

You are an AI Agent that exists as a Python object of type `{class_name}`.

Your job is to build a comprehensive analysis by iteratively:
1. Loading and exploring the data
2. Reading relevant documentation for business rules
3. Computing the answer step by step
4. Verifying your results before finalizing

KEY PRINCIPLES:
- Build your analysis incrementally, checking outputs at each step
- After each code execution, review the output before proceeding
- When errors occur, fix them before moving on

ANSWER FORMATTING - IMPORTANT:
- When a question asks "What percentage..." or "What is the X rate...", return the percentage VALUE (e.g., 73.15), NOT the decimal proportion (e.g., 0.7315).
- When a question asks for a "ratio", return the decimal value unless otherwise specified.
- Always re-read the question and guidelines before providing your final answer.

## Critical Domain Knowledge

### "Not Applicable" vs Other Answers
- **"Not Applicable"** = The concept/rule asked about is NOT DEFINED in documentation
- **"no"** = The rule EXISTS and the answer is negative
- **"0"** = The rule EXISTS and the numeric result is zero

### Fraud Definition
Fraud is the RATIO of fraudulent volume over total volume (a RATE, not a count).
- Fraud rate = fraudulent_count / total_count * 100 (as percentage)

### Fee Matching Rules - CRITICAL

**Helper Functions Available:**
```python
applies_to_all(value)      # True if value is None or []
volume_matches(fee_vol, actual_vol)
fraud_level_matches(fee_fraud, actual_fraud_pct)
capture_delay_matches(fee_delay, merchant_delay)  # OPT62: range matching
calc_fee(fee, amount)
find_lowest_fee(fees, amount)
```

**Rule 1: Null AND Empty List = Applies to All**

**Rule 2: Lowest Fee Wins** — when multiple fees match, apply the one with lowest calculated amount.

**Rule 3: Monthly Metrics MUST Be Calculated** — fees with monthly_volume or monthly_fraud_level require calculating actual values first.

**Rule 4: Fee Formula** — `fee = fixed_amount + rate * transaction_amount / 10000`

### MCC Lookup
Questions may reference MCC by description. Look up the code in merchant_category_codes.csv.

## Context Blocks
- `<data_summary>`: All loaded DataFrames, JSON files, and text files
- `<relevant_rules>`: Dict mapping rule names to rule dicts
- `<self>`: Lists helper functions and available data"""


# ============================================================================
# Subagent 1: RulesLawyer
# ============================================================================


class RulesLawyer(Agent, llm=FakeLLMClient()):
    """Expert at finding relevant business rules from documentation."""

    def _system_prompt(self) -> str:
        return _base_system_prompt(self.__class__.__name__)

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=3)))
    async def find_rules(
        self,
        question: str,
        text_files: dict[str, str],
        dataframes: dict[str, pd.DataFrame],
        json_files: dict[str, Any],
    ) -> dict[str, RelevantRule]:
        """Find business rules relevant to the question.

        ## Question
        {question}

        ## Available Data
        - `text_files`: Documentation files (e.g. "manual.md")
        - `dataframes`: CSV data as DataFrames
        - `json_files`: JSON data (e.g. fees.json, merchant_data.json)

        ## Helper Functions for Document Search

        ```python
        sections = list_markdown_sections(text_files["manual.md"])
        fee_sections = find_sections_matching_regex(text_files["manual.md"], r"fee|charge")
        fraud_sections = find_sections_with_content_matching_regex(text_files["manual.md"], r"fraud|risk")
        content = get_markdown_section(text_files["manual.md"], "Fee Calculation")
        ```

        ## Instructions

        1. Identify key terms from the question
        2. Search documentation using regex helpers
        3. Extract exact rules (fee formulas, null/empty semantics, thresholds, matching criteria)

        ## IMPORTANT: Always Include These Rules If Relevant

        If the question involves fees, ALWAYS extract:
        - NULL_FIELD_INTERPRETATION: "null/[] means applies to all"
        - FEE_FORMULA: "fixed_amount + rate * amount / 10000"
        - LOWEST_FEE_WINS: "When multiple fees match, lowest calculated wins"

        ## Return Format

        Return dict[str, dict] where key is UPPER_SNAKE_CASE rule name.
        Each value must be a dict with keys: "rule", "file", "section", "reasoning".

        **Do NOT import RelevantRule** — return plain dicts, Pydantic validates automatically.

        Example:
        ```python
        return_result({{"FEE_FORMULA": {{"rule": "fee = fixed_amount + rate * amount / 10000", "file": "manual.md", "section": "Fee Calculation"}}}})
        ```

        Return empty dict `{{}}` if concept is not defined in documentation.
        """
        ...


# ============================================================================
# Subagent 2: SolutionVerifier
# ============================================================================


class SolutionVerifier(Agent, llm=FakeLLMClient()):
    """Validates computed answers with enhanced fee matching checks."""

    def _system_prompt(self) -> str:
        return _base_system_prompt(self.__class__.__name__)

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=2)))
    async def verify(
        self,
        question: str,
        guidelines: str,
        answer: Any,
        explanation: str,
        relevant_rules: dict[str, RelevantRule],
        text_files: dict[str, str],
        dataframes: dict[str, pd.DataFrame],
        json_files: dict[str, Any],
    ) -> VerifyResult:
        """Verify if the computed answer is correct.

        ## Question
        {question}

        ## Answer Format Guidelines
        {guidelines}

        ## Computed Answer
        {answer}

        ## Explanation
        {explanation}

        ## Named Rules
        {relevant_rules}

        ## Verification Checklist

        ### 1. FORMAT CHECK
        - Does answer match required format exactly?
        - Common errors: percentage as decimal (0.73 vs 73.0), wrong separator, missing units

        ### 1b. ROUNDING CHECK
        - **14-DECIMAL RULE (OPT59)**: If 14 decimals requested, round to cents FIRST:
          delta -0.941192 → round_eur → -0.94 → format → "-0.94000000000000"
        - If 2/3/6 decimals: do NOT use round_eur, format directly

        ### 2. NULL SEMANTICS CHECK
        - Was `None` (null) treated as "applies to all"?
        - Was empty list `[]` treated as "applies to all"?

        ### 3. MONTHLY METRICS CHECK
        - Were actual monthly volume/fraud calculated before fee filtering?

        ### 4. LOWEST FEE CHECK
        - Was the lowest calculated fee selected (not lowest by ID)?

        ### 5. QUESTION INTENT CHECK
        - Does the answer address what was ASKED?

        ## Return
        Return VerifyResult(accepted=bool, reasoning=str).
        If rejecting, provide SPECIFIC guidance on what to fix in `reasoning`.
        """
        ...


# ============================================================================
# Main Agent
# ============================================================================


class DABStepAgent(
    Agent,
    llm=_default_llm(),
):
    """DABStep agent opt63 — payment-fee data analysis benchmark.

    Orchestrates RulesLawyer → compute_answer → SolutionVerifier in a retry loop.
    """

    # Sub-agents (overridable for testing)
    RulesLawyer = RulesLawyer
    SolutionVerifier = SolutionVerifier

    MAX_RETRIES: int = 3

    def _system_prompt(self) -> str:
        return (
            _base_system_prompt(self.__class__.__name__)
            + """

## DABStep Data
- **`data_summary` block**: Summaries of all DataFrames, JSON files, text files
- **`relevant_rules` block**: Business rules found by RulesLawyer

## Available Helper Functions (module-level, call directly)
```python
fee_matches(fee, card_scheme, account_type, capture_delay, is_credit, aci, mcc, intracountry, vol, fraud_pct)
applies_to_all(value)
volume_matches(fee_vol, actual_vol)
fraud_level_matches(fee_fraud, actual_fraud_pct)
capture_delay_matches(fee_delay, merchant_delay)  # OPT62: range matching
calc_fee(fee, amount)
find_lowest_fee(matching_fees, amount)
round_eur(value)           # OPT59: ONLY for 14-decimal questions
format_numeric_answer(value, guidelines)
```

## CRITICAL: Use fee_matches() — do NOT write your own fee matching!

## CRITICAL: ACI vs Card Scheme (OPT61)
- ACI values: single letters A, B, C, D, E, F, G (payments.csv `aci` column)
- card_scheme values: NexPay, GlobalCard, TransactPlus, etc. (fees.json)
- For "ACI comparison" tasks: iterate ALL 7 ACIs; pick the one with LOWEST total fee.
"""
        )

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        self.data_dir: str = ""
        self.text_files: dict[str, str] = {}
        self.json_files: dict[str, Any] = {}
        self.dataframes: dict[str, pd.DataFrame] = {}
        self.relevant_rules: dict[str, RelevantRule] = {}

    def _load_data(self, data_dir: str) -> None:
        """Load all data files from directory."""
        self.data_dir = data_dir
        self.text_files = {}
        self.json_files = {}
        self.dataframes = {}

        if not os.path.isdir(data_dir):
            return

        for filename in os.listdir(data_dir):
            filepath = os.path.join(data_dir, filename)
            if os.path.isdir(filepath):
                continue
            _, ext = os.path.splitext(filename)
            if ext in {".md", ".txt", ".rst"}:
                with open(filepath) as f:
                    self.text_files[filename] = f.read()
            elif ext == ".json":
                with open(filepath) as f:
                    self.json_files[filename] = json.load(f)
            elif ext == ".csv":
                self.dataframes[filename] = pd.read_csv(filepath)

    def _get_dataframe_summary(self, df: pd.DataFrame, name: str) -> str:
        access_path = f'self.dataframes["{name}"]'
        if df.empty:
            return f"### {access_path}\nEmpty DataFrame\n"
        lines = [
            f"### {access_path}",
            f"Shape: {df.shape[0]:,} rows × {df.shape[1]} columns",
            f"Columns: {', '.join(df.columns.tolist())}",
            "\nColumn types:",
        ]
        for col, dtype in df.dtypes.items():
            lines.append(f"  - {col}: {dtype}")
        return "\n".join(lines)

    def _get_data_context(self) -> str:
        sections = ["## Loaded Data Summary\n"]
        for name, df in sorted(self.dataframes.items()):
            sections.append(self._get_dataframe_summary(df, name))
        for name, data in sorted(self.json_files.items()):
            access_path = f'self.json_files["{name}"]'
            if isinstance(data, list):
                sections.append(f"### {access_path}\n{len(data)} items loaded")
                if data and isinstance(data[0], dict):
                    sections.append(f"Sample keys: {list(data[0].keys())}")
            elif isinstance(data, dict):
                sections.append(f"### {access_path}\n{len(data)} keys loaded")
                sections.append(f"Keys: {list(data.keys())[:10]}{'...' if len(data) > 10 else ''}")
        for name, content in sorted(self.text_files.items()):
            access_path = f'self.text_files["{name}"]'
            preview = content[:100].replace("\n", " ").strip()
            if len(content) > 100:
                preview += "..."
            file_lines = [
                f"### {access_path}",
                f"Size: {len(content):,} characters",
                f"Preview: {preview}",
            ]
            section_sizes = get_markdown_section_sizes(content)
            if section_sizes:
                file_lines.append("Sections:")
                for section_name, char_count in section_sizes:
                    file_lines.append(f"  - {section_name} ({char_count:,} chars)")
            sections.append("\n".join(file_lines))
        return "\n\n".join(sections)

    def get_markdown_section(self, markdown_content: str, section_header: str) -> str:
        return get_markdown_section(markdown_content, section_header)

    async def _run_evaluation(self, task_input: dict | DABStepInput) -> dict:
        """Entry point for evaluation framework."""
        inp = (
            task_input
            if isinstance(task_input, DABStepInput)
            else DABStepInput.from_dict(task_input)
        )

        if not inp.question:
            return {"response": "", "success": False, "error": "No question provided"}
        if not inp.data_dir:
            return {"response": "", "success": False, "error": "No data_dir provided"}

        try:
            self._load_data(inp.data_dir)
            self.context["data_summary"] = self._get_data_context()

            rules_lawyer = self.RulesLawyer(llm=self._llm)
            self.relevant_rules = await rules_lawyer.find_rules(
                question=inp.question,
                text_files=self.text_files,
                dataframes=self.dataframes,
                json_files=self.json_files,
            )
            self.context["relevant_rules"] = self.relevant_rules

            verifier = self.SolutionVerifier(llm=self._llm)
            hint = ""
            answer: str = ""
            explanation: str = ""

            for _ in range(self.MAX_RETRIES + 1):
                compute_result = await self.compute_answer(
                    question=inp.question,
                    guidelines=inp.guidelines,
                    hint=hint,
                )
                answer = compute_result.answer
                explanation = compute_result.explanation

                verify_result = await verifier.verify(
                    question=inp.question,
                    guidelines=inp.guidelines,
                    answer=answer,
                    explanation=explanation,
                    relevant_rules=self.relevant_rules,
                    text_files=self.text_files,
                    dataframes=self.dataframes,
                    json_files=self.json_files,
                )
                if verify_result.accepted:
                    break
                hint = verify_result.reasoning

            result_str = str(answer) if answer else ""
            return {
                "response": result_str,
                "success": True,
                "result": answer,
                "answer": result_str,
            }

        except Exception as e:
            return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=5)))
    async def compute_answer(self, question: str, guidelines: str, hint: str = "") -> AnswerResult:
        """Compute the answer using data analysis.

        ## Question
        {question}

        ## Answer Format Guidelines
        {guidelines}

        ## Previous Attempt Feedback
        {hint}

        ## REQUIRED STEPS

        ### Step 1: Review Rules
        Check `<relevant_rules>`. If empty, consider "Not Applicable".

        ### Step 2: Understand the Question
        What EXACTLY is being asked? What format is required?

        ### Step 3: If Fee Matching is Involved

        **A. Calculate Monthly Metrics FIRST:**
        ```python
        merchant_txns = payments[payments['merchant'] == merchant_name]
        period_txns = merchant_txns[(merchant_txns['year'] == year) & time_filter]
        monthly_volume = period_txns['eur_amount'].sum()
        monthly_fraud_rate = period_txns['has_fraudulent_dispute'].sum() / len(period_txns) * 100
        ```

        **B. Filter Fees using helper:**
        ```python
        matching = [f for f in fees if fee_matches(
            f, card_scheme, account_type, merchant['capture_delay'],
            is_credit, aci, mcc, intracountry, monthly_vol, fraud_rate)]
        best = find_lowest_fee(matching, txn['eur_amount'])
        ```

        **C. For "Compare ALL X" questions (e.g. which ACI has lowest fees):**
        Iterate ALL 7 ACIs (A-G) explicitly, pick the one with LOWEST total fee.

        ### Step 4: Compute Result — incrementally, checking output each step.

        ### Step 5: Verify Format
        - **14-DECIMAL RULE (OPT59)**: guidelines say "14 decimals" →
          1. `value = round_eur(value)`  # -0.941192 → -0.94
          2. `answer = format_numeric_answer(value, guidelines)`  # "-0.94000000000000"
        - 2/3/6 decimals: do NOT use round_eur, format directly.

        ### Step 6: Validate Before Returning
        - Numeric: `assert isinstance(answer, (int, float))`
        - Fee ID lists: verify all IDs exist in fees.json
        - "All X" questions: confirm you iterated ALL options

        ## Return
        Return AnswerResult(answer=str, explanation=str).
        - **answer**: final answer matching guidelines format exactly
        - **explanation**: key calculation steps and rules applied

        ## "Not Applicable"
        Return ONLY when the concept is NOT DEFINED in documentation.
        """
        ...
