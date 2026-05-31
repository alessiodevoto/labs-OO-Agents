# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ShellTools3 — the Python-native bake-off shell."""

import pytest

from nemo_oo_agents.tools._match import Match, Span
from nemo_oo_agents.tools.shell_tools3 import ShellTools3


@pytest.fixture
def sh(tmp_path):
    return ShellTools3(cwd=str(tmp_path))


async def test_run_parity(sh):
    r = await sh.run("echo hi")
    assert r.strip() == "hi" and r.success
    r = await sh.run("python3 -c 'import sys;print(len(sys.stdin.read()))'", stdin="x" * 42)
    assert r.strip() == "42"


async def test_pyp_streams(sh):
    await sh.write_file("m.py", "def a():\n    return parse(x)\n\ndef b():\n    return parse(y)\n")
    assert await sh.rg("parse", "m.py").count() == 2
    lines = await sh.rg("def ", "m.py").collect()
    assert len(lines) == 2


async def test_matches_structured(sh):
    await sh.write_file("m.py", "def a():\n    return parse(x)\n")
    ms = await sh.rg("parse", "m.py").matches()
    assert len(ms) == 1
    m = ms[0]
    assert isinstance(m, Match)
    assert m.line == 2 and m.matched == "parse"
    assert "return parse(x)" in m.text
    assert "2| " in m.numbered
    assert isinstance(m.span, Span)


async def test_replace_match_region(sh, tmp_path):
    await sh.write_file("m.py", "x\nTARGET line\ny\n")
    ms = await sh.rg("TARGET", "m.py").matches()
    r = await sh.replace(ms[0], "REPLACED")
    assert r.success
    content = (tmp_path / "m.py").read_text()
    assert "REPLACED" in content and "TARGET" not in content


async def test_replace_span_surgical(sh, tmp_path):
    await sh.write_file("m.py", "value = parse(x) + other\n")
    ms = await sh.rg("parse", "m.py").matches()
    r = await sh.replace(ms[0].span, "scan")
    assert r.success
    content = (tmp_path / "m.py").read_text()
    assert "value = scan(x) + other" in content


async def test_replace_delete(sh, tmp_path):
    await sh.write_file("m.py", "keep1\nKILL\nkeep2\n")
    ms = await sh.rg("KILL", "m.py").matches()
    r = await sh.replace(ms[0], "")
    assert r.success
    content = (tmp_path / "m.py").read_text()
    assert "KILL" not in content and "keep1" in content and "keep2" in content


async def test_replace_path_unique_or_error(sh, tmp_path):
    await sh.write_file("u.py", "a\nUNIQUE\nb\n")
    r = await sh.replace("u.py", "UNIQUE", "DONE")
    assert r.success and "DONE" in (tmp_path / "u.py").read_text()

    await sh.write_file("d.py", "z\nz\nz\n")
    r = await sh.replace("d.py", "z", "q")
    assert not r.success and "3 places" in r.error

    r = await sh.replace("u.py", "NOPE", "x")
    assert not r.success and "not found" in r.error


async def test_replace_path_requires_new(sh):
    await sh.write_file("u.py", "a\n")
    r = await sh.replace("u.py", "x")
    assert not r.success and "replace(path, old, new)" in r.error


async def test_replace_match_rejects_third_arg(sh):
    await sh.write_file("m.py", "x\nTARGET\ny\n")
    ms = await sh.rg("TARGET", "m.py").matches()
    r = await sh.replace(ms[0], "a", "b")
    assert not r.success and "one replacement arg" in r.error


async def test_match_context_widening(sh):
    await sh.write_file("c.py", "L1\nL2\nTARGET\nL4\nL5\n")
    ms = await sh.rg("TARGET", "c.py").matches()
    wide = ms[0].context(before=2, after=1)
    assert wide.line_range == (1, 4)
    assert "L1" in wide.text and "L4" in wide.text


async def test_lines_locator_returns_match(sh):
    await sh.write_file("f.py", "\n".join(f"line{i}" for i in range(1, 21)) + "\n")
    region = await sh.lines("f.py", 5, 7)
    from nemo_oo_agents.tools._match import Match

    assert isinstance(region, Match)
    assert region.line == 5 and region.end_line == 7
    assert region.text == "line5\nline6\nline7"
    assert region.numbered.startswith("5| line5")


async def test_lines_then_replace(sh, tmp_path):
    await sh.write_file("f.py", "\n".join(f"line{i}" for i in range(1, 21)) + "\n")
    region = await sh.lines("f.py", 5, 7)
    r = await sh.replace(region, "REPLACED")
    assert r.success
    content = (tmp_path / "f.py").read_text()
    assert "REPLACED" in content and "line5" not in content
    assert "line4" in content and "line8" in content


async def test_lines_then_pipe(sh):
    await sh.write_file("g.py", "\n".join(f"line{i}" for i in range(1, 21)) + "\n")
    region = await sh.lines("g.py", 5, 7)
    piped = await region.pipe().grep("6").collect()
    assert piped == ["line6"]


