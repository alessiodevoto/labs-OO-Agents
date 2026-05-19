"""Tests for cache_control injection in ResponsesClient."""

from unittest.mock import AsyncMock, patch

import litellm
import pytest

from nemo_oo_agents.unifiedllm import ResponsesClient


def make_mock_responses_response(content: str = "ok"):
    """Create a minimal litellm.ResponsesAPIResponse for testing."""
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.output = [
        MagicMock(type="message", content=[MagicMock(type="output_text", text=content)])
    ]
    resp.output_text = content
    resp.usage = None
    return resp


class TestResponsesClientCacheControlDefaults:
    """ResponsesClient should have cache_control_injection_points by default."""

    def test_default_has_system_and_last_tool(self):
        """ResponsesClient gets the default injection points from UnifiedLLM."""
        client = ResponsesClient(model="test-model")
        assert {"role": "system"} in client.cache_control_injection_points
        assert {"role": "tool", "position": "last"} in client.cache_control_injection_points

    def test_inject_cache_control_on_system(self):
        """_inject_cache_control marks system messages."""
        client = ResponsesClient(model="test-model")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        result = client._inject_cache_control(messages, [{"role": "system"}])
        assert result[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in result[1]


class TestResponsesClientCacheControlInjection:
    """Tests that cache_control is injected and preserved through _transform_messages."""

    @pytest.fixture
    def client(self):
        return ResponsesClient(model="test-model")

    def test_system_cache_control_not_in_output(self, client):
        """System messages are extracted to instructions; cache_control on system is harmless."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hi"},
        ]
        prepared = client._inject_cache_control(messages, [{"role": "system"}])
        input_msgs, instructions = client._transform_messages(prepared)
        # System extracted to instructions
        assert instructions == "System prompt"
        # Input should just have the user message
        assert len(input_msgs) == 1
        assert input_msgs[0]["role"] == "user"

    def test_tool_cache_control_preserved_in_native_format(self, client):
        """cache_control on tool messages is preserved as function_call_output."""
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "run", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "result 1", "tool_call_id": "tc1"},
            {"role": "user", "content": "What next?"},
        ]
        # Inject cache_control on last tool
        prepared = client._inject_cache_control(messages, [{"role": "tool", "position": "last"}])
        input_msgs, instructions = client._transform_messages(prepared)

        # Find the function_call_output item
        fco_items = [m for m in input_msgs if m.get("type") == "function_call_output"]
        assert len(fco_items) == 1
        # Should have cache_control preserved
        assert "cache_control" in fco_items[0]
        assert fco_items[0]["cache_control"] == {"type": "ephemeral"}

    def test_native_format_function_call_output_gets_cache_control(self, client):
        """When messages are already in native format, function_call_output gets marked."""
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Do something"},
            {"type": "function_call", "call_id": "tc1", "name": "run", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "tc1", "output": "result"},
            {"role": "user", "content": "Next"},
        ]
        # The injection should find function_call_output as equivalent to "tool"
        prepared = client._inject_cache_control(messages, [{"role": "tool", "position": "last"}])
        # The function_call_output item should have cache_control
        fco = [m for m in prepared if m.get("type") == "function_call_output"]
        assert len(fco) == 1
        assert fco[0].get("cache_control") == {"type": "ephemeral"}

    def test_user_message_cache_control_preserved(self, client):
        """cache_control on user messages is preserved in native format."""
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
        ]
        prepared = client._inject_cache_control(messages, [{"role": "user"}])
        input_msgs, _ = client._transform_messages(prepared)
        user_msgs = [m for m in input_msgs if m.get("role") == "user"]
        assert user_msgs[0].get("cache_control") == {"type": "ephemeral"}


class TestResponsesClientEndToEnd:
    """End-to-end tests that cache_control reaches litellm.aresponses."""

    @pytest.mark.asyncio
    async def test_acall_injects_cache_control(self):
        """acall() injects cache_control on messages before calling litellm."""
        client = ResponsesClient(model="openai/gpt-5.5")
        mock_response = make_mock_responses_response()

        with patch("litellm.aresponses", new_callable=AsyncMock) as mock_aresponses:
            mock_aresponses.return_value = mock_response

            await client.acall(
                [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Do something"},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "tc1", "type": "function",
                         "function": {"name": "run", "arguments": "{}"}}
                    ]},
                    {"role": "tool", "content": "tool output", "tool_call_id": "tc1"},
                    {"role": "user", "content": "Current turn"},
                ],
            )

            call_kwargs = mock_aresponses.call_args[1]
            input_items = call_kwargs["input"]

            # Find function_call_output (tool result)
            fco_items = [m for m in input_items if m.get("type") == "function_call_output"]
            assert len(fco_items) == 1
            # Last tool should have cache_control
            assert fco_items[0].get("cache_control") == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_acall_no_injection_when_empty(self):
        """No cache_control when injection_points is empty."""
        client = ResponsesClient(model="openai/gpt-5.5")
        client.cache_control_injection_points = []
        mock_response = make_mock_responses_response()

        with patch("litellm.aresponses", new_callable=AsyncMock) as mock_aresponses:
            mock_aresponses.return_value = mock_response

            await client.acall(
                [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "Hi"},
                ],
            )

            call_kwargs = mock_aresponses.call_args[1]
            input_items = call_kwargs["input"]
            # No items should have cache_control
            for item in input_items:
                assert "cache_control" not in item

    @pytest.mark.asyncio
    async def test_acall_custom_injection_points(self):
        """Custom injection points override defaults."""
        client = ResponsesClient(model="openai/gpt-5.5")
        mock_response = make_mock_responses_response()

        with patch("litellm.aresponses", new_callable=AsyncMock) as mock_aresponses:
            mock_aresponses.return_value = mock_response

            await client.acall(
                [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi"},
                    {"role": "user", "content": "Bye"},
                ],
                cache_control_injection_points=[{"role": "user", "position": "last"}],
            )

            call_kwargs = mock_aresponses.call_args[1]
            input_items = call_kwargs["input"]
            # Last user message should have content-block-level cache_control
            last_user = [m for m in input_items if m.get("role") == "user"][-1]
            assert isinstance(last_user["content"], list)
            assert last_user["content"][0]["cache_control"] == {"type": "ephemeral"}
