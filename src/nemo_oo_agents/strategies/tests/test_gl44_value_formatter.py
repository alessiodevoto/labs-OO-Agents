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

    def test_truncating_pformat_usable_as_formatter(self):
        # TDD: will fail until Change 5 is implemented
        from nemo_oo_agents.agentdoc import truncating_pformat

        big = list(range(10_000))
        call = _make_call(args=(big,))
        result = call.format_parameters_as_code(
            value_formatter=lambda v: truncating_pformat(v, max_chars=200)
        )
        # Output should be bounded
        assert len(result) < 2000

    def test_none_formatter_uses_truncating_pformat(self):
        # Default formatter is truncating_pformat — value still appears in output.
        call = _make_call(args=("hello",))
        result = call.format_parameters_as_code(value_formatter=None)
        assert "hello" in result


class TestFormatParametersTcParam:
    """format_parameters_as_code(tc=TruncationConfig(...)) uses pformat with structural limits."""

    def _make_call(self, **kwargs):
        return CurrentCall(
            id="test-id",
            method_name="test",
            decorator="agent",
            signature="(items: list)",
            args=(kwargs.get("items", []),),
            kwargs={},
        )

    def test_tc_applies_max_pprint_elements(self):
        """When tc provided, large lists are truncated to max_pprint_elements, not shown in full."""
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        call = self._make_call(items=list(range(1000)))
        tc = TruncationConfig(max_pprint_elements=5)
        result = call.format_parameters_as_code(tc=tc)
        # Head+tail: 5 elements → first 3 + last 2. Middle items (e.g. 500) not shown.
        assert "500" not in result  # Middle element absent
        assert "not shown" in result  # Truncation notice present

    def test_tc_applies_max_pprint_depth(self):
        """When tc provided, deeply nested structures are truncated at max_pprint_depth."""
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        nested = {"a": {"b": {"c": {"d": "deep_value"}}}}
        call = CurrentCall(
            id="test-id",
            method_name="test",
            decorator="agent",
            signature="(data: dict)",
            args=(nested,),
            kwargs={},
        )
        tc = TruncationConfig(max_pprint_depth=2)
        result = call.format_parameters_as_code(tc=tc)
        # deep_value is at depth 4 — should not appear when max_pprint_depth=2
        assert "deep_value" not in result

    def test_value_formatter_takes_precedence_over_tc(self):
        """When both value_formatter and tc provided, value_formatter wins."""
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        call = _make_call(args=(list(range(1000)),))
        tc = TruncationConfig(max_pprint_elements=5)
        result = call.format_parameters_as_code(
            value_formatter=lambda v: "CUSTOM_OUTPUT",
            tc=tc,
        )
        assert "CUSTOM_OUTPUT" in result
        # pformat with tc limits was NOT used (no truncation notice)
        assert "not shown" not in result

    def test_tc_none_falls_back_to_repr(self):
        """When tc=None and no value_formatter, defaults to repr."""
        call = _make_call(args=(42,))
        result = call.format_parameters_as_code(tc=None)
        assert "42" in result
