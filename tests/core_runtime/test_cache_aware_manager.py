# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CacheAwareManager: prefix diffing, breaker attribution, usage stats, demotion."""

from nooa.context_blocks.models import (
    BlockPart,
    RenderedMessage,
    Role,
    ToolCallInfo,
)
from nooa.runtime.cache_aware import CacheAwareManager


def _system(blocks: dict[str, str]) -> RenderedMessage:
    parts = [BlockPart(key=k, content=v) for k, v in blocks.items()]
    return RenderedMessage(role=Role.SYSTEM, content="\n\n".join(blocks.values()), parts=parts)


def _user(text: str) -> RenderedMessage:
    return RenderedMessage(role=Role.USER, content=text)


def _tool_turn(call_id: str, code: str, output: str) -> list[RenderedMessage]:
    return [
        RenderedMessage(
            role=Role.ASSISTANT,
            tool_call=ToolCallInfo(id=call_id, name="execute_python", arguments={"code": code}),
        ),
        RenderedMessage(role=Role.TOOL, content=output, tool_call_id=call_id),
    ]


SYS = {"system_prompt": "be good", "self": "doc(...)"}


class TestPrefixDiff:
    def test_first_request_clean(self):
        mgr = CacheAwareManager(enabled=True)
        report = mgr.observe_request("k", [_system(SYS), _user("task")])
        assert report is not None and report.clean

    def test_append_only_with_trailing_context_swap_is_clean(self):
        mgr = CacheAwareManager(enabled=True)
        base = [_system(SYS), _user("task"), *_tool_turn("c1", "print(1)", "1")]
        mgr.observe_request("k", [*base, _user("<context>turn=1</context>")])
        # next turn: trailing <context> replaced, new events appended after base
        report = mgr.observe_request(
            "k",
            [*base, *_tool_turn("c2", "print(2)", "2"), _user("<context>turn=2</context>")],
        )
        assert report is not None and report.clean

    def test_mutated_system_block_attributed(self):
        mgr = CacheAwareManager(enabled=True)
        mgr.observe_request("k", [_system(SYS), _user("task")])
        mutated = {**SYS, "self": "doc(...) CHANGED"}
        report = mgr.observe_request("k", [_system(mutated), _user("task")])
        assert report is not None and not report.clean
        assert report.breaker_kind == "system_blocks"
        assert report.mutated_blocks == ["self"]

    def test_mutated_event_mid_history_flagged(self):
        mgr = CacheAwareManager(enabled=True)
        base = [_system(SYS), _user("task"), *_tool_turn("c1", "print(1)", "1")]
        mgr.observe_request("k", [*base, _user("<context>1</context>")])
        rewritten = [_system(SYS), _user("task"), *_tool_turn("c1", "print(1)", "REWRITTEN")]
        report = mgr.observe_request(
            "k", [*rewritten, _user("more"), _user("<context>2</context>")]
        )
        assert report is not None and not report.clean
        assert report.breaker_kind == "event"
        assert report.breaker_index == 3

    def test_history_shrink_reported_as_rewrite(self):
        mgr = CacheAwareManager(enabled=True)
        msgs = [_system(SYS), _user("t"), *_tool_turn("c1", "a", "1"), *_tool_turn("c2", "b", "2")]
        mgr.observe_request("k", msgs)
        collapsed = [_system(SYS), _user("summary of the above")]
        report = mgr.observe_request("k", collapsed)
        assert report is not None and report.breaker_kind == "history_rewrite"

    def test_collapse_with_system_mutation_is_rewrite_not_churn(self):
        # A summarizer collapse that also rewrites the system message must be
        # classified as history_rewrite (info, no demotion counting) — not
        # attributed to system-block churn.
        demoted: list[str] = []
        mgr = CacheAwareManager(
            enabled=True, demote_after=1, on_demote=lambda k: (demoted.append(k), True)[1]
        )
        msgs = [_system(SYS), _user("t"), *_tool_turn("c1", "a", "1"), *_tool_turn("c2", "b", "2")]
        mgr.observe_request("k", msgs)
        collapsed = [_system({**SYS, "self": "doc CHANGED"}), _user("summary")]
        report = mgr.observe_request("k", collapsed)
        assert report is not None and report.breaker_kind == "history_rewrite"
        assert demoted == []

    def test_disabled_returns_none(self):
        mgr = CacheAwareManager(enabled=False)
        assert mgr.observe_request("k", [_user("x")]) is None

    def test_conversations_isolated_by_key(self):
        mgr = CacheAwareManager(enabled=True)
        mgr.observe_request("a", [_system(SYS), _user("task-a")])
        report = mgr.observe_request("b", [_system(SYS), _user("task-b")])
        assert report is not None and report.clean and report.prev_len == 0


class TestUsageStats:
    def test_accumulation_and_ceiling(self):
        mgr = CacheAwareManager(enabled=True, granularity=128)
        mgr.observe_usage("k", 1000, 0)
        mgr.observe_usage("k", 1500, 896)
        r = mgr.report()["k"]
        assert r["prompt_tokens"] == 2500
        assert r["cached_tokens"] == 896
        # ceiling: min(1000, 1500) // 128 * 128 = 896
        assert r["ceiling_tokens"] == 896
        assert r["ceiling_capture"] == 1.0

    def test_zero_prompt_ignored(self):
        mgr = CacheAwareManager(enabled=True)
        mgr.observe_usage("k", 0, 0)
        assert "k" not in mgr.report()


