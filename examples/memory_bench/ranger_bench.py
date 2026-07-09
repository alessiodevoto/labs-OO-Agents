# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trail Ranger — an EBR-style continual-learning benchmark (original content).

Epoch AI's EBR-Bench (https://epoch.ai/publications/earthborne-rangers-benchmark)
tests whether agents *learn from experience*: repeated playthroughs of a campaign
game where notes are the only thing that persists, scored on the final 20%. The
benchmark itself is closed (no public harness; copyrighted game content), so this
is an original mini campaign game that adopts the PROTOCOL, not the game:

* N playthroughs of a 5-day wilderness expedition (deterministic engine, no RNG
  at play time); short-term context is wiped between playthroughs — ONLY the
  long-term memory subsystem persists.
* Before each playthrough the player picks a **loadout** (4 archetypes) — the
  cross-playthrough exploration decision.
* The map hides **gotchas** (river needs rope, caves need a map, day-3 storm on
  the ridge, barren forage spot, a scoutable shortcut) — the learnable knowledge.
* Score = objectives completed (0..12); the headline is the mean of the FINAL 2
  playthroughs (the learning phase is free, like EBR's final-20% rule).

Arms:
* OFF    — no memory: every playthrough starts ignorant (learns within a
           playthrough, forgets between).
* ON     — the player writes discovered gotchas (skills) and loadout outcomes
           (episodes) to memory, and reads them at the start of each playthrough.
* GUIDE  — memory pre-seeded with the full strategy (EBR's "max elicitation"
           oracle guide): the ceiling that bounds what learning can add.

Metrics: score trajectory, final-2 mean, loadout coverage (did it remember what
it tried?), and **repeated mistakes** (a gotcha suffered while already known —
must be 0 for ON from playthrough 2 on).

Run::

    uv run python examples/memory_bench/ranger_bench.py                  # oracle policy, all arms
    uv run python examples/memory_bench/ranger_bench.py --playthroughs 6
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field

from nooa_tui.memory import MemoryConfig, MemoryManager, MemoryType
from nooa_tui.memory.config import VectorConfig

from nooa import Agent
from nooa.unifiedllm import FakeLLMClient

log = logging.getLogger("ranger_bench")

# ===========================================================================
# The game engine (deterministic; the "hidden rulebook" is the GOTCHAS dict)
# ===========================================================================
EDGES: dict[tuple[str, str], int] = {
    ("base", "meadow"): 2,
    ("meadow", "river"): 2,
    ("river", "forest"): 2,
    ("base", "forest"): 5,  # the long way around (no river crossing)
    ("forest", "ridge"): 3,
    ("ridge", "summit"): 3,
    ("ridge", "caves"): 2,
    ("forest", "lake"): 6,  # scouting the forest reveals a shortcut (cost 2)
}
FORAGE_YIELD = {"meadow": "berries", "forest": "mushrooms", "lake": "fish"}  # ridge: barren

LOADOUTS: dict[str, frozenset[str]] = {
    "minimal": frozenset(),
    "map_food": frozenset({"map"}),
    "food_rope": frozenset({"rope"}),
    "rope_map": frozenset({"rope", "map"}),
}

# gotcha key -> the memory text the player writes when it learns the rule
GOTCHAS = {
    "river_needs_rope": "Gotcha: crossing the river without a rope sweeps you back and wastes 4 energy.",
    "caves_need_map": "Gotcha: entering the caves without a map ends the day lost.",
    "ridge_forage_barren": "Gotcha: foraging on the ridge yields nothing (wasted energy).",
    "day3_ridge_storm": "Gotcha: on day 3 a storm doubles the cost of hiking onto the ridge.",
    "forest_shortcut": "Gotcha: scouting at the forest reveals a lake shortcut (cost 2 instead of 6).",
    "best_loadout": "Strategy: the rope_map loadout avoids both the river and caves gotchas.",
}

OBJECTIVES = (
    "visit_meadow",
    "visit_river",
    "visit_forest",
    "visit_ridge",
    "visit_caves",
    "visit_summit",
    "forage_berries",
    "forage_mushrooms",
    "forage_fish",
    "scout_shortcut",
    "summit_by_day4",
    "end_at_base",
)


@dataclass
class Playthrough:
    """One 5-day expedition; deterministic given the action sequence."""

    items: frozenset[str]
    day: int = 1
    energy: int = 10
    loc: str = "base"
    shortcut_open: bool = False
    done: set[str] = field(default_factory=set)
    suffered: list[str] = field(default_factory=list)  # gotcha keys hit this run

    def _edge_cost(self, a: str, b: str) -> int | None:
        cost = EDGES.get((a, b)) or EDGES.get((b, a))
        if cost is None:
            return None
        if {a, b} == {"forest", "lake"} and self.shortcut_open:
            cost = 2
        if self.day == 3 and b == "ridge":
            cost *= 2  # day-3 storm (gotcha)
        return cost

    def _spend(self, cost: int) -> bool:
        if self.day > 5 or self.energy < cost:
            return False
        self.energy -= cost
        return True

    def hike(self, dest: str) -> bool:
        cost = self._edge_cost(self.loc, dest)
        if cost is None or self.day > 5:
            return False
        if self.day == 3 and dest == "ridge":
            self.suffered.append("day3_ridge_storm")
        if {self.loc, dest} == {"meadow", "river"} and "rope" not in self.items:
            # swept back: the crossing fails and burns energy
            self.suffered.append("river_needs_rope")
            self.energy = max(0, self.energy - 4)
            return False
        if dest == "caves" and "map" not in self.items:
            self.suffered.append("caves_need_map")
            self.camp()  # lost: the day ends
            return False
        if not self._spend(cost):
            return False
        self.loc = dest
        self.done.add(f"visit_{dest}")
        if dest == "summit" and self.day <= 4:
            self.done.add("summit_by_day4")
        return True

    def forage(self) -> bool:
        if not self._spend(2):
            return False
        item = FORAGE_YIELD.get(self.loc)
        if item is None:
            self.suffered.append("ridge_forage_barren")
            return False
        self.done.add(f"forage_{item}")
        return True

    def scout(self) -> bool:
        if not self._spend(1):
            return False
        if self.loc == "forest":
            self.shortcut_open = True
            self.done.add("scout_shortcut")
        return True

    def camp(self) -> None:
        self.day += 1
        self.energy = 10

    def score(self) -> int:
        final = set(self.done)
        if self.day > 5 or (self.loc == "base" and self.day >= 4):
            if self.loc == "base":
                final.add("end_at_base")
        return len(final & set(OBJECTIVES))


# ===========================================================================
# The player policy — shared by all arms; only its KNOWLEDGE differs
# ===========================================================================
_LOADOUT_ORDER = ("minimal", "map_food", "food_rope", "rope_map")


def choose_loadout(knowledge: set[str], tried: list[str]) -> str:
    if "best_loadout" in knowledge:
        return "rope_map"
    for name in _LOADOUT_ORDER:
        if name not in tried:
            return name  # exploration: try something new
    return tried[-1]


def play(knowledge: set[str], loadout: str) -> Playthrough:
    """A greedy expedition informed by what the player KNOWS going in.

    Within a playthrough the player also learns from what it suffers (all
    arms); the arms differ only in whether that knowledge survives to the
    next playthrough.
    """
    pt = Playthrough(items=LOADOUTS[loadout])
    know = set(knowledge)

    def hike_learn(dest: str) -> bool:
        ok = pt.hike(dest)
        know.update(pt.suffered)
        return ok

    # Day 1: meadow (forage) then toward the forest.
    hike_learn("meadow")
    pt.forage()
    river_blocked = "river_needs_rope" in know and "rope" not in pt.items
    if not river_blocked:
        river_blocked = not hike_learn("river")
    if not river_blocked:
        hike_learn("forest")
    pt.camp()

    # Day 2: reach the forest (long way if the river is out), scout + forage.
    if pt.loc != "forest":
        if pt.loc != "base":
            hike_learn("meadow") if pt.loc == "river" else None
            # walk back toward base then take the long way
            if pt.loc == "meadow":
                hike_learn("base")
        hike_learn("forest")
    if "forest_shortcut" not in know:
        pt.scout()  # exploration: maybe something here?
        know.update({"forest_shortcut"} if pt.shortcut_open else set())
    else:
        pt.scout()  # known payoff: open the shortcut deliberately
    pt.forage()
    pt.camp()

    # Day 3: the lake via the shortcut if open; NEVER the ridge if the storm is known.
    if pt.shortcut_open or "forest_shortcut" in know:
        if hike_learn("lake"):
            pt.forage()
            hike_learn("forest")
    elif "day3_ridge_storm" not in know:
        hike_learn("ridge")  # walks into the storm (and learns)
    pt.camp()

    # Day 4: ridge -> summit (+ caves only with a map or ignorance).
    if pt.loc != "ridge":
        hike_learn("ridge")
    if pt.loc == "ridge":
        if "ridge_forage_barren" not in know:
            pt.forage()
            know.update(pt.suffered)
        if "map" in pt.items or "caves_need_map" not in know:
            if hike_learn("caves"):
                hike_learn("ridge")
        hike_learn("summit")
    pt.camp()

    # Day 5: home — respect the river gotcha on the way back too.
    if pt.loc == "summit":
        hike_learn("ridge")
    if pt.loc == "ridge":
        hike_learn("forest")
    if pt.loc == "forest":
        river_ok = "rope" in pt.items or "river_needs_rope" not in know
        if not (river_ok and hike_learn("river")):
            hike_learn("base")  # the long way home
    if pt.loc == "river":
        hike_learn("meadow")
    if pt.loc == "meadow":
        hike_learn("base")
    pt.day = 6  # expedition over
    return pt


# ===========================================================================
# The harness: N playthroughs per arm; memory is the only thing that persists
# ===========================================================================
class RangerAgent(Agent, llm=FakeLLMClient()):
    pass


def _knowledge_from_memory(manager: MemoryManager) -> set[str]:
    """Parse known gotcha keys out of recalled memory contents."""
    know: set[str] = set()
    for m in manager.recall("trail expedition gotchas strategy loadout", k=12):
        for key, text in GOTCHAS.items():
            if text in m.content or key in m.content:
                know.add(key)
    return know


def run_arm(*, arm: str, playthroughs: int, backend: str) -> dict:
    manager: MemoryManager | None = None
    if arm != "OFF":
        agent = RangerAgent()
        manager = MemoryManager.install(
            agent,
            config=MemoryConfig(
                enabled=True, path=":memory:", vector=VectorConfig(backend=backend)
            ),
        )
    if arm == "GUIDE":
        for text in GOTCHAS.values():  # the expert strategy guide, pre-seeded
            manager.remember(text, type=MemoryType.SKILL, importance=9.0)

    scores: list[int] = []
    repeated_mistakes = 0
    played: set[str] = set()  # harness-level metric only, never a policy input
    for i in range(playthroughs):
        # context wipe between playthroughs: only long-term memory persists.
        # Both knowledge AND the tried-loadout list are re-derived from memory —
        # the OFF arm genuinely starts every playthrough ignorant.
        knowledge = _knowledge_from_memory(manager) if manager is not None else set()
        tried: list[str] = []
        if manager is not None:
            for m in manager.store.all_memories():
                if m.type is MemoryType.EPISODE and "tried loadout" in m.content:
                    name = m.content.split("tried loadout ")[1].split(" ")[0]
                    if name not in tried:
                        tried.append(name)
        loadout = choose_loadout(knowledge, tried)
        played.add(loadout)

        pt = play(knowledge, loadout)
        scores.append(pt.score())
        repeated_mistakes += sum(1 for g in pt.suffered if g in knowledge)

        if arm == "ON" and manager is not None:
            for g in set(pt.suffered):
                manager.remember(GOTCHAS[g], type=MemoryType.SKILL, importance=8.0)
            if pt.shortcut_open:
                manager.remember(GOTCHAS["forest_shortcut"], type=MemoryType.SKILL)
            manager.remember(
                f"Episode: tried loadout {loadout} on playthrough {i + 1}, scored {pt.score()}/12.",
                type=MemoryType.EPISODE,
                dedup=False,
            )
            if loadout == "rope_map" and pt.score() >= max(scores[:-1] or [0]):
                manager.remember(GOTCHAS["best_loadout"], type=MemoryType.SKILL)
        log.info(
            "[%s] playthrough %d: loadout=%s score=%d/12 suffered=%s",
            arm,
            i + 1,
            loadout,
            pt.score(),
            sorted(set(pt.suffered)),
        )

    if manager is not None:
        manager.uninstall()
    final2 = scores[-2:] if len(scores) >= 2 else scores
    return {
        "scores": scores,
        "final2_mean": sum(final2) / len(final2),
        "loadouts_tried": len(played),
        "repeated_mistakes": repeated_mistakes,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--playthroughs", type=int, default=10)
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )

    results = {
        arm: run_arm(arm=arm, playthroughs=args.playthroughs, backend=args.backend)
        for arm in ("OFF", "ON", "GUIDE")
    }

    print("\n" + "=" * 76)
    print(f"TRAIL RANGER — EBR-style protocol, {args.playthroughs} playthroughs, score 0..12")
    print("=" * 76)
    print(f"  {'arm':7s} {'trajectory':<32s} {'final-2':>8s} {'loadouts':>9s} {'repeats':>8s}")
    for arm, r in results.items():
        traj = " ".join(f"{s:2d}" for s in r["scores"])
        print(
            f"  {arm:7s} {traj:<32s} {r['final2_mean']:>8.1f} "
            f"{r['loadouts_tried']:>9d} {r['repeated_mistakes']:>8d}"
        )
    print("=" * 76)
    print("final-2 = mean of the last two playthroughs (the EBR final-20% rule).")
    print("repeats = gotchas suffered while already known — MUST be 0 for ON/GUIDE.")
    print("OFF relearns every playthrough; ON converges toward GUIDE (the ceiling).")
    print("=" * 76 + "\n")

    on, off, guide = (
        results["ON"]["final2_mean"],
        results["OFF"]["final2_mean"],
        results["GUIDE"]["final2_mean"],
    )
    print(f"memory effect: ON −OFF = +{on - off:.1f}   |   headroom: GUIDE −ON = {guide - on:.1f}")
    if not (guide >= on > off):
        raise SystemExit("calibration broken: expected GUIDE >= ON > OFF")


if __name__ == "__main__":
    # keep asyncio import meaningful if an LLM arm is added later
    asyncio.get_event_loop_policy()
    main()
