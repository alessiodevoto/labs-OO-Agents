# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A long-horizon task suite, modelled on LongCLI-Bench.

One *project* (a small ``KVStore`` library) is built up over a sequence of
dependent tasks spanning LongCLI-Bench's four categories — ``from_scratch``,
``feature_add``, ``bug_fix``, ``refactor``. Each task carries:

* an **instruction** (what the agent is told to do),
* a **fail->pass** check (``f2p``) — the new requirement, expected to fail before
  the task and pass after,
* the **regression** set (``p2p``) — all earlier tasks' checks, which must keep
  passing,
* an **oracle** that writes the known-good solution and records the conventions a
  competent agent would remember (used by the offline smoke solver).

The long-horizon signal: conventions established early (the data file name, the
storage format, the public API) must be recalled to do later tasks correctly —
exactly what the memory subsystem is meant to carry across "sessions".
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MODULE = "kvstore.py"

# The conventions a competent agent would note early and reuse later. The oracle
# writes these to memory; the LLM agent is expected to recall them.
CONVENTIONS = [
    "The KVStore class lives in kvstore.py and persists to the JSON file path passed to its constructor.",
    "On-disk format is a single flat JSON object mapping string keys to values.",
    "Public API: KVStore(path), .set(k, v), .get(k), .delete(k), .keys(), len(store).",
]

# Incrementally-built solutions (each stage is the full, correct file at that point).
_V1 = """\
import json, os

class KVStore:
    def __init__(self, path):
        self.path = path
        self._data = {}
        if os.path.exists(path):
            with open(path) as f:
                self._data = json.load(f)

    def _flush(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f)

    def set(self, k, v):
        self._data[k] = v
        self._flush()

    def get(self, k):
        return self._data[k]          # BUG (fixed in T3): raises KeyError when missing

    def delete(self, k):
        del self._data[k]             # BUG (fixed in T3): raises KeyError when missing
        self._flush()
"""

_V2 = _V1.replace(
    "    def delete(self, k):\n        del self._data[k]             # BUG (fixed in T3): raises KeyError when missing\n        self._flush()\n",
    "    def delete(self, k):\n        del self._data[k]             # BUG (fixed in T3): raises KeyError when missing\n"
    "        self._flush()\n\n"
    "    def keys(self):\n        return sorted(self._data.keys())\n\n"
    "    def __len__(self):\n        return len(self._data)\n",
)

_V3 = _V2.replace(
    "        return self._data[k]          # BUG (fixed in T3): raises KeyError when missing",
    "        return self._data.get(k)",
).replace(
    "        del self._data[k]             # BUG (fixed in T3): raises KeyError when missing\n        self._flush()",
    "        self._data.pop(k, None)\n        self._flush()",
)

_V4 = _V3.replace(
    '    def _flush(self):\n        with open(self.path, "w") as f:\n            json.dump(self._data, f)\n',
    "    def _flush(self):\n"
    "        # atomic write: never leave a half-written store on crash\n"
    "        d = os.path.dirname(self.path) or '.'\n"
    "        fd, tmp = tempfile.mkstemp(dir=d)\n"
    "        with os.fdopen(fd, 'w') as f:\n"
    "            json.dump(self._data, f)\n"
    "        os.replace(tmp, self.path)\n",
).replace("import json, os", "import json, os, tempfile")