class TestDemotion:
    def test_demote_fires_after_threshold(self):
        demoted: list[str] = []
        mgr = CacheAwareManager(
            enabled=True, demote_after=2, on_demote=lambda k: (demoted.append(k), True)[1]
        )
        for i in range(3):
            mgr.observe_request("k", [_system({**SYS, "notes": f"v{i}"}), _user("task")])
        assert demoted == ["notes"]

    def test_demote_fires_once(self):
        calls: list[str] = []
        mgr = CacheAwareManager(
            enabled=True, demote_after=1, on_demote=lambda k: (calls.append(k), True)[1]
        )
        for i in range(4):
            mgr.observe_request("k", [_system({**SYS, "notes": f"v{i}"}), _user("t")])
        assert calls == ["notes"]


def _envelope(text: str) -> RenderedMessage:
    from nooa.context_blocks.models import BlockPart, TextPart

    content = f"<context>\n{text}\n</context>"
    return RenderedMessage(
        role=Role.USER,
        content=content,
        parts=[
            TextPart(text="<context>\n"),
            BlockPart(key="state", content=text),
            TextPart(text="\n</context>"),
        ],
    )


class TestContextTrail:
    def _mgr(self):
        return CacheAwareManager(enabled=True, context_trail=True)

    def test_consecutive_requests_become_append_only(self):
        mgr = self._mgr()
        base = [_system(SYS), _user("task")]
        out1 = mgr.stabilize_messages("k", [*base, _envelope("turn=1")])
        core2 = [*base, *_tool_turn("c1", "print(1)", "1")]
        out2 = mgr.stabilize_messages("k", [*core2, _envelope("turn=2")])
        # out2 must extend out1 byte-for-byte
        assert out2[: len(out1)] == out1
        # and end with the new snapshot
        assert out2[-1].content == "<context>\nturn=2\n</context>"
        # old snapshot retained at its historical position
        assert sum(1 for m in out2 if (m.content or "").startswith("<context>")) == 2

    def test_unchanged_envelope_not_duplicated(self):
        mgr = self._mgr()
        base = [_system(SYS), _user("task")]
        out1 = mgr.stabilize_messages("k", [*base, _envelope("same")])
        core2 = [*base, *_tool_turn("c1", "x", "1")]
        out2 = mgr.stabilize_messages("k", [*core2, _envelope("same")])
        assert out2[: len(out1)] == out1
        assert sum(1 for m in out2 if (m.content or "").startswith("<context>")) == 1

    def test_history_rewrite_resets_trail(self):
        mgr = self._mgr()
        base = [_system(SYS), _user("task"), *_tool_turn("c1", "a", "1")]
        mgr.stabilize_messages("k", [*base, _envelope("t1")])
        collapsed = [_system(SYS), _user("summary")]
        out = mgr.stabilize_messages("k", [*collapsed, _envelope("t2")])
        # trail reset: only the current snapshot survives
        assert sum(1 for m in out if (m.content or "").startswith("<context>")) == 1
        assert out[-1].content == "<context>\nt2\n</context>"

    def test_no_envelope_passthrough(self):
        mgr = self._mgr()
        msgs = [_system(SYS), _user("task")]
        assert mgr.stabilize_messages("k", msgs) is msgs

    def test_disabled_passthrough(self):
        mgr = CacheAwareManager(enabled=True, context_trail=False)
        msgs = [_system(SYS), _user("task"), _envelope("t1")]
        assert mgr.stabilize_messages("k", msgs) is msgs

    def test_three_turns_chain(self):
        mgr = self._mgr()
        base = [_system(SYS), _user("task")]
        outs = []
        core = base
        for i in range(1, 4):
            outs.append(mgr.stabilize_messages("k", [*core, _envelope(f"turn={i}")]))
            core = [*core, *_tool_turn(f"c{i}", f"code{i}", str(i))]
        for a, b in zip(outs, outs[1:], strict=False):
            assert b[: len(a)] == a

    def test_stabilized_lists_report_clean_in_observe(self):
        mgr = self._mgr()
        base = [_system(SYS), _user("task")]
        out1 = mgr.stabilize_messages("k", [*base, _envelope("turn=1")])
        mgr.observe_request("k", out1)
        core2 = [*base, *_tool_turn("c1", "x", "1")]
        out2 = mgr.stabilize_messages("k", [*core2, _envelope("turn=2")])
        report = mgr.observe_request("k", out2)
        assert report is not None and report.clean


class TestContextManagerDemote:
    def test_demote_to_dynamic_moves_partition(self):
        from nooa.runtime.context_manager import ContextManager

        cm = ContextManager()
        cm.set_static("notes", "hello")
        assert cm.is_static("notes")
        assert cm.demote_to_dynamic("notes") is True
        assert not cm.is_static("notes")
        assert cm["notes"] == "hello"

    def test_demote_missing_key(self):
        from nooa.runtime.context_manager import ContextManager

        assert ContextManager().demote_to_dynamic("nope") is False
