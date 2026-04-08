# Ralph Loop Critical Mistake - Wrong Model Used

**Date**: Mon Jan 20 22:15:00 CET 2026
**Severity**: CRITICAL
**Impact**: All previous test results (opt30, opt31, opt32, agent007) are INVALID

---

## The Mistake

**What I did wrong**: Tested all agent variants with `qwen/qwen3-next-80b-a3b-instruct` instead of Claude Sonnet 4.5

**Command used (WRONG)**:
```bash
python run_ablation.py --config rsc_dab_hard_opt30 \
  --benchmark dabstep --limit 10 \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct
```

**Command should have been**:
```bash
python run_ablation.py --config rsc_dab_hard_opt30 \
  --benchmark dabstep --limit 10 \
  --provider nvidia_internal \
  --model aws/anthropic/bedrock-claude-sonnet-4-5-v1
```

---

## Why This is Critical

1. **Agents designed for Claude**: The 8-phase architecture with CodeActStrategy was designed and tuned for Claude Sonnet's code generation capabilities

2. **Model-specific behaviors**:
   - Qwen may not follow docstring instructions the same way
   - Code generation patterns differ between models
   - Template usage reliability varies by model

3. **Invalid comparisons**: All the comparisons between opt30 (10%), opt31 (20%), opt32 (10%), and agent007 (20%) were comparing **Qwen's performance**, not Claude's

4. **Wasted iterations**: Hours of iteration and debugging based on Qwen results that don't apply to Claude

---

## Tests That Need Re-Running

**With Claude Sonnet 4.5**:
1. ✅ opt30 (currently running)
2. ⏳ opt31
3. ⏳ opt32
4. ⏳ agent007 baseline

**Previous Results (with Qwen) - DISCARD**:
- opt30: 1/10 (10%) ❌ Invalid
- opt31: 2/10 (20%) ❌ Invalid
- opt32: 1/10 (10%) ❌ Invalid
- agent007: 2/10 (20%) ❌ Invalid

---

## Lessons Learned

1. **Always verify model**: Check `--provider` and `--model` parameters before long test runs
2. **Document test config**: Each test log should clearly show which model was used
3. **Model matters**: DABStep benchmark results are highly model-dependent

---

## Current Status

**Running**: opt30 with Claude Sonnet 4.5 (started Mon Jan 20 22:08 CET)
**Next**: Based on opt30 results, decide whether to test opt31/opt32 or iterate further
**Goal**: >50% pass rate (5/10 tasks) on 10-task subset

---

## Expected Outcomes

**Hypothesis**: Claude Sonnet should perform MUCH better than Qwen because:
- Agents were designed for Claude's capabilities
- Template code in docstrings should be followed more reliably
- 8-phase architecture tested during development (presumably with Claude)

**If Claude also scores ~20%**: Then the architecture itself may be flawed, not just model mismatch

**If Claude scores >50%**: Architecture is sound, just need right model
