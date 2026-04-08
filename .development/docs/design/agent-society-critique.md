# Agent Society Design - Critical Review

## Summary

The proposed "Agent Society" experiment aims to study idea propagation in AI research communities, with a backtesting methodology for validation. While ambitious and interesting, the design has several methodological weaknesses that should be addressed.

---

## Major Concerns

### 1. Lack of Baselines and Controls

**Problem**: No comparison baselines are defined. How do we know the agent society performs better than:
- A single agent with the same total compute?
- Random idea generation?
- Simple keyword-based literature retrieval?
- Human researchers (if we have data)?

**Recommendation**: Add explicit baselines:
```yaml
baselines:
  - single_agent: One agent with N times the cycles
  - random: Random paper sampling + random hypothesis generation
  - retrieval: BM25/embedding retrieval without generation
  - human: Historical human prediction accuracy (if available)
```

### 2. Circular Evaluation Problem

**Problem**: Using LLMs to evaluate LLM-generated ideas is circular. The same biases that cause contamination may also cause inflated evaluation scores.

**Specific issues**:
- Semantic similarity to ground truth may reward contaminated predictions
- LLM judges may have the same knowledge contamination as generators
- No human evaluation protocol defined

**Recommendation**:
- Add human evaluation for a subset of predictions
- Use multiple evaluation methods (exact match, human judges, downstream utility)
- Define inter-rater reliability metrics

### 3. Undefined Success Criteria

**Problem**: What constitutes a "successful" prediction? The metrics are vague:
- "Hit rate" - what similarity threshold?
- "Anticipation score" - how is "early" defined?
- "Breakthrough detection" - what makes something a breakthrough?

**Recommendation**: Pre-register specific, falsifiable hypotheses:
```
H1: Agent society with 7 diverse agents will achieve >15% hit rate
    (cosine similarity > 0.8) on top-100 cited papers published
    2021-2023, when seeded with 2020 papers.

H2: Historian critic agents will reduce anachronism rate by >50%
    compared to uncontrolled baseline.
```

### 4. Confounding Variables Not Addressed

**Problem**: Many factors could explain results besides "agent society dynamics":
- Model capability differences
- Prompt engineering quality
- Paper selection bias in seeding
- Domain difficulty differences

**Recommendation**: Control for confounds:
- Same model across conditions
- Standardized prompts with ablations
- Random paper sampling with stratification
- Multiple domains with difficulty ratings

### 5. Forgetting Approaches Are Untested Assumptions

**Problem**: The 6 "forgetting" approaches are speculative. No evidence they actually work:
- Historian critics might have the same contamination
- Competitive games might just make agents more evasive, not more honest
- Forbidden term lists are incomplete by definition

**Recommendation**:
- Run pilot studies on each forgetting approach
- Define measurable contamination metrics first
- Validate with known-contaminated vs known-clean examples

### 6. Scalability and Compute Costs Undefined

**Problem**: No discussion of:
- How many API calls per experiment?
- Estimated costs?
- Time to run a single backtest?
- Statistical power analysis (how many runs needed)?

**Recommendation**: Add resource estimates:
```yaml
resource_estimates:
  single_backtest:
    agents: 7
    cycles: 100
    api_calls_per_cycle: ~50
    total_api_calls: 5000
    estimated_cost: $XX
    estimated_time: X hours

  statistical_power:
    effect_size: 0.3 (medium)
    alpha: 0.05
    power: 0.8
    required_runs: 30
```

### 7. Cherry-Picking Risk in Case Studies

**Problem**: The proposed case studies (ChatGPT, AlphaFold, etc.) are famous breakthroughs. This introduces:
- Hindsight bias in selecting "interesting" cases
- Publication bias toward positive results
- No representation of failed predictions