def _load(workdir: Path):
    """Import the workdir's kvstore.py fresh (no sys.modules caching)."""
    path = workdir / MODULE
    spec = importlib.util.spec_from_file_location(f"kv_{uuid.uuid4().hex}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# --- verification checks (return (passed, detail)) ---
def _check_roundtrip(workdir: Path) -> tuple[bool, str]:
    try:
        mod = _load(workdir)
        kv = workdir / "data1.json"
        s = mod.KVStore(str(kv))
        s.set("a", 1)
        s.set("b", 2)
        if s.get("a") != 1:
            return False, "get('a') != 1"
        # persistence across instances
        s2 = mod.KVStore(str(kv))
        if s2.get("b") != 2:
            return False, "did not persist across instances"
        return True, "roundtrip + persistence ok"
    except Exception as e:  # noqa: BLE001
        return False, f"raised {e!r}"


def _check_keys_and_len(workdir: Path) -> tuple[bool, str]:
    try:
        mod = _load(workdir)
        s = mod.KVStore(str(workdir / "data2.json"))
        s.set("z", 1)
        s.set("a", 2)
        if s.keys() != ["a", "z"]:
            return False, f"keys() not sorted: {s.keys()}"
        if len(s) != 2:
            return False, f"len != 2: {len(s)}"
        return True, "keys()+len ok"
    except Exception as e:  # noqa: BLE001
        return False, f"raised {e!r}"


def _check_missing_key_safe(workdir: Path) -> tuple[bool, str]:
    try:
        mod = _load(workdir)
        s = mod.KVStore(str(workdir / "data3.json"))
        if s.get("nope") is not None:
            return False, "get(missing) should be None"
        s.delete("nope")  # must not raise
        return True, "missing-key handling ok"
    except Exception as e:  # noqa: BLE001
        return False, f"raised {e!r}"


def _check_atomic_still_works(workdir: Path) -> tuple[bool, str]:
    # Refactor must preserve behaviour: pure regression-style check.
    return _check_roundtrip(workdir)


def _oracle(version: str, remembers: list[str]) -> Callable:
    def run(workdir: Path, mem) -> None:
        # A competent agent recalls earlier conventions before editing...
        mem.recall("KVStore data file format and public API")
        (workdir / MODULE).write_text(version)
        # ...and records what it established for next time.
        for fact in remembers:
            mem.remember(fact, type="skill")

    return run


@dataclass
class Task:
    name: str
    category: str
    instruction: str
    oracle: Callable
    f2p: Callable[[Path], tuple[bool, str]]
    # filled in by build_suite: the cumulative regression checks (all prior f2p)
    p2p: list[Callable[[Path], tuple[bool, str]]] = field(default_factory=list)


def build_suite() -> list[Task]:
    tasks = [
        Task(
            name="t1_create_store",
            category="from_scratch",
            instruction=(
                "Create a file `kvstore.py` defining a class `KVStore`. The constructor "
                "takes a path to a JSON file used for persistence. Implement `.set(k, v)`, "
                "`.get(k)`, and `.delete(k)`. Data must survive across instances."
            ),
            oracle=_oracle(_V1, CONVENTIONS[:2]),
            f2p=_check_roundtrip,
        ),
        Task(
            name="t2_add_keys_len",
            category="feature_add",
            instruction=(
                "Add to `KVStore` a `.keys()` method returning the keys sorted, and make "
                "`len(store)` return the number of entries. Keep the existing API and "
                "on-disk format unchanged."
            ),
            oracle=_oracle(_V2, [CONVENTIONS[2]]),
            f2p=_check_keys_and_len,
        ),
        Task(
            name="t3_fix_missing_key",
            category="bug_fix",
            instruction=(
                "Bug: `KVStore.get()` raises KeyError for missing keys and `.delete()` "
                "raises KeyError when the key is absent. Fix both: `.get()` should return "
                "None for a missing key and `.delete()` should be a no-op."
            ),
            oracle=_oracle(
                _V3, ["get() returns None for missing keys; delete() is a no-op if absent."]
            ),
            f2p=_check_missing_key_safe,
        ),
        Task(
            name="t4_atomic_write",
            category="refactor",
            instruction=(
                "Refactor persistence to write atomically (write to a temp file then "
                "os.replace) so a crash never corrupts the store. The public API and "
                "on-disk format must stay exactly the same."
            ),
            oracle=_oracle(_V4, ["KVStore persists with an atomic temp-file + os.replace write."]),
            f2p=_check_atomic_still_works,
        ),
    ]
    # Regression set = all earlier tasks' f2p checks.
    prior: list[Callable] = []
    for t in tasks:
        t.p2p = list(prior)
        prior.append(t.f2p)
    return tasks
