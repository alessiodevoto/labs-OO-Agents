# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for extract_and_parse_json robustness."""

import json

import pytest

from unifiedllm import extract_and_parse_json


class TestExtractAndParseJson:
    def test_plain_json(self):
        result = extract_and_parse_json('{"answer": "42", "confidence": 0.9}')
        assert result == {"answer": "42", "confidence": 0.9}

    def test_markdown_fenced_json(self):
        text = '```json\n{"answer": "42"}\n```'
        result = extract_and_parse_json(text)
        assert result == {"answer": "42"}

    def test_markdown_bold_prefix(self):
        """LLMs sometimes wrap JSON in markdown bold markers."""
        text = '**{"answer": "42", "confidence": 0.9}**'
        result = extract_and_parse_json(text)
        assert result == {"answer": "42", "confidence": 0.9}

    def test_markdown_bold_prefix_only(self):
        text = '**{"answer": "hello"}**'
        result = extract_and_parse_json(text)
        assert result == {"answer": "hello"}

    def test_single_star_prefix(self):
        text = '*{"answer": "hello"}*'
        result = extract_and_parse_json(text)
        assert result == {"answer": "hello"}

    def test_bold_with_whitespace(self):
        text = '** {"answer": "hello"} **'
        result = extract_and_parse_json(text)
        assert result == {"answer": "hello"}

    def test_nested_json_extraction(self):
        text = 'Here is the answer: {"answer": "42"} and more text'
        result = extract_and_parse_json(text)
        assert result == {"answer": "42"}

    def test_empty_text_raises(self):
        with pytest.raises(json.JSONDecodeError):
            extract_and_parse_json("")

    def test_unparseable_raises(self):
        with pytest.raises(json.JSONDecodeError):
            extract_and_parse_json("this is not json at all")
