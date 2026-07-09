# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the LoCoMo benchmark adapter (parsing + grading; no network)."""

import json
import sys
from pathlib import Path

_EX = Path(__file__).resolve().parents[4] / "examples" / "memory_bench"
sys.path.insert(0, str(_EX))

import locomo  # noqa: E402

_MINI = {
    "conversation": {
        "speaker_a": "A",
        "speaker_b": "B",
        "session_1_date_time": "1 Jan 2023",
        "session_1": [
            {"speaker": "A", "dia_id": "D1:1", "text": "hi"},
            {"speaker": "B", "dia_id": "D1:2", "text": "the escalation code is QX-9"},
        ],
        "session_2_date_time": "2 Jan 2023",
        "session_2": [{"speaker": "A", "dia_id": "D2:1", "text": "bye"}],
    },
    "qa": [
        {"question": "what is the code?", "answer": "QX-9", "category": 4},
        {"question": "unanswerable?", "answer": "Not mentioned", "category": 5},
    ],
    "sample_id": "x",
}


def test_load_sample_parses_turns_in_session_order(tmp_path):
    p = tmp_path / "mini.json"
    p.write_text(json.dumps([_MINI]))
    turns, qa = locomo.load_sample(p, 0)
    assert [t.text for t in turns] == ["hi", "the escalation code is QX-9", "bye"]
    assert turns[1].as_memory_text() == "[1 Jan 2023] B: the escalation code is QX-9"


def test_load_sample_excludes_adversarial_category(tmp_path):
    p = tmp_path / "mini.json"
    p.write_text(json.dumps([_MINI]))
    _, qa = locomo.load_sample(p, 0)
    assert len(qa) == 1  # category 5 dropped
    assert qa[0].category == 4 and qa[0].answer == "QX-9"


def test_grade_substring():
    assert locomo.grade_substring("QX-9", "the escalation code is qx-9 indeed")
    assert locomo.grade_substring(
        "Psychology, counseling", "She studied psychology and counseling."
    )
    assert not locomo.grade_substring("QX-9", "No information available")
    assert not locomo.grade_substring("", "anything")
