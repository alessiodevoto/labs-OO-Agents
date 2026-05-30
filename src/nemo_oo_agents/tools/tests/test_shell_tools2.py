# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ShellTools2 — the simple bake-off shell."""

import pytest

from nemo_oo_agents.tools.shell_tools2 import ShellTools2


@pytest.fixture
def sh(tmp_path):
    return ShellTools2(cwd=str(tmp_path))


async def test_run_result_is_str_like(sh):
    r = await sh.run("echo hello; echo world")
    assert "hello" in r and "world" in r
    assert r[:5] == "hello"
    assert r.splitlines()[:2] == ["hello", "world"]
    assert r.success and r.returncode == 0


async def test_run_stdin_as_data_to_cat(sh):
    payload = "data\nwith 'quotes' and $VARS and `ticks`\n"
    r = await sh.run("cat", stdin=payload)
    assert "$VARS" in r and "`ticks`" in r
    assert r.success


async def test_run_stdin_as_data_to_python(sh):
    r = await sh.run("python3 -c 'import sys; print(len(sys.stdin.read()))'", stdin="x" * 100)
    assert r.strip() == "100"


async def test_write_file_no_quoting(sh, tmp_path):
    nasty = "s = \"a 'mix' of $q `and` backticks\"\n"
    w = await sh.write_file("a/b/m.py", nasty)
    assert w.created and w.success
    assert (tmp_path / "a/b/m.py").read_text() == nasty


async def test_read_gutter_and_range(sh):
    await sh.write_file("m.py", "one\ntwo\nthree\n")
    full = await sh.read("m.py")
    assert "1| one" in full and "3| three" in full
    rng = await sh.read("m.py", lines=(2, 2))
    assert "2| two" in rng and "one" not in rng


async def test_replace_unique(sh, tmp_path):
    await sh.write_file("m.py", "def f():\n    return s\n")
    r = await sh.replace("m.py", "return s", "return s.upper()")
    assert r.success
    assert "return s.upper()" in (tmp_path / "m.py").read_text()


async def test_replace_ambiguous_errors(sh):
    await sh.write_file("d.txt", "x\nx\nx\n")
    r = await sh.replace("d.txt", "x", "y")
    assert not r.success and "3 places" in r.error


async def test_replace_delete(sh, tmp_path):
    await sh.write_file("d.txt", "keep\nKILL\nkeep2\n")
    r = await sh.replace("d.txt", "KILL\n", "")
    assert r.success and "KILL" not in (tmp_path / "d.txt").read_text()


async def test_state_persists(sh):
    await sh.run("export FOO=bar")
    r = await sh.run("echo $FOO")
    assert r.strip() == "bar"
