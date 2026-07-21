# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Red-team scanner: did any GAME SOURCE content leak into the agent's memory/messages?

The real game implementation lives at
``examples/arc_agi_3/environment_files/<game>/<hash>/<game>.py`` (downloaded by the
SDK; gitignored) and must NEVER reach the agent — the agent is supposed to infer
the game purely by playing. Each game's source is independently obfuscated: its
type names are random 8+ char lowercase tokens (e.g. ``akikkfevfqs``) that are
**per-game unique** (they appear in exactly one game's source) and cannot occur
in the hex grid, English prose, or the ``arcengine`` SDK surface. They are ideal
canaries: if such a token appears in a game's agent data, a source fragment
leaked — regardless of vector (traceback, repr, render text, an agent read, …).

Because the sources are gitignored (downloaded on demand), the canary **ban
list** — 3 unique tokens per game — is committed alongside this scanner in
``game_source_canaries.json`` so the scan runs with no sources present. Refresh
it after a game-set change with ``--regen`` (needs environment_files locally).

Per game we grep every game's canaries across that game's ENTIRE memory + message
tree (agent_logs, memory store, team_nemo world-models, steps, gameplay) —
following symlinks, so a live run whose logs are symlinked into /tmp is covered.
A hit is classified:
  * OWN   — game X's token found in game X's data  (the agent saw its own source)
  * CROSS — game X's token found in game Y's data  (cross-game source leak)

Run:   RT_RUN_ROOT=results/arc_agi_3/nemo_solver/<run> python3 scan_source_leak.py
Regen: python3 scan_source_leak.py --regen        # rebuild the committed ban list
Writes: <RT_OUT or run/red_team/evidence>/source_leak.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BANLIST = HERE / "game_source_canaries.json"
# Candidate identifiers for --regen: 8+ pure-lowercase tokens; cross-game
# uniqueness then strips shared English/SDK vocab, leaving obfuscated per-game names.
_TOKEN = re.compile(r"\b[a-z]{8,}\b")
N_PER_GAME = 3


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_banlist() -> dict[str, list[str]]:
    return json.loads(BANLIST.read_text())["canaries"]


def regen_banlist() -> dict[str, list[str]]:
    """Rebuild the ban list from local sources (examples/arc_agi_3/environment_files)."""
    envfiles = _repo_root() / "examples" / "arc_agi_3" / "environment_files"
    srcs = sorted(envfiles.glob("*/*/*.py"))
    if not srcs:
        sys.stderr.write(f"error: no game sources under {envfiles} — download them first\n")
        raise SystemExit(2)
    per: dict[str, Counter] = {}
    for src in srcs:
        game = src.parent.parent.name
        per[game] = Counter(_TOKEN.findall(src.read_text(errors="ignore")))
    doc_freq: Counter = Counter()
    for toks in per.values():
        doc_freq.update(toks.keys())
    banlist: dict[str, list[str]] = {}
    for g in sorted(per):
        uniq = [t for t in per[g] if doc_freq[t] == 1 and len(t) >= 8]
        uniq.sort(key=lambda t: (-per[g][t], -len(t), t))
        banlist[g] = uniq[:N_PER_GAME]
    payload = {
        "_comment": (
            "Per-game canary tokens: obfuscated identifiers UNIQUE to each ARC-AGI-3 "
            "game source (appear in exactly one game). Used by scan_source_leak.py to "
            "detect game-source content leaking into agent memory/messages. 3 per game "
            "where available; some games use short obfuscation and have fewer."
        ),
        "canaries": banlist,
    }
    BANLIST.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sparse = [g for g, v in banlist.items() if len(v) < N_PER_GAME]
    print(
        f"regenerated {BANLIST.name}: {len(banlist)} games, {sum(len(v) for v in banlist.values())} tokens"
    )
    if sparse:
        print(f"  sparse (<{N_PER_GAME}, short obfuscation): {', '.join(sparse)}")
    return banlist


def grep_tree_multi(tokens: list[str], root: Path) -> list[tuple[str, str]]:
    """(file, token) hits for ANY token under *root*, symlinks followed.

    One find + one grep per tree: -L follows symlinks (live-run agent_logs are
    symlinked into /tmp), grep -F fixed-strings (Aho-Corasick over all tokens),
    -o emits the matched token, -H the filename, -I skips binaries.
    """
    files = [
        f
        for f in subprocess.run(
            ["find", "-L", str(root), "-type", "f"], capture_output=True, text=True
        ).stdout.split("\n")
        if f.strip()
    ]
    if not files:
        return []
    tokenset = set(tokens)  # filter -o hits against the tokens actually searched
    args = ["grep", "-FHoI"]
    for t in tokens:
        args += ["-e", t]
    hits: list[tuple[str, str]] = []
    for i in range(0, len(files), 2000):  # chunk to stay under ARG_MAX
        r = subprocess.run(args + ["--", *files[i : i + 2000]], capture_output=True, text=True)
        for ln in r.stdout.split("\n"):
            if not ln.strip():
                continue
            path, _, tok = ln.rpartition(":")
            if tok in tokenset:
                hits.append((path, tok))
    return hits


def main() -> int:
    if "--regen" in sys.argv[1:]:
        regen_banlist()
        return 0

    # Imported lazily so --regen works without a run to audit.
    from rt_common import RUN_ROOT
    from rt_common import _variant_dir as _vdir

    skip = {"red_team", "analysis"}
    canaries = load_banlist()
    token_owner: dict[str, str] = {}
    for g, names in canaries.items():
        for n in names:
            token_owner[n] = g
    all_tokens = list(token_owner)
    sparse = [g for g, v in canaries.items() if len(v) < N_PER_GAME]

    games = [
        d.name
        for d in sorted(RUN_ROOT.iterdir())
        if d.is_dir() and d.name not in skip and _vdir(d) is not None
    ]
    own_leaks, cross_leaks, scanned = [], [], []
    for g in games:
        vdir = _vdir(RUN_ROOT / g)
        run = next(iter(vdir.glob("2*")), None) if vdir else None
        if run is None:
            continue
        scanned.append(g)
        # One grep of EVERY game's canaries against THIS game's data. Games with
        # no own-canary are still scanned so a cross-game leak into them is caught.
        for f, token in grep_tree_multi(all_tokens, run):
            owner = token_owner[token]
            rel = f.replace(str(RUN_ROOT) + "/", "")
            entry = {"context_game": g, "token": token, "owner": owner, "file": rel}
            (own_leaks if owner == g else cross_leaks).append(entry)

    from rt_common import EVID

    result = {
        "run": str(RUN_ROOT),
        "games_scanned": len(scanned),
        "canaries_per_game_target": N_PER_GAME,
        "total_canary_tokens": len(all_tokens),
        "games_sparse_canaries": sparse,
        "own_source_leaks": own_leaks,
        "cross_game_source_leaks": cross_leaks,
        "verdict": "LEAK" if (own_leaks or cross_leaks) else "CLEAN",
    }
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / "source_leak.json").write_text(json.dumps(result, indent=2))

    print(f"scan_source_leak: {len(scanned)} games scanned, {len(all_tokens)} canary tokens")
    print(f"  OWN source leaks:   {len(own_leaks)}")
    print(f"  CROSS source leaks: {len(cross_leaks)}")
    if sparse:
        print(f"  sparse canaries (<{N_PER_GAME}, short obfuscation): {', '.join(sparse)}")
    print(f"  VERDICT: {result['verdict']}")
    for e in (own_leaks + cross_leaks)[:20]:
        print(f"    {e['owner']}::{e['token']} -> found in {e['context_game']} @ {e['file']}")
    print(f"  evidence -> {EVID / 'source_leak.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
