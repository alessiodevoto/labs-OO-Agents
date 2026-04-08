# litellm Local Cost Map Configuration

**Date:** 2026-02-18
**Status:** Approved

## Problem

`litellm` makes network requests to `raw.githubusercontent.com` on every import to fetch model pricing data. This causes:

1. **Sandbox violations** during optimization runs - each Python process importing litellm triggers a network request
2. **Unnecessary network dependency** - the data is already bundled with litellm
3. **Potential failures** if GitHub is unreachable or rate-limited

### Investigation

Through systematic testing, we confirmed:
- Each `import litellm` in a fresh Python process makes a GitHub request
- Setting `LITELLM_LOCAL_MODEL_COST_MAP=True` prevents the request
- litellm includes local cost data, so no functionality is lost

## Solution

Set `LITELLM_LOCAL_MODEL_COST_MAP=True` in the project's `.env` file to use litellm's bundled local cost data instead of fetching from GitHub.

## Design

### Implementation

Add to `.env`:
```bash
# Prevent litellm from fetching model costs from GitHub (uses bundled local data)
LITELLM_LOCAL_MODEL_COST_MAP=True
```

### Scope

**Project-wide** - applies to all tools and scripts that use litellm:
- E2E optimization pipeline
- Evaluation harness
- Any scripts that import litellm

### Testing

1. Verify no githubusercontent requests when importing litellm
2. Confirm optimization runs complete without sandbox violations
3. Ensure model cost tracking still works correctly

## Rollout

1. Add variable to `.env`
2. Commit to new branch
3. Create separate MR (independent of retry logic changes)

## Alternative Approaches Considered

**Approach 1: Optimizer-only setting**
- Set only in optimizer code
- Con: Other tools would still trigger requests

**Approach 2: .env + code validation**
- Add runtime checks to warn if not set
- Con: Overkill for this fix, adds complexity

**Approach 3: Whitelist githubusercontent.com in sandbox**
- Allow the requests through
- Con: Permits unnecessary network traffic, doesn't fix root cause

## Decision

**Chosen: Simple .env addition with inline comment**
- Simplest solution
- Fixes the problem at the source
- Self-documenting via inline comment
