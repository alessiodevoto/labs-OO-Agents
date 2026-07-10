# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""When is long-term memory useful, and when is it *detrimental*?

Two paired-session scenarios. In each, a *setup* session establishes a
project-specific convention (which the agent records in memory), then — with
short-term context wiped — a *test* session must act on an **underspecified**
instruction. The difference between the two scenarios is whether the world stays
the same:

* ``recall`` (memory USEFUL): the convention is **stable** but no longer visible
  in the test session, and a decoy makes the naive choice wrong. Memory supplies
  the missing fact → ON should pass, OFF should fail.

* ``stale`` (memory DETRIMENTAL): the convention **changed** between sessions.
  The old fact is still in memory and gets recalled, steering the agent to the
  now-wrong action; an agent without memory instead inspects current reality and
  succeeds → ON should fail, OFF should pass.

* ``stale`` + REFERENCES (the fix): the same scenario, but the memory stores a
  *pointer* to the schema doc (``references=["file:...SCHEMA.md"]``) instead of a
  frozen copy of the value. At recall time the reference resolves LIVE against
  the CURRENT file → the memory cannot go stale → ON+refs should pass.

Run::

    uv run python examples/memory_bench/memory_effect.py                 # auto (llm if creds, else oracle)
    uv run python examples/memory_bench/memory_effect.py --solver oracle # deterministic illustration
    uv run python examples/memory_bench/memory_effect.py --solver llm --backend chroma_embedded --embedder litellm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import MemFacade, make_agent_cls  # noqa: E402
from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402
from nooa_memory import (  # noqa: E402
    MemoryConfig,
    MemoryManager,
)
from nooa_memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)

from nooa.unifiedllm import FakeLLMClient  # noqa: E402

log = logging.getLogger("memory_effect")


# ===========================================================================
# Scenario 1 — recall (memory USEFUL): stable convention + decoy
# ===========================================================================
def s1_setup_files(wd: Path) -> None:
    (wd / "config").mkdir(exist_ok=True)
    (wd / "config" / "app.json").write_text(
        json.dumps({"api_token": "DECOY-DO-NOT-USE", "tok_v2": "REAL-TOKEN-42", "test_token": "t"})
    )
    (wd / "PROJECT_NOTES.md").write_text(
        "CONVENTION: the production API token is the value under the key `tok_v2` in "
        "config/app.json. The `api_token` key is a deprecated decoy — never use it."
    )


def s1_mutate_for_test(wd: Path) -> None:
    # New session: the notes that explained the convention are gone; data remains.
    (wd / "PROJECT_NOTES.md").unlink(missing_ok=True)
    (wd / "tokens.py").unlink(missing_ok=True)


S1_SETUP_INSTR = (
    "Read PROJECT_NOTES.md in {wd}. Implement get_token() in {wd}/tokens.py that returns "
    "the production API token from config/app.json, using the key the notes specify. "
    "Then save the token-key convention to memory so future tasks get it right."
)
S1_TEST_INSTR = (
    "Implement get_token() in {wd}/tokens.py that returns the project's production API "
    "token from config/app.json."
)


def s1_setup_oracle(wd: Path, mem: MemFacade) -> None:
    (wd / "tokens.py").write_text(
        "import json\n"
        "def get_token():\n"
        f"    return json.load(open(r'{wd}/config/app.json'))['tok_v2']\n"
    )
    mem.remember(
        "Production API token = config/app.json key 'tok_v2' (NOT 'api_token', a decoy).",
        type="skill",
    )


def s1_test_oracle(wd: Path, mem: MemFacade) -> None:
    recalled = mem.recall("which json key holds the production api token")
    # Trust memory if it supplies the convention; otherwise take the naive choice.
    key = "tok_v2" if any("tok_v2" in r for r in recalled) else "api_token"
    (wd / "tokens.py").write_text(
        "import json\n"
        "def get_token():\n"
        f"    return json.load(open(r'{wd}/config/app.json'))['{key}']\n"
    )


