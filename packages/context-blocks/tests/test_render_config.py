# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import BaseModel, ValidationError

from context_blocks.formatter import (
    MarkdownBlockFormatter,
    OpenAIProviderFormatter,
    XMLBlockFormatter,
)
from context_blocks.render_config import RenderConfig


def test_render_config_is_pydantic_model():
    assert issubclass(RenderConfig, BaseModel)


def test_render_config_defaults():
    c = RenderConfig()
    assert isinstance(c.block_formatter, XMLBlockFormatter)
    assert isinstance(c.provider_formatter, OpenAIProviderFormatter)


def test_render_config_frozen():
    c = RenderConfig()
    with pytest.raises(ValidationError):
        c.block_formatter = MarkdownBlockFormatter()


def test_render_config_custom_formatters():
    c = RenderConfig(block_formatter=MarkdownBlockFormatter())
    assert isinstance(c.block_formatter, MarkdownBlockFormatter)
    assert isinstance(c.provider_formatter, OpenAIProviderFormatter)


def test_merge_with_overrides_only_explicit_fields():
    base = RenderConfig()
    override = RenderConfig(block_formatter=MarkdownBlockFormatter())
    merged = base.merge_with(override)
    assert isinstance(merged.block_formatter, MarkdownBlockFormatter)
    assert isinstance(merged.provider_formatter, OpenAIProviderFormatter)  # not overridden


def test_merge_with_empty_fields_set_raises():
    # RenderConfig() has empty model_fields_set (default_factory fields don't set model_fields_set).
    # merge_with() must raise — use None to express "no override", not a default-constructed config.
    base = RenderConfig(block_formatter=MarkdownBlockFormatter())
    no_overrides = RenderConfig()
    assert not no_overrides.model_fields_set
    with pytest.raises(ValueError, match="merge_with"):
        base.merge_with(no_overrides)


def test_merge_with_none_returns_self():
    base = RenderConfig(block_formatter=MarkdownBlockFormatter())
    assert base.merge_with(None) is base
