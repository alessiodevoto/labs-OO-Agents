# DABStep Partial Score Tracking

**Goal**: 100% pass rate (10/10 tasks)
**Current Best**: 50% pass rate with simple agent (iter4)
**8-Phase Best**: 40% pass rate but higher partial scores

---

## Score Comparison Matrix

| Task | iter4 (50%) | rsc_dab_hard (40%) | rsc_dab_hard_opt1 (40%) | Notes |
|------|-------------|---------------------|--------------------------|-------|
| **dabstep_5_easy** | ✅ 1.0 | ✅ 1.0 | ✅ 1.0 | Simple aggregation - all pass |
| **dabstep_49_easy** | ❌ 0.0 | ❌ 0.0 | ❌ 0.0 | Fraud RATE vs COUNT issue |
| **dabstep_70_easy** | ✅ 1.0 | ❌ 0.27 | ❌ 0.0 | Not Applicable detection |
| **dabstep_1273_hard** | ✅ 1.0 | ✅ 1.0 | ✅ 1.0 | Simple fee calculation - all pass |
| **dabstep_1305_hard** | ✅ 1.0 | ✅ 1.0 | ✅ 1.0 | Fee + MCC - all pass |
| **dabstep_1464_hard** | ✅ 1.0 | ✅ 1.0 | ❌ 0.0 | Massive fee list - iter4/baseline pass |
| **dabstep_1681_hard** | ❌ 0.0 | ❌ 0.07 | ❌ 0.06 | Fee IDs for specific date |
| **dabstep_1753_hard** | ❌ 0.24 | ❌ 0.28 | ❌ 0.24 | Fee IDs for date range (March) |
| **dabstep_1871_hard** | ❌ 0.36 | ❌ 0.73 | ❌ 0.36 | **Delta calculation - 8-phase much closer!** |
| **dabstep_2697_hard** | ❌ 0.2 | ❌ 0.21 | ❌ 0.11 | Optimization problem (ACI selection) |
| **PASS RATE** | **50%** (5/10) | **40%** (4/10) | **40%** (4/10) | - |
| **AVG PARTIAL** | 0.58 | 0.56 | 0.47 | - |

---

## Key Findings

### 1. **8-Phase Shows Promise on Complex Tasks**
- **dabstep_1871_hard** (delta calculation): 0.73 vs 0.36 - **2x better**
- **dabstep_1753_hard** (March fees): 0.28 vs 0.24 - slightly better
- **Pattern**: Multi-step calculations benefit from structured decomposition

### 2. **8-Phase Struggles with Simple Tasks**
- **dabstep_70_easy**: Dropped from 1.0 → 0.0
- **dabstep_1464_hard**: Dropped from 1.0 → 0.0 (phase 6 timeout)
- **Pattern**: Over-engineering hurts simple tasks

### 3. **Fraud Rate Problem Persists**
- **dabstep_49_easy**: All agents get 0.0
- Issue: Calculating COUNT (which country has most fraud) instead of RATE (highest percentage)
- **Critical**: Need explicit, unavoidable fraud rate calculation

### 4. **Date Handling is Universal Weakness**
- **dabstep_1681_hard** (day 10): 0.0-0.07 across all agents
- **dabstep_1753_hard** (March): 0.24-0.28 across all agents
- Issue: No datetime module, must use day_of_year ranges
- **Need**: Better date range conversion (March = days 60-90)

### 5. **Delta Calculation Almost There**
- **dabstep_1871_hard**: Got -0.948 vs expected -0.940
- Only 0.008 off! (0.85% error)
- **Need**: Better Decimal precision handling

---

## Task Categories by Difficulty

### ✅ **SOLVED (All agents pass)**
1. dabstep_5_easy - Country with most transactions (simple groupby)
2. dabstep_1273_hard - Average fee for credit (simple matching)
3. dabstep_1305_hard - Average fee with MCC join (moderate join)

