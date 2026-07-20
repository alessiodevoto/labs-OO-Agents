# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verbatim provider-item replay for prompt caching (reasoning models).

The chain under test:

    ToolCallEvent.provider_items
      -> _event_block_to_messages -> RenderedMessage.provider_items
      -> OpenAIProviderFormatter (chat dict carries "provider_items")
      -> ResponsesClient._transform_messages (emits items verbatim)
      -> CompletionClient path strips the key (chat API has no equivalent)

Without verbatim replay, reasoning models get near-zero prompt-cache hits in
multi-turn tool use (see tmp/cache_awareness analysis).
"""

from nooa.context_blocks.events import ToolCallEvent, ToolResult
from nooa.context_blocks.formatter import (
    OpenAIProviderFormatter,
    ResponsesProviderFormatter,
    _event_block_to_messages,
)
from nooa.context_blocks.models import BlockMetadata, ResolvedBlock, Role
from nooa.strategies.codeact import _extract_provider_batch, _slice_provider_items
from nooa.unifiedllm.unifiedllm import _strip_provider_items

REASONING_ITEM = {
    "type": "reasoning",
    "id": "rs_abc",
    "summary": [],
    "encrypted_content": "gAAAA-opaque-blob",
}
FC_ITEM = {
    "type": "function_call",
    "id": "fc_abc",
    "call_id": "call_1",
    "name": "execute_python",
    "arguments": '{"code": "print(1)"}',
}


def _tool_block(provider_items=None) -> ResolvedBlock:
    event = ToolCallEvent(
        tool_call_id="call_1",
        name="execute_python",
        arguments={"code": "print(1)"},
        result=ToolResult(tool_call_id="call_1", content="1"),
        provider_items=provider_items,
    )
    return ResolvedBlock(
        key="event_1", content="", role=Role.ASSISTANT, metadata=BlockMetadata(), event=event
    )


class TestEventToRenderedMessage:
    def test_provider_items_carried(self):
        msgs = _event_block_to_messages(_tool_block([REASONING_ITEM, FC_ITEM]), wrap_content=None)
        assert msgs[0].provider_items == [REASONING_ITEM, FC_ITEM]

    def test_absent_by_default(self):
        msgs = _event_block_to_messages(_tool_block(), wrap_content=None)
        assert msgs[0].provider_items is None


class TestOpenAIChatFormatter:
    def test_tool_call_message_carries_items(self):
        msgs = _event_block_to_messages(_tool_block([REASONING_ITEM, FC_ITEM]), wrap_content=None)
        out = OpenAIProviderFormatter().format(msgs)
        assert out[0]["provider_items"] == [REASONING_ITEM, FC_ITEM]
        assert out[0]["tool_calls"][0]["id"] == "call_1"

    def test_no_items_no_key(self):
        msgs = _event_block_to_messages(_tool_block(), wrap_content=None)
        out = OpenAIProviderFormatter().format(msgs)
        assert "provider_items" not in out[0]


class TestResponsesTransform:
    def _client(self):
        from nooa.unifiedllm.unifiedllm import ResponsesClient

        return ResponsesClient.__new__(ResponsesClient)  # transform is self-contained

    def test_verbatim_replay(self):
        chat_msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "execute_python", "arguments": '{"code": "1"}'},
                    }
                ],
                "provider_items": [REASONING_ITEM, FC_ITEM],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "1"},
        ]
        input_messages, instructions = self._client()._transform_messages(chat_msgs)
        assert instructions == "sys"
        # reasoning + function_call replayed verbatim, not reconstructed
        assert input_messages[1] == REASONING_ITEM
        assert input_messages[2] == FC_ITEM
        assert input_messages[3]["type"] == "function_call_output"

    def test_without_items_reconstructs(self):
        chat_msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "execute_python", "arguments": '{"code": "1"}'},
                    }
                ],
            },
        ]
        input_messages, _ = self._client()._transform_messages(chat_msgs)
        assert input_messages[0] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "execute_python",
            "arguments": '{"code": "1"}',
        }

    def test_native_responses_formatter_replays(self):
        msgs = _event_block_to_messages(_tool_block([REASONING_ITEM, FC_ITEM]), wrap_content=None)
        out = ResponsesProviderFormatter().format(msgs)
        assert out[0] == REASONING_ITEM
        assert out[1] == FC_ITEM


class TestChatCompletionsStrip:
    def test_strip_provider_items(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "provider_items": [REASONING_ITEM]},
        ]
        stripped = _strip_provider_items(msgs)
        assert "provider_items" not in stripped[1]
        # non-mutating: original untouched, untouched messages not copied
        assert "provider_items" in msgs[1]
        assert stripped[0] is msgs[0]


class TestBatchHelpers:
    class _Resp:
        def __init__(self, batch):
            self.assistant_message = {"_batch": batch}

    def test_extract_strips_none_values(self):
        batch = [{**REASONING_ITEM, "status": None}, FC_ITEM]
        items = _extract_provider_batch(self._Resp(batch))
        assert items is not None
        assert "status" not in items[0]

    def test_extract_none_for_chat_shape(self):
        class R:
            assistant_message = {"role": "assistant", "content": "hi"}

        assert _extract_provider_batch(R()) is None

    def test_slice_single_call(self):
        assert _slice_provider_items([REASONING_ITEM, FC_ITEM], "call_1") == [
            REASONING_ITEM,
            FC_ITEM,
        ]

    def test_slice_multiple_calls_no_overlap(self):
        r2 = {**REASONING_ITEM, "id": "rs_2"}
        fc2 = {**FC_ITEM, "id": "fc_2", "call_id": "call_2"}
        batch = [REASONING_ITEM, FC_ITEM, r2, fc2]
        assert _slice_provider_items(batch, "call_1") == [REASONING_ITEM, FC_ITEM]
        assert _slice_provider_items(batch, "call_2") == [r2, fc2]

    def test_slice_unknown_call_id(self):
        assert _slice_provider_items([REASONING_ITEM, FC_ITEM], "call_x") is None


class TestReasoningReplayParams:
    def test_apply_reasoning_replay(self):
        from nooa.unifiedllm.unifiedllm import ResponsesClient

        client = ResponsesClient.__new__(ResponsesClient)
        client.reasoning_replay = True
        params: dict = {}
        client._apply_reasoning_replay(params)
        assert params["store"] is False
        assert "reasoning.encrypted_content" in params["include"]

    def test_disabled_no_changes(self):
        from nooa.unifiedllm.unifiedllm import ResponsesClient

        client = ResponsesClient.__new__(ResponsesClient)
        client.reasoning_replay = False
        params: dict = {}
        client._apply_reasoning_replay(params)
        assert params == {}

    def test_caller_store_wins(self):
        from nooa.unifiedllm.unifiedllm import ResponsesClient

        client = ResponsesClient.__new__(ResponsesClient)
        client.reasoning_replay = True
        params: dict = {"store": True, "include": ["message.output_text.logprobs"]}
        client._apply_reasoning_replay(params)
        assert params["store"] is True
        assert params["include"] == [
            "message.output_text.logprobs",
            "reasoning.encrypted_content",
        ]
