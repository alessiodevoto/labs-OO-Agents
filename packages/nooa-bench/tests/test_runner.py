# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the in-container NOOA benchmark runner."""

from __future__ import annotations

import json

from nooa_bench import runner


def test_task_input_includes_only_supplied_optional_fields():
    payload = runner._task_input(
        instruction="fix it",
        working_dir="/root",
        skill_mode="library_skill",
        skills_dir=None,
    )

    assert payload == {
        "user_message": "fix it",
        "working_dir": "/root",
        "skill_mode": "library_skill",
    }


def test_exit_code_from_result_keeps_agent_failure_explicit():
    assert runner._exit_code_from_result({"success": True}) == 0
    assert runner._exit_code_from_result({"success": False}) == 1
    assert runner._exit_code_from_result({}) == 1


def test_write_result_preserves_activated_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "LOGS_DIR", tmp_path)

    runner._write_result(
        {
            "success": True,
            "response": "pytest -q",
            "activated_skills": ["cmd.xlsx"],
            "n_input_tokens": 10,
            "n_output_tokens": 2,
        },
        model="openai/openai/openai/gpt-5.2",
        agent_type="bench",
    )

    payload = json.loads((tmp_path / "result.json").read_text())

    assert payload["success"] is True
    assert payload["activated_skills"] == ["cmd.xlsx"]