**Recommendation**:
- Pre-register case studies before running experiments
- Include "negative" cases (things that didn't happen)
- Random sampling of papers, not just famous ones

### 8. Virality Metric Is Poorly Defined

**Problem**: "Virality" in the agent society is self-referential:
- Ideas are viral if other agents cite them
- But agents are prompted by the same system
- This may just measure prompt similarity, not idea quality

**Recommendation**:
- External validation of viral ideas (do they match real-world impact?)
- Compare in-society virality to actual citation counts
- Test if viral ideas are actually novel or just well-phrased

---

## Minor Concerns

### 9. No Ablation Studies Planned

What happens if we remove:
- Critique mechanism?
- Cross-specialty agents?
- Ingestion pipeline?
- Specific agent personas?

### 10. Reproducibility Concerns

- Are prompts fully specified?
- What about model temperature, sampling?
- How to handle API changes over time?
- Will code/data be released?

### 11. Ethical Considerations Missing

- What if agents generate harmful research directions?
- Dual-use concerns for some domains (bio, cyber)?
- Should there be content filtering?

### 12. No Failure Modes Discussed

What if:
- All agents converge on the same ideas?
- No cross-specialty spread occurs?
- Forgetting mechanisms kill all creativity?
- The experiment produces no breakthroughs?

---

## Recommended Improvements

### Phase 0: Pilot Studies (Before Main Experiment)

1. **Contamination measurement pilot**
   - How contaminated are current models for different cutoffs?
   - Which forgetting approach works best?
   - Sample size: 100 probes per model

2. **Evaluation calibration pilot**
   - Do semantic similarity scores correlate with human judgments?
   - What threshold should we use?
   - Sample size: 50 ideas rated by 3 humans each

3. **Single-agent baseline pilot**
   - What does one agent achieve with same compute?
   - Is multi-agent actually better?

### Strengthen Methodology

1. **Pre-registration**
   - Register hypotheses, methods, and analysis plan before running
   - Specify primary vs exploratory analyses

2. **Multiple evaluation methods**
   - Automatic: semantic similarity, keyword overlap
   - Human: blind rating of idea quality/novelty
   - Downstream: do ideas lead to useful research directions?

3. **Statistical rigor**
   - Power analysis
   - Multiple comparison correction
   - Effect size reporting, not just p-values

4. **Ablation matrix**
   ```
   | Condition | Agents | Critics | Cross-specialty | Forgetting |
   |-----------|--------|---------|-----------------|------------|
   | Full      | 7      | Yes     | Yes             | Historian  |
   | No critic | 7      | No      | Yes             | Historian  |
   | Homogeneous| 7     | Yes     | No              | Historian  |
   | No forget | 7      | Yes     | Yes             | None       |
   | Single    | 1      | N/A     | N/A             | Historian  |
   ```

### Add Negative Controls

1. **Shuffled timeline**: Seed with 2023 papers, predict 2020
   - Should get ~0% hit rate (can't predict the past)
   - If we get high scores, something is wrong

2. **Random domain**: Agents specialized in X predict Y
   - ML agents predicting philosophy breakthroughs
   - Should perform worse than domain-matched agents

3. **Noise injection**: Add fake papers to seed
   - Do agents get confused or filter them out?

---

## Summary of Changes Needed

| Priority | Issue | Action |
|----------|-------|--------|
| **Critical** | No baselines | Define single-agent, random, retrieval baselines |
| **Critical** | Circular evaluation | Add human evaluation protocol |
| **Critical** | Vague metrics | Pre-register specific hypotheses with thresholds |
| **High** | Untested forgetting | Run pilot studies first |
| **High** | No power analysis | Calculate required sample sizes |
| **High** | Cherry-picking risk | Pre-register case studies |
| **Medium** | No ablations | Design ablation matrix |
| **Medium** | Reproducibility | Specify all prompts, parameters |
| **Low** | Ethics | Add content filtering discussion |
| **Low** | Failure modes | Document expected failure cases |

---

## Verdict

**As a reviewer, I would request major revisions.**

The core idea is interesting and novel, but the methodology needs significant strengthening before this would be publishable. The main issues are:

1. Lack of rigorous baselines makes it impossible to interpret results
2. Circular LLM evaluation undermines validity
3. No pilot studies to validate key assumptions
4. Insufficient statistical planning

With the recommended improvements, this could become a strong contribution to the field of AI agents and scientific discovery.
