# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TDD: gl-44 — format_parameters_as_code accepts optional value_formatter.

Change 5 of truncation-2.0: format_parameters_as_code(value_formatter=callable)
allows PredictStrategy to cap large parameter values before embedding in prompts.
"""

from nemo_oo_agents.strategies.current_call import CurrentCall


def _make_call(method_name="test", signature=None, args=(), kwargs=None):
    return CurrentCall(
        id="test-id",
        method_name=method_name,
        decorator="agent",
        signature=signature,
        args=args,
        kwargs=kwargs or {},
    )


class TestFormatParametersValueFormatter:
    """format_parameters_as_code must accept optional value_formatter."""

    def test_default_formatter_renders_values(self):
        call = _make_call(args=(42,), kwargs={"flag": True})
        result = call.format_parameters_as_code()
        assert "42" in result
        assert "True" in result

    def test_accepts_custom_value_formatter(self):
        # TDD: will fail until Change 5 is implemented
        call = _make_call(args=(42,))
        result = call.format_parameters_as_code(value_formatter=lambda v: "CUSTOM")
        assert "CUSTOM" in result

    def test_formatter_receives_actual_python_value(self):
        # TDD: will fail until Change 5 is implemented
        received = []
        call = _make_call(args=([1, 2, 3],))
        call.format_parameters_as_code(value_formatter=lambda v: (received.append(v), repr(v))[1])
        assert [1, 2, 3] in received

    def test_formatter_applied_to_kwargs(self):
        # TDD: will fail until Change 5 is implemented
        call = _make_call(kwargs={"x": 99})
        result = call.format_parameters_as_code(value_formatter=lambda v: f"FMT({v})")
        assert "FMT(99)" in result

    def test_parameter_names_always_present(self):
        # TDD: will fail until Change 5 is implemented
        call = _make_call(
            signature="(data: str, count: int)",
            args=("hello", 5),
        )
        result = call.format_parameters_as_code(value_formatter=lambda v: "<cap>")
        assert "data" in result
        assert "count" in result
        assert "<cap>" in result

    def test_safe_pformat_usable_as_formatter(self):
        # TDD: will fail until Change 5 is implemented
        from agentdoc import safe_pformat

        big = list(range(10_000))
        call = _make_call(args=(big,))
        result = call.format_parameters_as_code(
            value_formatter=lambda v: safe_pformat(v, max_chars=200)
        )
        # Output should be bounded
        assert len(result) < 2000

    def test_none_formatter_uses_safe_pformat(self):
        # Default formatter is safe_pformat — value still appears in output.
        call = _make_call(args=("hello",))
        result = call.format_parameters_as_code(value_formatter=None)
        assert "hello" in result
