# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SWE-bench diff response normalization."""

import pytest
from nemo_oo_agents_benchmarks.agents.swebench_todo import (
    Diff,
    SWEBenchTodoAgent,
    _extract_unified_diff,
    _is_valid_unified_diff,
)
from pydantic import ValidationError

RAW_DIFF = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-old
+new
"""


class _RunResult:
    def __init__(self, text: str):
        self.text = text


class _Shell:
    def __init__(self, diff: str):
        self.diff = diff

    async def run(self, command: str):
        assert command == "git diff HEAD"
        return _RunResult(self.diff)


class _Agent:
    solve_task = SWEBenchTodoAgent.solve_task

    def __init__(self, *, result, worktree_diff: str = ""):
        self.result = result
        self.shell = _Shell(worktree_diff)

    async def _solve_task(self, description: str, response_format: str = ""):
        return self.result


def test_extracts_diff_from_xml_wrapper():
    wrapped = f"""## Summary

Fixed it.

<diff>
{RAW_DIFF}</diff>
"""
    assert _extract_unified_diff(wrapped) == RAW_DIFF.strip()


def test_extracts_diff_from_markdown_fence_with_prose():
    wrapped = f"""Here is the patch:

```diff
{RAW_DIFF}```
"""
    assert _extract_unified_diff(wrapped) == RAW_DIFF.strip()


def test_diff_from_patch_normalizes_wrappers():
    diff = Diff.from_patch(f"""<diff>
{RAW_DIFF}</diff>""")
    assert diff.source == "patch"
    assert diff.patch == RAW_DIFF.strip()


def test_diff_rejects_non_diff_text():
    with pytest.raises(ValidationError):
        Diff.from_patch("""## Summary
No patch here""")


def test_diff_from_worktree_is_explicit_sentinel():
    diff = Diff.from_worktree()
    assert diff.source == "worktree"
    assert diff.patch is None


@pytest.mark.asyncio
async def test_solve_task_prefers_worktree_diff_over_model_prose():
    agent = _Agent(
        result="""## Summary
not a raw diff""",
        worktree_diff=RAW_DIFF,
    )
    assert await agent.solve_task("task", "diff") == RAW_DIFF.strip()


@pytest.mark.asyncio
async def test_solve_task_uses_validated_model_diff_when_worktree_empty():
    agent = _Agent(result=Diff.from_patch(RAW_DIFF), worktree_diff="")
    assert await agent.solve_task("task", "diff") == RAW_DIFF.strip()


@pytest.mark.asyncio
async def test_solve_task_rejects_empty_worktree_sentinel():
    agent = _Agent(result=Diff.from_worktree(), worktree_diff="")
    assert await agent.solve_task("task", "diff") == ""


def test_raw_diff_validator_contract():
    assert _is_valid_unified_diff(RAW_DIFF)
    assert not _is_valid_unified_diff(f"""<diff>
{RAW_DIFF}</diff>""")
    assert not _is_valid_unified_diff(f"""## Summary
{RAW_DIFF}""")