async def test_lines_span_replace(sh, tmp_path):
    await sh.write_file("g.py", "alpha\nbeta\ngamma\n")
    region = await sh.lines("g.py", 1, 1)
    r = await sh.replace(region.span, "FIRST")
    assert r.success
    assert (tmp_path / "g.py").read_text().startswith("FIRST")


async def test_match_line_number_alias(sh):
    await sh.write_file("f.py", "a\nTARGET\nb\n")
    ms = await sh.rg("TARGET", "f.py").matches()
    m = ms[0]
    assert m.line == 2 == m.line_no == m.line_number


async def test_read_raises_on_missing(sh):
    import pytest as _pytest

    with _pytest.raises(FileNotFoundError):
        await sh.read("nope.py")


async def test_lines_raises_on_missing(sh):
    import pytest as _pytest

    with _pytest.raises(FileNotFoundError):
        await sh.lines("nope.py", 1, 1)


async def test_region_replace_preserves_final_newline(sh, tmp_path):
    await sh.write_file("a.txt", "x\nTARGET\nz\n")
    await sh.replace(await sh.lines("a.txt", 2, 2), "NEW")
    assert (tmp_path / "a.txt").read_text() == "x\nNEW\nz\n"


async def test_region_replace_preserves_no_final_newline(sh, tmp_path):
    await sh.write_file("b.txt", "x\nTARGET")
    assert not (tmp_path / "b.txt").read_text().endswith("\n")
    await sh.replace(await sh.lines("b.txt", 2, 2), "NEW")
    assert (tmp_path / "b.txt").read_text() == "x\nNEW"


async def test_span_replace_preserves_final_newline(sh, tmp_path):
    await sh.write_file("c.txt", "alpha beta\n")
    ms = await sh.rg("beta", "c.txt").matches()
    await sh.replace(ms[0].span, "GAMMA")
    assert (tmp_path / "c.txt").read_text() == "alpha GAMMA\n"


async def test_span_replace_preserves_no_final_newline(sh, tmp_path):
    await sh.write_file("e.txt", "alpha beta")
    ms = await sh.rg("beta", "e.txt").matches()
    await sh.replace(ms[0].span, "GAMMA")
    assert (tmp_path / "e.txt").read_text() == "alpha GAMMA"


async def test_matches_rejects_context_and_files_only(sh):
    import pytest as _pytest

    await sh.write_file("f.py", "TODO a\nTODO b\n")
    with _pytest.raises(ValueError):
        await sh.rg("TODO", "f.py", context=2).matches()
    with _pytest.raises(ValueError):
        await sh.rg("TODO", "f.py", files_only=True).matches()


async def test_matches_forwards_max_count(sh):
    await sh.write_file("f.py", "TODO 1\nTODO 2\nTODO 3\n")
    ms = await sh.rg("TODO", "f.py", max_count=2).matches()
    assert len(ms) == 2


async def test_matches_memoized(sh):
    await sh.write_file("f.py", "TODO a\nTODO b\n")
    stream = sh.rg("TODO", "f.py")
    first = await stream.matches()
    second = await stream.matches()
    assert first is second  # memoized — same list object, no second rg shell
    assert len(first) == 2


async def test_match_repr_summarizes_file_lines(sh):
    """Match.__repr__ summarizes _file_lines as <N lines> so print(match)/
    print(matches) does not dump the whole file."""
    await sh.write_file("f.py", "\n".join(f"line{i}" for i in range(1, 200)) + "\n")
    ms = await sh.rg("line5", "f.py").matches()
    r = repr(ms[0])
    assert "_file_lines=<199 lines>" in r
    assert len(r) < 300  # not a full-file dump
    assert "line1\nline2" not in r  # the file contents are NOT in the repr
    # full text still reachable on demand
    assert ms[0].text and "|" not in ms[0].text
    assert "|" in ms[0].numbered
    # print(list-of-matches) is safe too — no file dump regardless of match count
    assert "_file_lines=<199 lines>" in repr(ms)
    assert "line1\nline2" not in repr(ms)


async def test_run_unawaited_fails_loudly(sh):
    coro = sh.run("echo hi")  # forgot await
    import pytest as _pytest

    with _pytest.raises(TypeError, match="did you forget"):
        _ = coro.stdout
    with _pytest.raises(TypeError):
        _ = "x" in coro
    # awaited still works
    r = await sh.run("echo hi")
    assert r.stdout.strip() == "hi" and r.strip() == "hi" and r[:2] == "hi"


async def test_read_numbers_false_is_raw(sh, tmp_path):
    await sh.write_file("f.py", "import os\ndef f():\n    return 1\n")
    numbered = await sh.read("f.py")
    assert "1| import os" in numbered and "lines 1-3" in numbered
    raw = await sh.read("f.py", numbers=False)
    assert raw == "import os\ndef f():\n    return 1"
    assert "|" not in raw and "lines" not in raw
    # raw feeds replace() cleanly (the footgun is gone)
    rep = await sh.replace("f.py", "def f():\n    return 1", "def f():\n    return 2")
    assert rep.success


