# CI and Agent Testing Proposal

## Problem Statement

The `agentdoc` dependency issue revealed gaps in our testing and CI infrastructure:

1. **No CI pipeline** - No automated testing on commits/PRs
2. **No agent smoke tests** - Can't detect import failures or basic startup issues
3. **Missing dependency detection** - No automated check that all packages are installable

The pre-commit hooks only check linting/formatting, not functionality.

## Why CI Didn't Catch This

**Root cause**: There is no CI pipeline configured (no `.github/workflows/` or `.gitlab-ci.yml`)

**What exists**:
- Pre-commit hooks (`.pre-commit-config.yaml`) that run:
  - `ruff` linting and formatting
  - Jupyter notebook output stripping
  - File hygiene checks (trailing whitespace, YAML validation, etc.)

**What's missing**:
- Actual test execution
- Import validation
- Agent startup smoke tests
- Dependency installation verification

## Proposed Solution

### 1. Add Agent Smoke Tests

Create minimal tests that verify agents can at least import and initialize:

```python
# tests/agents/test_agent_imports.py
"""Smoke tests to ensure agents can import successfully."""

def test_librarian_agent_imports():
    """Test that LibrarianAgent can be imported."""
    from agents.librarian_agent.librarian_agent import LibrarianAgent
    assert LibrarianAgent is not None

def test_tpm_agent_imports():
    """Test that TPM agent can be imported."""
    from agents.tpm_agent.runner import TPMAgentRunner
    assert TPMAgentRunner is not None

def test_nemo_oo_agents_runtime_imports():
    """Test that nemo_oo_agents runtime with agentdoc dependency works."""
    from nemo_oo_agents.runtime import enable_tracing
    from nemo_oo_agents.runtime.actor import ActorRuntime
    assert enable_tracing is not None
    assert ActorRuntime is not None
```

### 2. Add GitHub Actions CI Workflow

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.12']

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install uv
        run: pip install uv

      - name: Create virtual environment
        run: uv venv .venv

      - name: Install nemo_oo_agents
        run: |
          source .venv/bin/activate
          uv pip install -e .

      - name: Install agentdoc
        run: |
          source .venv/bin/activate
          uv pip install -e packages/agentdoc/

      - name: Install test dependencies
        run: |
          source .venv/bin/activate
          uv pip install pytest pytest-asyncio

      - name: Run smoke tests
        run: |
          source .venv/bin/activate
          pytest tests/agents/ -v

      - name: Run nemo_oo_agents tests
        run: |
          source .venv/bin/activate
          pytest tests/ -v --ignore=tests/agents/
```

### 3. Add Pre-commit Import Check

Optionally add a local pre-commit hook to catch import errors early:

```yaml
# Add to .pre-commit-config.yaml
  - repo: local
    hooks:
      - id: test-imports
        name: Test critical imports
        entry: python -c "from nemo_oo_agents.runtime import enable_tracing; from agentdoc import doc"
        language: system
        pass_filenames: false
        always_run: true
```

### 4. Document Setup Steps

Update `CLAUDE.md` or create `SETUP.md`:

```markdown
## Development Setup

1. Create virtual environment:
   ```bash
   uv venv .venv
   source .venv/bin/activate
   ```

2. Install nemo_oo_agents:
   ```bash
   uv pip install -e .
   ```

3. **CRITICAL**: Install local packages:
   ```bash
   uv pip install -e packages/agentdoc/
   ```

4. Install pre-commit hooks:
   ```bash
   pip install pre-commit
   pre-commit install
   ```

5. Run tests:
   ```bash
   pytest tests/
   ```
```

## Implementation Plan

### Phase 1: Immediate Fixes (Today)
1. ✅ Fix agentdoc installation (DONE)
2. Create smoke test file for agents
3. Add setup documentation

### Phase 2: CI Infrastructure (This Week)
1. Create GitHub Actions workflow
2. Add smoke tests to CI
3. Test CI on a branch

### Phase 3: Comprehensive Testing (Future)
1. Add unit tests for agent functionality
2. Add integration tests for Slack/GitLab interactions
3. Add test coverage reporting

## Benefits

1. **Catch dependency issues** - CI will fail if local packages aren't installable
2. **Prevent broken imports** - Smoke tests catch import errors immediately
3. **Better onboarding** - New developers get clear setup instructions
4. **Faster feedback** - Errors caught in PR review, not production
5. **Confidence in changes** - Safe to refactor knowing tests will catch breakage

## Open Questions

1. Should CI run on every commit or just PRs?
2. Do we want to add test coverage reporting (e.g., codecov)?
3. Should we test agents against actual Slack/GitLab or use mocks?
4. What's the right balance between fast smoke tests and comprehensive integration tests?

## Next Steps

1. Get feedback on this proposal
2. Create smoke test file
3. Set up GitHub Actions workflow
4. Document setup process
5. Add CI badge to README
