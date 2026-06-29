# Paper Status: Helpers Beat Prompts

## Sections

| Section | Status | Notes |
|---------|--------|-------|
| Abstract | DRAFTED | Five contributions: programming model, helpers beat prompts, capability tests, compositional optimization, CIM |
| 1. Introduction | DRAFTED | Four paragraphs mapping to contributions |
| 2. Programming Model | DRAFTED | Agent-as-object, visibility, strategies, context, orchestration |
| 3. Helpers Beat Prompts | DRAFTED | Division of labor, capability tests, DABStep, tau-bench |
| 4. Compositional Optimization | DRAFTED | Decomposition, surfaces, trace loop, DSPy comparison |
| 5. Memory Architecture | DRAFTED | Current mechanisms + CIM proposal + planned experiments |
| 6. Related Work | DRAFTED | Frameworks, code agents, memory — needs expansion |
| 7. Discussion | DRAFTED | Limitations, CIM roadmap, SFT, broader impact |
| 8. Conclusion | DRAFTED | Concise summary of three contributions |

## TODOs (marked with \placeholder{} in LaTeX)

### Figures needed
- [ ] DABStep accuracy progression chart (opt1-opt55, line chart)
- [ ] Architecture diagram (agent class → methods → LLM calls)
- [ ] CIM architecture diagram (main agent, shards, linter flow)
- [ ] Prompt assembly pipeline (system blocks → events → rendering)

### Results needed
- [ ] Full capability test results across multiple models
- [ ] Detailed tau-bench ablation table (which opt fixed which failure pattern)
- [ ] Monolithic vs. decomposed optimization comparison
- [ ] DSPy quantitative comparison (if shared benchmark available)
- [ ] CIM Experiment 1: Contradiction detection rate
- [ ] CIM Experiment 2: Scaling behavior (10-10K facts)
- [ ] CIM Experiment 3: Context efficiency comparison
- [ ] CIM Experiment 4: Bolt-on improvement on tau-bench/DABStep

### Writing needed
- [ ] Co-authors list
- [ ] DABStep citation (find or create)
- [ ] Expand related work with additional citations
- [ ] NeurIPS style file (neurips_2026.sty when available)

## Experimental Data Sources (in repo)

| Benchmark | Agent files | Results | Analysis |
|-----------|------------|---------|----------|
| DABStep | `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt*.py` | `.development/docs/evaluation/dabstep-*` | 40%→80% over 55 iterations |
| Tau-bench | `experiments/evaluation-ablations/agents/tau_bench_opt*.py` | `experiments/evaluation-ablations/tau_baseline_results/` | 95% Pass^1, 12 failure patterns |
| SWE-bench | `experiments/evaluation-ablations/agents/swebench_*.py` | No scores in repo | Multi-phase agent, SFT pipeline |
| Capability | `util/prompt-optimization/` | 11/14 passing | Mode selection, method correctness |