async def test_read_numbers_false_window(sh, tmp_path):
    await sh.write_file("f.txt", "\n".join(f"L{i}" for i in range(1, 11)) + "\n")
    raw = await sh.read("f.txt", lines=(3, 5), numbers=False)
    assert raw == "L3\nL4\nL5"


async def test_read_window_does_not_load_whole_file(sh, tmp_path):
    await sh.write_file("f.txt", "\n".join(f"L{i}" for i in range(1, 21)) + "\n")
    v = await sh.read("f.txt", lines=(5, 7))
    assert "5| L5" in v and "lines 5-7" in v
    raw = await sh.read("f.txt", lines=(5, 7), numbers=False)
    assert raw == "L5\nL6\nL7"


async def test_match_lines_slicing(sh, tmp_path):
    await sh.write_file("f.py", "\n".join(f"L{i}" for i in range(1, 21)) + "\n")
    region = await sh.lines("f.py", 1, 20)
    sub = region.lines[5:8]  # lines 5,6,7
    assert sub.line == 5 and sub.end_line == 7 and sub.text == "L5\nL6\nL7"
    one = region.lines[10]
    assert one.line == 10 and one.text == "L10"
    r = await sh.replace(sub, "X")
    assert r.success
    content = (tmp_path / "f.py").read_text()
    assert "X" in content and "L5" not in content and "L4" in content and "L8" in content


async def test_large_file_anchor_guard(sh, tmp_path):
    import pytest as _pytest

    big = tmp_path / "big.txt"
    big.write_text(("x" * 99 + "\n") * 110000)  # ~11 MB
    with _pytest.raises(ValueError, match="too large to anchor"):
        await sh.read("big.txt")  # whole-file read -> guard
    with _pytest.raises(ValueError, match="too large to anchor"):
        await sh.lines("big.txt", 1, 5)  # locator -> guard
    # windowed read is still cheap (no whole-file load, no guard)
    w = await sh.read("big.txt", lines=(1, 2))
    assert "1| " in w


async def test_max_anchor_mb_ctor_kwarg(tmp_path):
    import pytest as _pytest

    from nemo_oo_agents.tools.shell_tools3 import ShellTools3

    big = tmp_path / "big.txt"
    big.write_text(("x" * 99 + "\n") * 60000)  # ~6 MB

    # default 10 MB: 6 MB anchors fine
    sh = ShellTools3(cwd=str(tmp_path))
    assert sh._max_anchor_bytes == 10_000_000
    assert "1| " in await sh.read("big.txt")

    # override to 5 MB: 6 MB now trips the guard
    sh2 = ShellTools3(cwd=str(tmp_path), max_anchor_mb=5)
    assert sh2._max_anchor_bytes == 5_000_000
    with _pytest.raises(ValueError, match="limit 5 MB"):
        await sh2.read("big.txt")


async def test_subregion_byte_span_multibyte(sh, tmp_path):
    """Match.lines[i].span must give byte-accurate offsets even with multibyte
    UTF-8 content before the region (byte_start used to use char lengths)."""
    await sh.write_file("u.py", "x = 'café'\ny = 'naïve'\nTARGET = 1\n")
    region = await sh.lines("u.py", 1, 3)
    sub = region.lines[3]
    raw = (tmp_path / "u.py").read_bytes()
    assert raw[sub.byte_start : sub.byte_end].decode() == sub.matched == "TARGET = 1"


async def test_lines_slicer_rejects_step_and_negatives(sh, tmp_path):
    import pytest as _pytest

    await sh.write_file("f.py", "\n".join(f"L{i}" for i in range(1, 21)) + "\n")
    region = await sh.lines("f.py", 1, 20)
    with _pytest.raises(ValueError):
        region.lines[1:9:2]
    with _pytest.raises(ValueError):
        region.lines[-3:]
    with _pytest.raises(ValueError):
        region.lines[0]


async def test_windowed_read_out_of_range_and_empty(sh, tmp_path):
    await sh.write_file("f.txt", "\n".join(f"L{i}" for i in range(1, 6)) + "\n")
    # window past EOF clamps
    v = await sh.read("f.txt", lines=(4, 99), numbers=False)
    assert v == "L4\nL5"
    # window entirely past EOF -> empty
    v2 = await sh.read("f.txt", lines=(100, 110), numbers=False)
    assert v2 == ""


async def test_subregion_past_eof_is_empty_not_indexerror(sh, tmp_path):
    """An out-of-range line region (start past EOF) yields an empty Match,
    not an IndexError in the byte-offset loop."""
    await sh.write_file("f.py", "L1\nL2\nL3\n")
    region = await sh.lines("f.py", 1, 3)
    assert region.lines[10].text == ""  # single line past EOF
    assert region.lines[10:12].text == ""  # slice past EOF
    # windowed read past EOF is already empty
    assert await sh.read("f.py", lines=(10, 12), numbers=False) == ""
