# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skill — base class and wrapper for agent-loadable capabilities."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# SKILL.md frontmatter parsing
# ---------------------------------------------------------------------------


class _SkillProperties(BaseModel):
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str] = {}
    user_invocable: bool = False  # explicit user-invocable: true
    install_as: str | None = None  # install-as: command → user-invocable slash command
    argument_hint: str | None = None

    @property
    def is_user_command(self) -> bool:
        """True if this skill should be registered as a user-invocable slash command."""
        return self.install_as == "command" or self.user_invocable


def _find_skill_md(skill_dir: Path) -> Path | None:
    # Check for both case variants; on case-insensitive filesystems (macOS HFS+/APFS),
    # path.exists() may return True even if the actual filename differs in case.
    # We iterate through actual directory entries to get real filenames, then
    # prefer SKILL.md (uppercase) over skill.md when both exist.
    if not skill_dir.is_dir():
        return None
    matches: dict[str, Path] = {}
    for entry in skill_dir.iterdir():
        if entry.name in ("SKILL.md", "skill.md"):
            matches[entry.name] = entry
    return matches.get("SKILL.md") or matches.get("skill.md")


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse SKILL.md frontmatter compatible with Claude Code's lenient behaviour.

    Strategy: try ``yaml.safe_load`` on the whole block first — it handles
    multi-line values (e.g. ``metadata:`` sub-keys) correctly.  If the block
    contains an invalid YAML scalar (e.g. ``argument-hint: "<title>" [-p]``),
    fall back to a line-by-line regex that treats each value as a raw string,
    matching what Claude Code actually does.

    In both paths, YAML lists are coerced to strings (Claude Code v2.1.47+
    behaviour for ``argument-hint: [label]``).
    """
    if not content.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter (---)")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter not properly closed with ---")

    fm_text = parts[1]
    body = parts[2].strip()

    # --- Fast path: strict YAML ---
    try:
        meta = yaml.safe_load(fm_text) or {}
        if not isinstance(meta, dict):
            raise ValueError("SKILL.md frontmatter must be a YAML mapping")
    except yaml.YAMLError:
        # --- Fallback: line-by-line regex (Claude Code-compatible) ---
        meta = {}
        for line in fm_text.splitlines():
            m = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.+)$", line)
            if not m:
                continue
            key, raw = m.group(1), m.group(2).strip()
            try:
                parsed = yaml.safe_load(raw)
                meta[key] = str(parsed) if isinstance(parsed, list) else parsed
            except yaml.YAMLError:
                meta[key] = raw  # invalid scalar — keep as raw string

    # Coerce any top-level list values to strings (e.g. argument-hint: [label])
    for key, val in meta.items():
        if isinstance(val, list):
            meta[key] = str(val)

    if "metadata" in meta and isinstance(meta["metadata"], dict):
        meta["metadata"] = {str(k): str(v) for k, v in meta["metadata"].items()}
    return meta, body


def _read_skill_properties(skill_dir: Path) -> _SkillProperties:
    skill_md = _find_skill_md(skill_dir)
    if skill_md is None:
        raise ValueError(f"SKILL.md not found in {skill_dir}")
    meta, _ = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if "name" not in meta:
        raise ValueError("Missing required frontmatter field: name")
    if "description" not in meta:
        raise ValueError("Missing required frontmatter field: description")
    return _SkillProperties(
        name=str(meta["name"]).strip(),
        description=str(meta["description"]).strip(),
        license=meta.get("license"),
        compatibility=meta.get("compatibility"),
        allowed_tools=meta.get("allowed-tools"),
        metadata=meta.get("metadata") or {},
        user_invocable=bool(meta.get("user-invocable", False)),
        install_as=meta.get("install-as"),
        argument_hint=meta.get("argument-hint"),
    )


def _parse_skill_md(path: Path) -> tuple[str, str, str]:
    """Parse a skill directory into (skill_id, description, body).

    Returns:
        skill_id:    Normalized directory name (lowercase, hyphens)
        description: From frontmatter ``description:`` field
        body:        Skill content after the frontmatter block

    Raises:
        ValueError: SKILL.md not found.
    """
    skill_id = path.name.lower().replace(" ", "-").replace("_", "-")

    skill_md = _find_skill_md(path)
    if skill_md is None:
        raise ValueError(f"SKILL.md not found in {path}")

    props = _read_skill_properties(path)
    description = props.description or path.name
    _, body = _parse_frontmatter(skill_md.read_text())
    return skill_id, description, body.strip()


def _resolve_skill_path(skill_root: Path, relative: str) -> Path:
    """Resolve a relative path and verify it stays within skill_root.

    Raises:
        ValueError: If the path escapes the skill directory.
        FileNotFoundError: If the resolved path does not exist.
    """
    resolved = (skill_root / relative).resolve()
    if not resolved.is_relative_to(skill_root.resolve()):
        raise ValueError(
            f"Path {relative!r} escapes the skill directory. Only paths within {skill_root} are allowed."
        )
    if not resolved.exists():
        raise FileNotFoundError(f"{relative!r} not found in skill directory {skill_root}")
    return resolved


def _build_script_command(
    script: Path, args: tuple[str, ...], interpreter: str | None = None
) -> str:
    """Build the shell command to run a script.

    Priority: explicit interpreter > shebang line > direct execution.
    Pass ``interpreter`` explicitly for scripts without a shebang.
    """
    quoted_script = shlex.quote(str(script))
    quoted_args = " ".join(shlex.quote(a) for a in args)

    if interpreter:
        cmd = (
            f"{interpreter} {quoted_script} {quoted_args}"
            if quoted_args
            else f"{interpreter} {quoted_script}"
        )
        return cmd.strip()

    cmd = f"{quoted_script} {quoted_args}" if quoted_args else quoted_script
    return cmd.strip()


class Skill:
    """Base class for agent skills.

    Wraps a Python object or inline content so the LLM can discover it via
    ``doc(self.<skill>)``. Third-party libraries use ``Skill(obj)``; inline
    text uses ``Skill(content=...)``. For SKILL.md-based skills use
    ``TextSkill(path=...)``.

        class WritingSkill(Skill):
            \"\"\"Write and reuse helper methods on the agent.\"\"\"

        class MyAgent(Agent, llm=llm):
            self.pd = Skill(pd)  # registers pandas for discovery; use pd directly
    """

    # Tells agentdoc not to expand this type in "Referenced Types" sections.
    # Skills show a brief one-liner wherever they appear as a field type.
    __agentdoc_skip__ = True

    # Skills are reconstructed on agent init (e.g., LibraryManager rescans libs/).
    # They shouldn't be serialized in snapshots — mark them as nosnapshot.
    __nosnapshot__ = True

    def __init__(self, obj=None, *, content: str | None = None):
        n_given = sum(x is not None for x in (obj, content))
        if n_given > 1:
            raise ValueError("Skill() accepts exactly one of: obj, content")
        if n_given == 0 and type(self) is Skill:
            raise ValueError("Skill() requires one of: obj=<object>, content=<str>")

        if obj is not None:
            self._skill_obj = obj
            self.__class__ = type("Skill", (Skill,), {"__doc__": obj.__doc__ or ""})  # type: ignore[misc]
        elif content is not None:
            self.__class__ = type("Skill", (Skill,), {"__doc__": content})  # type: ignore[misc]

    def __dir__(self) -> list[str]:
        # Forward dir() to wrapped object so the LLM can discover its attributes.
        base = list(super().__dir__())
        try:
            skill_obj = object.__getattribute__(self, "_skill_obj")
            return list(set(base) | set(dir(skill_obj)))
        except AttributeError:
            return base


class TextSkill(Skill):
    """Skill loaded from a SKILL.md directory. Has id, description, run_script, read_file."""

    def __init__(self, *, path: Path | str, id: str | None = None):
        skill_id, description, body = _parse_skill_md(Path(path))
        docstring = f"{description}\n---\n{body}"
        class_name = "".join(word.capitalize() for word in skill_id.split("-"))
        self._skill_path = Path(path)
        self.__class__ = type(  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]
            class_name, (TextSkill,), {"__doc__": docstring, "_id": id or skill_id}
        )

    @property
    def id(self) -> str:
        return type(self)._id  # type: ignore[attr-defined]

    @property
    def description(self) -> str:
        doc = type(self).__doc__ or ""
        return doc.split("\n---\n")[0].split("\n")[0]

    async def run_script(
        self,
        name: str,
        *args: str,
        interpreter: str | None = None,
        timeout: float = 30.0,
    ) -> str:
        """Run a script from this skill's scripts/ directory.

        Supports any script type — Python, shell, Ruby, Node.js, Perl, etc.
        Scripts with a shebang line run directly. For scripts without one,
        pass ``interpreter`` explicitly.

            output = await self.my_skill.run_script("run_eval.py", "--limit", "10")
            output = await self.my_skill.run_script("report.sh")
            output = await self.my_skill.run_script("query.sql", interpreter="psql -f")
            output = await self.my_skill.run_script("process.js", "input.json")
        """
        script = _resolve_skill_path(self._skill_path, f"scripts/{name}")
        if not script.is_file():
            scripts_dir = self._skill_path / "scripts"
            available = (
                sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
                if scripts_dir.is_dir()
                else []
            )
            raise FileNotFoundError(
                f"Script {name!r} not found in {scripts_dir}. Available: {available}"
            )

        from nemo_oo_agents.tools.bash_tool import BashTool

        cmd = _build_script_command(script, args, interpreter=interpreter)
        result = await BashTool(working_dir=self._skill_path).run(cmd, timeout=timeout)
        return str(result)

    def read_file(self, path: str) -> str:
        """Read a file from anywhere within this skill's directory.

        content = self.my_skill.read_file("scripts/run_eval.py")
        content = self.my_skill.read_file("assets/prompt.txt")
        content = self.my_skill.read_file("SKILL.md")
        """
        resolved = _resolve_skill_path(self._skill_path, path)
        if not resolved.is_file():
            raise ValueError(f"{path!r} is not a file.")
        return resolved.read_text()