def s1_verify(wd: Path) -> tuple[bool, str]:
    try:
        import importlib.util
        import uuid

        spec = importlib.util.spec_from_file_location(f"tok_{uuid.uuid4().hex}", wd / "tokens.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        val = mod.get_token()
        return (val == "REAL-TOKEN-42", f"get_token()={val!r}")
    except Exception as e:  # noqa: BLE001
        return False, f"raised {e!r}"


# ===========================================================================
# Scenario 2 — stale (memory DETRIMENTAL): convention changes between sessions
# ===========================================================================
def s2_setup_files(wd: Path) -> None:
    (wd / "SCHEMA.md").write_text(
        "Config format v1: write settings to settings.json using the field `retry_count`."
    )


def s2_mutate_for_test(wd: Path) -> None:
    # The world moved on: schema v2 renamed the field. Old memory is now wrong.
    (wd / "SCHEMA.md").write_text(
        "Config format v2 (CURRENT): the retry field is now `max_retries`. "
        "The old `retry_count` field is REJECTED by the validator."
    )
    (wd / "conf.py").unlink(missing_ok=True)
    (wd / "settings.json").unlink(missing_ok=True)


S2_SETUP_INSTR = (
    "Read SCHEMA.md in {wd}. Implement apply_config() in {wd}/conf.py that writes the retry "
    "setting (value 3) to {wd}/settings.json using the field name the schema specifies, and "
    "call it. Then remember the config field-name convention for future tasks."
)
S2_TEST_INSTR = (
    "Set the retry setting to 5 in {wd}/settings.json (write it via apply_config() in "
    "{wd}/conf.py and run it)."
)


def _schema_field(wd: Path) -> str:
    txt = (wd / "SCHEMA.md").read_text() if (wd / "SCHEMA.md").exists() else ""
    m = re.search(r"`(max_retries|retry_count)`", txt)
    return m.group(1) if m else "retry_count"


def s2_setup_oracle(wd: Path, mem: MemFacade) -> None:
    field = _schema_field(wd)  # v1 -> retry_count
    (wd / "settings.json").write_text(json.dumps({field: 3}))
    mem.remember(f"settings.json uses the retry field name `{field}`.", type="skill")


def s2_test_oracle(wd: Path, mem: MemFacade) -> None:
    recalled = mem.recall("settings.json retry field name")
    # Trust memory if present (stale!); else inspect the CURRENT schema.
    if any("retry_count" in r for r in recalled):
        field = "retry_count"  # stale memory wins -> wrong
    else:
        field = _schema_field(wd)  # reads current v2 -> max_retries
    (wd / "settings.json").write_text(json.dumps({field: 5}))


S2_SETUP_INSTR_REF = (
    "Read SCHEMA.md in {wd}. Implement apply_config() in {wd}/conf.py that writes the retry "
    "setting (value 3) to {wd}/settings.json using the field name the schema specifies, and "
    "call it. Then remember the convention BY REFERENCE — "
    'self.remember("the retry field name is defined by the schema doc", type="skill", '
    'references=["file:{ref_path}"]) — so the memory follows the file instead of freezing '
    "today's value."
)
S2_TEST_INSTR_REF = (
    "Set the retry setting to 5 in {wd}/settings.json (write it via apply_config() in "
    "{wd}/conf.py and run it). Recall your conventions first; referenced values in recalled "
    "memories are re-read fresh — trust them over the memory text."
)


def s2_setup_oracle_ref(wd: Path, mem: MemFacade) -> None:
    field = _schema_field(wd)  # v1 -> retry_count
    (wd / "settings.json").write_text(json.dumps({field: 3}))
    # Store a POINTER to the schema doc, not a copy of today's field name.
    mem.remember(
        "The retry field name for settings.json is whatever the schema doc currently says.",
        type="skill",
        references=[f"file:{wd.name}/SCHEMA.md"],
    )


def s2_test_oracle_ref(wd: Path, mem: MemFacade) -> None:
    rendered = "\n".join(mem.recall_rendered("settings.json retry field name"))
    # The reference resolves LIVE against the current schema -> v2 field.
    field = "max_retries" if "max_retries" in rendered else "retry_count"
    (wd / "settings.json").write_text(json.dumps({field: 5}))


def s2_verify(wd: Path) -> tuple[bool, str]:
    try:
        data = json.loads((wd / "settings.json").read_text())
    except Exception as e:  # noqa: BLE001
        return False, f"no valid settings.json ({e!r})"
    if "retry_count" in data:
        return False, f"used stale v1 field retry_count={data['retry_count']}"
    return (data.get("max_retries") == 5, f"settings.json={data}")


SCENARIOS = {
    "recall": {
        "effect": "useful",
        "setup_files": s1_setup_files,
        "mutate": s1_mutate_for_test,
        "setup_instr": S1_SETUP_INSTR,
        "test_instr": S1_TEST_INSTR,
        "setup_oracle": s1_setup_oracle,
        "test_oracle": s1_test_oracle,
        "verify": s1_verify,
        "expect": "ON passes, OFF fails",
    },
    "stale": {
        "effect": "detrimental",
        "setup_files": s2_setup_files,
        "mutate": s2_mutate_for_test,
        "setup_instr": S2_SETUP_INSTR,
        "test_instr": S2_TEST_INSTR,
        "setup_oracle": s2_setup_oracle,
        "test_oracle": s2_test_oracle,
        "setup_oracle_ref": s2_setup_oracle_ref,
        "test_oracle_ref": s2_test_oracle_ref,
        "setup_instr_ref": S2_SETUP_INSTR_REF,
        "test_instr_ref": S2_TEST_INSTR_REF,
        "verify": s2_verify,
        "expect": "ON fails, OFF passes, ON+refs passes",
    },
}


async def run_scenario(
    name: str,
    *,
    solver: str,
    memory_on: bool,
    backend: str,
    embed: str,
    use_references: bool = False,
) -> bool:
    sc = SCENARIOS[name]
    if use_references:
        # file: references resolve relative to the working dir (containment),
        # so the by-reference arm keeps its files under cwd.
        wd = Path(tempfile.mkdtemp(prefix=f"memeffect_{name}_", dir=".")).resolve()
    else:
        wd = Path(tempfile.mkdtemp(prefix=f"memeffect_{name}_"))
    llm = build_llm() if solver == "llm" else FakeLLMClient()
    agent = make_agent_cls(llm, with_memory=memory_on and solver == "llm")()
    manager: MemoryManager | None = None
    if memory_on:
        manager = MemoryManager.install(
            agent,
            config=MemoryConfig(
                enabled=True,
                path=":memory:",
                vector=VectorConfig(backend=backend),
                embedding=build_embedding_config(embed),
                retrieval=RetrievalConfig(hops=1),
                spontaneous=SpontaneousConfig(query_strategies=("last_message", "recent_events")),
                reflection=ReflectionPolicy(only_top_level=True),
            ),
        )

    sc["setup_files"](wd)
    # --- setup session ---
    if solver == "oracle":
        setup = sc["setup_oracle_ref"] if use_references else sc["setup_oracle"]
        setup(wd, MemFacade(manager))
    else:
        instr = sc["setup_instr_ref"] if use_references else sc["setup_instr"]
        await agent.solve(instr.format(wd=wd, ref_path=f"{wd.name}/SCHEMA.md"), str(wd))

    # --- new session: wipe short-term context; mutate the world ---
    agent.event_manager.clear()
    sc["mutate"](wd)

    # --- test session ---
    if solver == "oracle":
        test = sc["test_oracle_ref"] if use_references else sc["test_oracle"]
        test(wd, MemFacade(manager))
    else:
        try:
            instr = sc["test_instr_ref"] if use_references else sc["test_instr"]
            await agent.solve(instr.format(wd=wd), str(wd))
        except Exception as e:  # noqa: BLE001
            log.warning("%s test solve error: %r", name, e)

    ok, detail = sc["verify"](wd)
    if manager is not None:
        log.info("[%s ON] memory: %s", name, manager.memory_stats().summary())
        manager.uninstall()
    arm = ("ON+refs" if use_references else "ON") if memory_on else "OFF"
    log.info("[%s %s] %s (%s)", name, arm, "PASS" if ok else "FAIL", detail)
    if use_references:
        shutil.rmtree(wd, ignore_errors=True)  # cwd-based tmp dir: always clean up
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--solver", choices=["oracle", "llm", "auto"], default="auto")
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.verbose:
        logging.getLogger("nooa.memory").setLevel(logging.DEBUG)

    solver = args.solver
    if solver == "auto":
        solver = "llm" if has_llm_creds() else "oracle"
    print(f"solver={solver}  backend={args.backend}  embedder={args.embedder}\n")

    async def go() -> dict:
        out = {}
        for name in ("recall", "stale"):
            on = await run_scenario(
                name, solver=solver, memory_on=True, backend=args.backend, embed=args.embedder
            )
            off = await run_scenario(
                name, solver=solver, memory_on=False, backend=args.backend, embed=args.embedder
            )
            ref = None
            if "setup_oracle_ref" in SCENARIOS[name]:
                ref = await run_scenario(
                    name,
                    solver=solver,
                    memory_on=True,
                    backend=args.backend,
                    embed=args.embedder,
                    use_references=True,
                )
            out[name] = (on, off, ref)
        return out

    res = asyncio.run(go())

    print("\n" + "=" * 72)
    print("MEMORY: USEFUL vs DETRIMENTAL vs BY-REFERENCE")
    print("=" * 72)
    print(
        f"  {'scenario':10s} {'intended effect':14s} {'mem ON':8s} {'mem OFF':8s} "
        f"{'ON+refs':8s} verdict"
    )
    for name, (on, off, ref) in res.items():
        effect = SCENARIOS[name]["effect"]
        if effect == "useful":
            verdict = "memory HELPED" if (on and not off) else "no clear effect"
        else:
            verdict = "memory HURT" if (off and not on) else "no clear effect"
            if ref:
                verdict += "; references FIXED it"
        ref_s = "-" if ref is None else ("PASS" if ref else "FAIL")
        print(
            f"  {name:10s} {effect:14s} {'PASS' if on else 'FAIL':8s} "
            f"{'PASS' if off else 'FAIL':8s} {ref_s:8s} {verdict}"
        )
    print("=" * 72)
    print("recall: a stable convention only memory still holds → memory helps.")
    print("stale : a convention that changed → recalled-but-outdated memory misleads.")
    print("refs  : the same memory stored as a POINTER resolves live → cannot go stale.")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