### 🟡 **PARTIALLY SOLVED (High partial scores)**
1. dabstep_1871_hard - Delta calculation (0.73 with 8-phase, 0.36 with simple)
   - **Gap**: Precision issue (0.008 difference)
   - **Strategy**: Use Decimal throughout, round only at end

2. dabstep_1753_hard - March fee IDs (0.24-0.28)
   - **Gap**: Missing ~6 IDs or including extras
   - **Strategy**: Better date range (March = days 60-90), validate unique transactions

### 🔴 **UNSOLVED (Low partial scores)**
1. **dabstep_49_easy** - Fraud rate (0.0)
   - **Gap**: COUNT vs RATE confusion
   - **Strategy**: Make fraud rate calculation MANDATORY in prompt

2. **dabstep_70_easy** - Not Applicable (0.0-1.0, inconsistent)
   - **Gap**: Doesn't check if entity exists first
   - **Strategy**: Explicit "check existence BEFORE calculation" step

3. **dabstep_1681_hard** - Day 10 fee IDs (0.0-0.07)
   - **Gap**: Date filtering doesn't work
   - **Strategy**: Clear day_of_year == 10 filter

4. **dabstep_2697_hard** - ACI optimization (0.11-0.21)
   - **Gap**: Wrong ACI selected, wrong cost calculation
   - **Strategy**: Need to understand the optimization objective better

---

## Optimization Strategy

### **Phase 1: Fix Fraud Rate (Target: +10%)**
Make fraud rate calculation impossible to skip:
- Add MANDATORY check in Phase 7: "If question contains 'top' AND 'fraud', MUST calculate RATE"
- Show exact formula at the TOP of Phase 7 prompt
- Add validation: "Did you calculate fraud_rate? Yes/No"

### **Phase 2: Fix Not Applicable (Target: +10%)**
Add existence check as Phase 0.5:
- Before any calculation, check if all entities exist
- If merchant not in dataset: return "Not Applicable" immediately
- Add to Phase 5 (extraction): "If filtered data is empty and question asks about specific entity, return Not Applicable"

### **Phase 3: Fix Date Handling (Target: +20%)**
Add date conversion reference card:
```
January = days 1-31
February = days 32-59
March = days 60-90
April = days 91-120
...
```
Make this available in Phase 4 (exploration) and Phase 5 (extraction)

### **Phase 4: Fix Delta Precision (Target: +10%)**
- Use Decimal for all intermediate calculations
- Only convert to float at final answer
- Round to exactly 14 decimals as requested

### **Target After All Fixes: 90%+ (9/10 tasks)**

---

## Next Steps

1. ✅ **Read official baseline prompts** - Done
2. ⏳ **Create opt2 with:**
   - Official baseline's 4-phase structure (Explore → Plan → Execute → Conclude)
   - Mandatory fraud rate calculation
   - Existence checks before computation
   - Date conversion reference
   - Decimal precision throughout
3. ⏳ **Test and iterate**
4. ⏳ **Reach 100%**

---

## Learnings from Official Baseline

From `https://huggingface.co/spaces/adyen/DABstep/blob/main/baseline/prompts.py`:

1. **4-Phase Root Workflow**:
   - Explore → Plan → Execute → Conclude
   - Each phase has clear purpose
   - Can restart from Explore if Execute fails

2. **3-Phase Step Workflow**:
   - Thought (explain reasoning)
   - Code (write and execute)
   - Observation (review outputs)

3. **Critical Rules**:
   - "Not Applicable" only AFTER exhausting all plans
   - Never create notional variables
   - Check documentation FIRST
   - $1M reward for correct answer (motivation)

4. **Key Insight**: Structure + explicit reasoning + validation = better results

---

## Hypothesis for opt2

**Combine best of both worlds:**
- Keep 8-phase structure (good for complex tasks)
- Add official baseline's explicit workflow rules
- Make critical calculations (fraud rate, existence checks) MANDATORY and unavoidable
- Add $1M reward incentive
- Increase iteration limits for complex phases (10 → 15)

**Expected improvement: 40% → 70%+ (7/10 tasks)**
