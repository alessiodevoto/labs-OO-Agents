# Skills Integration

## Goal

Add a reusable way for agents to access specialized knowledge, workflows, and tools through Skills. Skills are modular, self-contained packages that extend agent capabilities with domain-specific expertise. From an agent's standpoint, skills appear as attributes that provide documentation content and bundled resources.

## Architecture

### Skill: Runtime Object

The `Skill` class (`packages/skills-agent006/src/skills_agent006/skill.py`) provides the agent-facing interface:

**Initialization:**
- Created via manager methods: `SkillManager.create_from_id()` or `SkillManager.create_from_path()`
- Manager methods automatically extract metadata (id, name, description) from `SKILL.md` frontmatter
- Skill ID is generated from directory name (normalized: lowercase, hyphens)
- Properties: `id`, `description`, `content`, `path`

**Content Access:**
- Skill content is accessible via `doc(skill)` function from `agentdoc`
- `doc(skill, concise=True)`: Shows description only (first line of docstring)
- `doc(skill, concise=False)`: Shows description + full content (complete docstring)
- Docstring format: `{description} Hint: use doc(skill, concise=False) to access full content.\n---{full_content}`

**Agent Integration:**
- Skills are assigned as agent attributes: `agent.git_workflow = SkillManager.create_from_id("git-workflow", Path("skills"))`
- Attributes appear in agent docstrings like any other tool
- Skill metadata (id, description) accessible as properties

### SkillManager: Manager for Discovery

The `SkillManager` class (`packages/skills-agent006/src/skills_agent006/skill.py`) provides discovery and loading methods:

**Discovery Methods:**
- `SkillManager.discover(paths)`: Recursively searches directories for `SKILL.md` files, returns `dict[str, Skill]`
- `SkillManager.create_from_id(skill_id, paths)`: Load a skill by ID from one or more directories (creates dynamic subclass)
- `SkillManager.create_from_path(path)`: Load a skill by path (creates dynamic subclass with formatted docstring)

**Skill Discovery:**
- Recursively searches directories for `SKILL.md` files
- Each directory containing `SKILL.md` is treated as a skill
- Skills are validated using `skills_ref` library (follows Agent Skills spec)
- Invalid skills are skipped (logged as warnings but don't prevent other skills from loading)

## Usage

### Basic Usage

```python
from skills_agent006 import SkillManager
from pathlib import Path
from agentdoc import doc

# Load skill using manager (creates dynamic subclass with docstring)
agent.git_workflow = SkillManager.create_from_id("git-workflow", Path("skills"))

# Access skill content via doc()
async def my_method(self):
    """Use git workflow knowledge."""
    # Concise mode: shows description only
    description = doc(self.git_workflow, concise=True)

    # Full mode: shows description + full content
    full_content = doc(self.git_workflow, concise=False)
```

### With SkillManager (Discovery)

```python
from skills_agent006 import SkillManager
from pathlib import Path

# Discover all skills in directories
skills = SkillManager.discover([Path(".cursor/skills"), Path(".claude/skills")])

# Activate specific skill by ID
agent.git_workflow = SkillManager.create_from_id("git-workflow", Path(".cursor/skills"))

# Or activate from the discovered list
for skill_id, skill in skills.items():
    attr_name = skill_id.replace("-", "_")
    setattr(agent, attr_name, skill)
```

### Loading by ID or Path

```python
# Load by ID (searches multiple directories)
agent.frontend = SkillManager.create_from_id("frontend-design", [Path(".cursor/skills"), Path("skills")])

# Load by path
agent.git_workflow = SkillManager.create_from_path(Path("skills/git-workflow"))
```

## Skill Format

Skills follow the Agent Skills specification:

**Directory Structure:**
```text
skill-name/
  SKILL.md          # Main skill documentation (required)
  scripts/          # Optional resource files
    helper.py
  config.json       # Optional configuration files
```

**SKILL.md Format:**
```markdown
---
name: Git Workflow
description: Best practices for Git operations
tags: [git, version-control]
---

# Git Workflow Guide

Content here...
```

**Metadata Fields:**
- `name`: Display name (required)
- `description`: Short description (required)
- `tags`: Optional list of tags for filtering
- Other metadata: Any additional YAML frontmatter fields

## Implementation Details

**Skill Loading:**
- Uses `skills_ref` library for parsing and validation
- Falls back to manual frontmatter parsing if `skills_ref` fails
- Skill ID generated from directory name: `"My Skill Name"` → `"my-skill-name"`

**Docstring Format:**
- Manager methods (`SkillManager.create_from_id()`, `SkillManager.create_from_path()`) create dynamic subclasses
- Dynamic subclasses have docstrings formatted as: `{description} Hint: use doc(skill, concise=False) to access full content.\n---{full_content}`
- Usage hint is included inline with description for agent006-style clarity
- Base `Skill` class requires explicit parameters (id, description, content) - use manager methods instead

**Error Handling:**
- Missing `SKILL.md`: Raises `ValueError`
- Invalid frontmatter: Raises `ParseError` (logged as warning, skill skipped in `list()`)
- Missing required fields: Logged as validation warning
- Resource not found: Raises `FileNotFoundError` with helpful message
- Skill not found (in `create_from_id()`): Raises `ValueError` with skill ID

**Discovery:**
- `SkillManager.discover()` returns a dictionary mapping skill IDs to `Skill` instances
- Skill IDs are computed from directory names during discovery (not from skill instances)
- Invalid skills are automatically skipped during discovery
- Multiple directories can be searched (searched in order, first match wins for `create_from_id()`)

**Properties:**
- `skill.id`: Skill identifier (read-only property)
- `skill.description`: Skill description (read-only property)
- `skill.content`: Full skill content (read-only property)
- `skill.path`: Path to skill directory (read-only property)
