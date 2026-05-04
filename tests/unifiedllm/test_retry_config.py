# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import asyncio

import pytest
from pydantic import BaseModel, ValidationError

from nemo_oo_agents.unifiedllm.retry_config import RetryConfig


def test_retry_config_is_pydantic_model():
    assert issubclass(RetryConfig, BaseModel)


def test_retry_config_defaults():
    c = RetryConfig()
    assert c.max_retries == 3
    assert c.base_delay == 1.0
    assert c.max_delay == 60.0
    assert c.exponential_base == 2.0
    assert c.jitter_factor == 0.3
    assert c.rate_limit_extra_retries == 3
    assert c.rate_limit_base_delay == 3.0
    assert c.rate_limit_backoff_base == 3.0
    assert c.retryable_status_codes == frozenset({429, 500, 502, 503, 504})
    assert asyncio.TimeoutError in c.retryable_exceptions
    assert c.retry_on_empty_content is False
    assert c.on_retry is None


def test_retry_config_frozen():
    c = RetryConfig()
    with pytest.raises(ValidationError):
        c.max_retries = 5


def test_retryable_status_codes_is_frozenset():
    c = RetryConfig()
    assert isinstance(c.retryable_status_codes, frozenset)


def test_retryable_exceptions_is_typed_tuple():
    c = RetryConfig()
    assert isinstance(c.retryable_exceptions, tuple)
    for exc_type in c.retryable_exceptions:
        assert isinstance(exc_type, type)
        assert issubclass(exc_type, BaseException)


def test_merge_with_overrides_only_explicit_fields():
    base = RetryConfig()
    override = RetryConfig(max_retries=5)
    merged = base.merge_with(override)
    assert merged.max_retries == 5
    assert merged.base_delay == 1.0  # not overridden


def test_merge_with_rejects_empty_fields_set():
    # The ValueError fires when model_fields_set is empty
    # (e.g. model_validate({}) with all-default fields)
    base = RetryConfig()
    empty_fields = RetryConfig.model_validate({})
    assert not empty_fields.model_fields_set
    with pytest.raises(ValueError, match="merge_with"):
        base.merge_with(empty_fields)


def test_merge_with_known_limitation_round_trip():
    # KNOWN LIMITATION (Option A): model_validate(model_dump()) marks ALL
    # fields as set, so merge_with cannot detect "defaults only" configs.
    # Callers must always construct fresh: RetryConfig(field=value).
    base = RetryConfig()
    round_tripped = RetryConfig.model_validate(RetryConfig().model_dump())
    assert round_tripped.model_fields_set  # all fields appear "set"
    merged = base.merge_with(round_tripped)  # does not raise — known limitation
    assert merged == round_tripped


def test_rate_limit_backoff_base_is_new_field():
    # This field did not exist in the old dataclass
    c = RetryConfig(rate_limit_backoff_base=5.0)
    assert c.rate_limit_backoff_base == 5.0
