# Skills Approaches: Comparison and Trade-offs

_2026-03-10_

Three layered patterns exist for exposing capabilities to the LLM in agent006. They are
**complementary, not competing** — each layer adds something the previous one
cannot do.

---

## Layer 0 — Plain tool (no Skill)

```python
self.bash = BashTool()
```

The agent sees full `BashTool` docs in `doc(self)` — agentdoc introspects the
class completely because it is not a Skill. Good for always-on tools where the
docs should always be visible.

**Cannot do:**
- Suppress full docstring from `doc(self)` — always fully expanded
- Participate in the automatic `## Skills` table

---

## Layer 1 — Skill subclass (no SkillManager)

```python
class MethodWriting(Skill):
    """Define persistent helper methods on the agent."""
```

```python
self.writing = MethodWriting()
```

The agent sees only the one-liner for `writing` in `doc(self)` because
`Skill.__agentdoc_skip__ = True` suppresses expansion. When the agent needs
the full API it calls `doc(self.writing)` explicitly.

The `## Skills` table in the execution context lists all `Skill` attributes
automatically — the agent sees `writing` there without needing to know the
attribute name upfront.

**Adds over Layer 0:**
- **Docstring suppression**: `doc(self)` stays clean; the skill is visible
  but not noisy
- **Discovery**: the `## Skills` table in the execution context lists all
  `Skill` attributes automatically

---

## Layer 2 — TextSkill + SkillManager (file-based skills)

```python
class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writing = MethodWriting()
        self.git = TextSkill(path=Path("skills/git-workflow"))  # single skill
        SkillManager.install(self, skills_dir=Path("skills/"))  # bulk-load
        # file skills auto-assigned: self.git_workflow, self.frontend_design, ...
```

`TextSkill` loads a single SKILL.md directory and exposes `id`, `description`,
`run_script()`, and `read_file()`. `SkillManager` scans a directory and
attaches each skill as an agent attribute (hyphens → underscores).

All assigned skills appear in the `## Skills` table automatically — same
mechanism as Layer 1.

**Adds over Layer 1:**
- **File-based skills**: SKILL.md directories load as `TextSkill` instances with
  their content as the agent's usage guide
- **Bundled scripts**: `TextSkill.run_script()` runs scripts in `scripts/`
  alongside the SKILL.md
- **Bulk loading**: `SkillManager.install()` loads an entire directory at once

---

## Summary: when to use each layer

| Situation | Pattern |
|-----------|---------|
| Always-on tool, full docs OK in `doc(self)` (e.g. BashTool) | Layer 0 |
| Capability that should be quiet in `doc(self)` | Layer 1 (`Skill` subclass) |
| File-based SKILL.md skills, single or bulk | Layer 2 (`TextSkill` / `SkillManager`) |

Layers 1 and 2 work together: a `Skill` subclass is always accessible via
`doc(self.x)` (Layer 1), and `TextSkill`/`SkillManager` loads file-based
skills alongside it (Layer 2). Discovery is automatic in both cases via the
`## Skills` table.

---

## What each layer exposes to the agent

```
# doc(self) always shows:
bash:    BashTool       # ← FULL docs (Layer 0 — not a Skill)
writing: MethodWriting  # ← one-liner only (Layer 1 — Skill subclass)

# ## Skills table in execution context (all layers):
| Skill           | Description                                      |
|-----------------|--------------------------------------------------|
| `self.writing`  | Define persistent helper methods on the agent.  |
| `self.git_workflow` | Git workflow helpers for branching and committing. |

# Full docs on demand:
doc(self.writing)      # → full MethodWriting docstring and method signatures
doc(self.git_workflow) # → full git workflow guide
```
