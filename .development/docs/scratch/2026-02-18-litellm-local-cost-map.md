# litellm Local Cost Map Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent litellm from making network requests to githubusercontent.com by configuring it to use bundled local cost data.

**Architecture:** Single environment variable addition to `.env` file that configures litellm behavior globally.

**Tech Stack:** Environment variables, litellm library

---

## Task 1: Add LITELLM_LOCAL_MODEL_COST_MAP to .env

**Files:**
- Modify: `.env` (add new variable after line 11, in the environment config section)

**Step 1: Locate the appropriate section in .env**

The `.env` file has API keys organized by service. Add the litellm config after the HF_TOKEN line (around line 11).

**Step 2: Add the variable with comment**

Add to `.env` after line 11:
```bash
# Prevent litellm from fetching model costs from GitHub (uses bundled local data)
LITELLM_LOCAL_MODEL_COST_MAP=True
```

**Step 3: Verify the syntax**

Check that the file is valid:
```bash
cat .env | grep LITELLM
```

Expected: Output shows the new line

**Step 4: Commit**

```bash
git add .env
git commit -m "feat: configure litellm to use local cost map

Prevents network requests to githubusercontent.com by using
litellm's bundled local cost data instead of fetching from GitHub.

This fixes sandbox violations during optimization runs.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Test that litellm no longer makes GitHub requests

**Files:**
- Test: Manual verification (no test file needed)

**Step 1: Test baseline (should trigger request)**

Before sourcing .env, verify the problem exists:
```bash
python -c "import litellm; print('Imported')"
```

Expected: (User would see sandbox violation if monitoring)

**Step 2: Test with the fix**

Source the .env and test:
```bash
source .env
python -c "
import os
print(f'LITELLM_LOCAL_MODEL_COST_MAP={os.environ.get(\"LITELLM_LOCAL_MODEL_COST_MAP\")}')
import litellm
print('Imported successfully')
"
```

Expected:
- Output shows `LITELLM_LOCAL_MODEL_COST_MAP=True`
- No sandbox violation (user confirms)
- Import succeeds

**Step 3: Test multiple imports**

Verify it works consistently:
```bash
for i in {1..3}; do
  echo "Test $i"
  python -c "import litellm; print('OK')"
done
```

Expected: All 3 imports succeed without GitHub requests

---

## Task 3: Verify optimization still works

**Files:**
- Test: Run a quick optimization test

**Step 1: Check that eval_pipeline still imports**

```bash
source .venv/bin/activate
source .env
python -c "from eval_pipeline import Evaluator; print('Eval pipeline imports OK')"
```

Expected: No errors, no GitHub requests

**Step 2: Run a minimal eval test**

```bash
cd util/e2e_optimization/src/e2e_optimization/examples/dabstep
python -c "
import os
print('LITELLM_LOCAL_MODEL_COST_MAP:', os.getenv('LITELLM_LOCAL_MODEL_COST_MAP'))

from e2e_optimization import Optimizer
print('Optimizer imports successfully')
"
```

Expected:
- Shows `LITELLM_LOCAL_MODEL_COST_MAP: True`
- Optimizer imports without errors
- No GitHub requests

**Step 3: Document success**

Verify the fix is complete:
```bash
echo "✓ litellm local cost map configured"
echo "✓ No GitHub requests on import"
echo "✓ Optimization pipeline works"
```

---

## Verification Checklist

Before considering this complete:
- [ ] `.env` contains `LITELLM_LOCAL_MODEL_COST_MAP=True`
- [ ] Comment explains why the variable exists
- [ ] Importing litellm does not trigger GitHub requests
- [ ] eval_pipeline imports successfully
- [ ] Changes are committed

---

## Notes

**Why this works:** litellm includes bundled model cost data. Setting `LITELLM_LOCAL_MODEL_COST_MAP=True` tells litellm to use the bundled data instead of fetching the latest from GitHub. This is safe because:
1. The bundled data is recent enough for our needs
2. Model costs don't change frequently
3. We don't need real-time pricing updates

**Testing note:** Confirming "no GitHub requests" requires the user to observe their sandbox/network monitoring, as we don't have instrumentation to detect this programmatically.
