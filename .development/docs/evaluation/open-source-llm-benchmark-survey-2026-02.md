# Open-Source LLMs for Training Data Generation (February 2026)

> Last updated: Sun Feb 23 2026

Survey of open-source/open-weight LLMs with **permissive licenses** (Apache 2.0, MIT, or equivalent) whose outputs can be used commercially for training. Ranked by **benchmark performance** with release date as context.

**Scope**: Only models available on our NVIDIA NIM endpoint (`integrate.api.nvidia.com`) or with publicly available weights we could run manually. All models listed have licenses that permit using outputs for training.

**Benchmark Legend:**
- **SWE-bench Verified** -- Real-world GitHub issue resolution (% resolved). Best proxy for agentic coding.
- **LiveCodeBench** -- Contamination-free competitive programming (Pass@1 %)
- **BFCL** -- Berkeley Function Calling Leaderboard (tool-use accuracy %)
- **TAU2-bench** -- Multi-turn conversational agent benchmark (avg across domains)
- **WebArena** -- Autonomous web browsing agent (task completion %)
- **OSWorld** -- Desktop OS interaction agent (task completion %)
- **Codeforces** -- Competitive programming Elo rating

---

## Ranking by Coding Performance

Sorted by SWE-bench Verified (the best available proxy for real-world coding + agentic capability).

| # | Model | SWE-bench | LiveCodeBench | BFCL | Release | License | On NIM? |
|---|-------|-----------|---------------|------|---------|---------|---------|
| 1 | **MiniMax M2.5** | **80.2%** | ~65% | 76.8% | Feb 2026 | Modified MIT | No (manual) |
| 2 | **DeepSeek V3.2** | **77.2%** | 83.3% | -- | Dec 2025 | MIT | Yes |
| 3 | **Kimi K2.5** | **76.8%** | 85.0% | -- | Jan 2026 | MIT | Yes |
| 4 | **Qwen 3.5 (397B)** | **76.4%** | 83.6% | 72.9% | Feb 2026 | Apache 2.0 | Yes |
| 5 | **MiniMax M2.1** | **74.0%** | 81.0% | -- | Dec 2025 | Modified MIT | Yes |
| 6 | Devstral 2 (123B) | 72.2% | -- | -- | Dec 2025 | Modified MIT | Yes |
| 7 | Qwen 3 Coder (480B) | 69.6% | ~59% | -- | Jul 2025 | Apache 2.0 | Yes |
| 8 | DeepSeek V3.1 | 66.0% | 74.8% | -- | Aug 2025 | MIT | Yes |
| 9 | Kimi K2 | 65.8% | 53.7% | -- | Jul 2025 | Modified MIT | Yes |
| 10 | gpt-oss-120b | 62.4% | -- | ~68% | Aug 2025 | Apache 2.0 | Yes |
| 11 | gpt-oss-20b | 60.7% | -- | -- | Aug 2025 | Apache 2.0 | Yes |
| 12 | Seed-OSS-36B | 56.0% | 67.4% | -- | Aug 2025 | Apache 2.0 | Yes |
| 13 | Mistral Large 3 (675B) | -- | ~82.8% | -- | Dec 2025 | Apache 2.0 | Yes |

## Ranking by Agentic Performance

Sorted by TAU2-bench / multi-step agent benchmarks (where reported).

| # | Model | TAU2-bench | WebArena | OSWorld | HLE |
|---|-------|-----------|----------|---------|-----|
| 1 | **MiniMax M2.1** | 87.0 (Telecom) | -- | -- | 22.2 |
| 2 | **Qwen 3.5 (397B)** | 86.7 (avg) | -- | -- | 28.7 |
| 3 | **DeepSeek V3.2** | 80.4 (avg) | -- | -- | -- |
| 4 | **Kimi K2.5** | -- | **58.9** | **63.3** | **50.2** |
| 5 | gpt-oss-120b | 67.8 (Retail) | -- | -- | -- |
| 6 | Kimi K2 | 66.1 (avg) | -- | -- | -- |
| 7 | Seed-OSS-36B | 58.2 (avg) | -- | -- | 10.1 |
| 8 | gpt-oss-20b | 54.8 (Retail) | -- | -- | -- |

---

## Model Details (by family)

### DeepSeek

#### DeepSeek V3.2
| Field | Value |
|-------|-------|
| **Release Date** | December 1, 2025 |
| **License** | MIT |
| **Parameters** | 685B total (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `deepseek-ai/deepseek-v3.2` |

| Benchmark | Score | Notes |
|-----------|-------|-------|
| SWE-bench Verified | 77.2% | Via DeepSeek framework; 72-74% on other frameworks |
| SWE-bench Multilingual | 70.2% | |
| LiveCodeBench (Pass@1) | 83.3% | Non-thinking mode |
| TAU2-bench (Airline) | 63.8 | |
| TAU2-bench (Retail) | 81.1 | |
| TAU2-bench (Telecom) | 96.2 | |
| Codeforces Elo | 2701 | Grandmaster level |

Sources: [Technical Report](https://arxiv.org/abs/2512.02556), [HuggingFace](https://huggingface.co/deepseek-ai/DeepSeek-V3.2)

#### DeepSeek V3.1
| Field | Value |
|-------|-------|
| **Release Date** | August 21, 2025 |
| **License** | MIT |
| **Parameters** | 671B total, 37B active (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `deepseek-ai/deepseek-v3.1` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 66.0% |
| LiveCodeBench | 74.8% |

Sources: [Artificial Analysis](https://artificialanalysis.ai/models/deepseek-v3-1)

---

### Qwen

#### Qwen 3.5 (397B-A17B)
| Field | Value |
|-------|-------|
| **Release Date** | February 16, 2026 |
| **License** | Apache 2.0 |
| **Parameters** | 397B total, 17B active (MoE, 512 experts) |
| **Context** | 262K native, extensible to 1M |
| **NIM ID** | `qwen/qwen3.5-397b-a17b` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 76.4% |
| SWE-bench Multilingual | 69.3% |
| LiveCodeBench v6 | 83.6% |
| BFCL v4 | 72.9% |
| TAU2-bench | 86.7 (avg) |
| HLE | 28.7 |

Sources: [HuggingFace](https://huggingface.co/Qwen/Qwen3.5-397B-A17B)

#### Qwen 3 Coder (480B-A35B)
| Field | Value |
|-------|-------|
| **Release Date** | July 2025 |
| **License** | Apache 2.0 |
| **Parameters** | 480B total, 35B active (MoE) |
| **Context** | 256K native, extensible to 1M |
| **NIM ID** | `qwen/qwen3-coder-480b-a35b-instruct` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 69.6% |
| LiveCodeBench v5 | ~59% |

Sources: [HuggingFace](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct), [Blog](https://qwenlm.github.io/blog/qwen3-coder/)

#### Qwen 3 (235B-A22B)
| Field | Value |
|-------|-------|
| **Release Date** | April 29, 2025 |
| **License** | Apache 2.0 |
| **Parameters** | 235B total, 22B active (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `qwen/qwen3-235b-a22b` |

| Benchmark | Score |
|-----------|-------|
| LiveCodeBench v5 | 70.7% |
| BFCL v3 | 70.8% |
| Codeforces Elo | 2,056 |

Sources: [Technical Report](https://arxiv.org/abs/2505.09388)

#### QwQ-32B (Reasoning)
| Field | Value |
|-------|-------|
| **Release Date** | March 2025 |
| **License** | Apache 2.0 |
| **Parameters** | 32.5B (dense) |
| **Context** | 131K tokens |
| **NIM ID** | `qwen/qwq-32b` |

| Benchmark | Score |
|-----------|-------|
| LiveCodeBench | 63.4% |
| BFCL | 66.4% |

Sources: [HuggingFace](https://huggingface.co/Qwen/QwQ-32B)

---

### MiniMax

#### MiniMax M2.5
| Field | Value |
|-------|-------|
| **Release Date** | February 12, 2026 |
| **License** | Modified MIT |
| **Parameters** | 230B total, 10B active (MoE) |
| **Context** | 205K tokens |
| **NIM ID** | **Not on NIM** -- weights on [HuggingFace](https://huggingface.co/MiniMaxAI/MiniMax-M2.5), would need manual setup |

| Benchmark | Score | Notes |
|-----------|-------|-------|
| SWE-bench Verified | **80.2%** | Best open-weight as of release |
| Multi-SWE-bench | 51.3% | |
| SWE-bench Multilingual | -- | |
| LiveCodeBench | ~65% | Different eval window than others |
| BFCL (multi-turn) | 76.8% | |
| HumanEval | 89.6% | |
| BrowseComp | 76.3% | With context management |

Sources: [Blog](https://www.minimax.io/news/minimax-m25), [HuggingFace](https://huggingface.co/MiniMaxAI/MiniMax-M2.5)

#### MiniMax M2.1
| Field | Value |
|-------|-------|
| **Release Date** | December 22, 2025 |
| **License** | Modified MIT |
| **Parameters** | 230B total, 10B active (MoE) |
| **Context** | 200K tokens |
| **NIM ID** | `minimaxai/minimax-m2.1` |

| Benchmark | Score | Notes |
|-----------|-------|-------|
| SWE-bench Verified | 74.0% | |
| Multi-SWE-bench | 49.4% | |
| SWE-bench Multilingual | 72.5% | |
| LiveCodeBench | 81.0% | |
| TAU2-bench (Telecom) | 87.0% | |
| VIBE (avg) | 88.6% | Full-stack dev benchmark |
| Toolathlon | 43.5% | Tool-use benchmark |
| BrowseComp | 47.4% | |
| HLE | 22.2% | |

Sources: [Blog](https://www.minimax.io/news/minimax-m21), [HuggingFace](https://huggingface.co/MiniMaxAI/MiniMax-M2.1)

---

### Moonshot Kimi

#### Kimi K2.5
| Field | Value |
|-------|-------|
| **Release Date** | January 27, 2026 |
| **License** | MIT |
| **Parameters** | ~1T total (MoE, built on K2 base) |
| **Context** | 128K tokens |
| **NIM ID** | `moonshotai/kimi-k2.5` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 76.8% |
| SWE-bench Multilingual | 73.0% |
| LiveCodeBench v6 | 85.0% |
| WebArena | 58.9 |
| OSWorld-Verified | 63.3 |
| HLE | 50.2% |
| BrowseComp | 74.9% |

Sources: [Blog](https://www.kimi.com/blog/kimi-k2-5.html), [HuggingFace](https://huggingface.co/moonshotai/Kimi-K2.5)

#### Kimi K2
| Field | Value |
|-------|-------|
| **Release Date** | July 2025 |
| **License** | Modified MIT |
| **Parameters** | 1T total, 32B active (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `moonshotai/kimi-k2-instruct` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 65.8% |
| LiveCodeBench v6 | 53.7% |
| TAU2-bench | 66.1 (avg) |

Sources: [Technical Report](https://arxiv.org/abs/2507.20534)

---

### Mistral

#### Mistral Large 3 (675B)
| Field | Value |
|-------|-------|
| **Release Date** | December 2-4, 2025 |
| **License** | Apache 2.0 |
| **Parameters** | 675B total, ~41B active (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `mistralai/mistral-large-3-675b-instruct-2512` |

| Benchmark | Score |
|-----------|-------|
| LiveCodeBench v6 | ~82.8% |
| HumanEval | ~92% |
| LMArena | #2 among OSS non-reasoning |

Note: SWE-bench Verified, BFCL, TAU-bench not reported by Mistral.

Sources: [Blog](https://mistral.ai/news/mistral-3), [HuggingFace](https://huggingface.co/mistralai/Mistral-Large-3-675B-Instruct-2512)

#### Devstral 2 (123B)
| Field | Value |
|-------|-------|
| **Release Date** | December 9, 2025 |
| **License** | Modified MIT |
| **Parameters** | 123B (dense) |
| **Context** | 256K tokens |
| **NIM ID** | `mistralai/devstral-2-123b-instruct-2512` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 72.2% |

Sources: [Blog](https://mistral.ai/news/devstral-2-vibe-cli)

---

### OpenAI OSS

#### gpt-oss-120b
| Field | Value |
|-------|-------|
| **Release Date** | August 5, 2025 |
| **License** | Apache 2.0 |
| **Parameters** | ~117B (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `openai/gpt-oss-120b` |

| Benchmark | Score | Notes |
|-----------|-------|-------|
| SWE-bench Verified | 62.4% | At "high" reasoning effort |
| BFCL v3 | ~68% | |
| TAU-bench (Retail) | 67.8% | At "high" reasoning effort |
| Codeforces Elo | 2,463 | |

Sources: [Model Card](https://arxiv.org/html/2508.10925v1)

#### gpt-oss-20b
| Field | Value |
|-------|-------|
| **Release Date** | August 5, 2025 |
| **License** | Apache 2.0 |
| **Parameters** | ~20B (MoE) |
| **Context** | 128K tokens |
| **NIM ID** | `openai/gpt-oss-20b` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 60.7% |
| TAU-bench (Retail) | 54.8% |
| Codeforces Elo | 2,230 |

Sources: [Model Card](https://arxiv.org/html/2508.10925v1)

---

### ByteDance

#### Seed-OSS-36B
| Field | Value |
|-------|-------|
| **Release Date** | August 20, 2025 |
| **License** | Apache 2.0 |
| **Parameters** | 36B (dense) |
| **Context** | 512K tokens |
| **NIM ID** | `bytedance/seed-oss-36b-instruct` |

| Benchmark | Score |
|-----------|-------|
| SWE-bench Verified | 56.0% |
| LiveCodeBench v6 | 67.4% |
| TAU-bench (Retail) | 70.4% |
| TAU-bench (Airline) | 46% |
| HumanEval | 78.8% |
| HLE | 10.1 |

Sources: [GitHub](https://github.com/ByteDance-Seed/seed-oss), [HuggingFace](https://huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct)

---

## Release Timeline (chronological)

| Release Date | Model | License | Total / Active Params | Architecture |
|-------------|-------|---------|----------------------|-------------|
| Mar 2025 | QwQ-32B | Apache 2.0 | 32.5B / 32.5B | Dense |
| Apr 2025 | Qwen 3 (235B) | Apache 2.0 | 235B / 22B | MoE |
| Jul 2025 | Kimi K2 | Modified MIT | 1T / 32B | MoE |
| Jul 2025 | Qwen 3 Coder (480B) | Apache 2.0 | 480B / 35B | MoE |
| Aug 2025 | gpt-oss-120b | Apache 2.0 | ~117B / MoE | MoE |
| Aug 2025 | gpt-oss-20b | Apache 2.0 | ~20B / MoE | MoE |
| Aug 2025 | DeepSeek V3.1 | MIT | 671B / 37B | MoE |
| Aug 2025 | Seed-OSS-36B | Apache 2.0 | 36B / 36B | Dense |
| Dec 2025 | DeepSeek V3.2 | MIT | 685B / MoE | MoE |
| Dec 2025 | Mistral Large 3 (675B) | Apache 2.0 | 675B / ~41B | MoE |
| Dec 2025 | Devstral 2 (123B) | Modified MIT | 123B / 123B | Dense |
| Dec 2025 | MiniMax M2.1 | Modified MIT | 230B / 10B | MoE |
| Jan 2026 | Kimi K2.5 | MIT | ~1T / MoE | MoE |
| Feb 2026 | MiniMax M2.5 | Modified MIT | 230B / 10B | MoE |
| Feb 2026 | Qwen 3.5 (397B) | Apache 2.0 | 397B / 17B | MoE |

---

## Recommendations for Training Data Generation

### Top tier (best output quality)

| Model | Why | Caveat |
|-------|-----|--------|
| **MiniMax M2.5** | 80.2% SWE-bench, 76.8% BFCL -- best open-weight coding | Not on NIM, needs manual setup |
| **DeepSeek V3.2** | 77.2% SWE-bench, 83.3% LiveCodeBench, MIT license | On NIM, ready to use |
| **Kimi K2.5** | 76.8% SWE-bench, 85.0% LiveCodeBench, best WebArena/OSWorld | On NIM, ready to use |
| **Qwen 3.5** | 76.4% SWE-bench, 86.7 TAU2-bench, best agentic model, Apache 2.0 | On NIM, ready to use. Most recent (Feb 2026) |

### Strong tier (good quality, proven)

| Model | Why | Caveat |
|-------|-----|--------|
| **MiniMax M2.1** | 74.0% SWE-bench, 81.0% LiveCodeBench, only 10B active params | On NIM |
| **Mistral Large 3** | 82.8% LiveCodeBench, Apache 2.0 | No SWE-bench score reported |
| **Devstral 2** | 72.2% SWE-bench, code-focused | Modified MIT, limited benchmark coverage |

### Efficiency picks (small models, good cost/quality)

| Model | Why | Caveat |
|-------|-----|--------|
| **MiniMax M2.1/M2.5** | Only 10B active params, competitive with 40B+ active models | Modified MIT |
| **Seed-OSS-36B** | 56% SWE-bench at just 36B dense, 70.4% TAU-bench Retail, Apache 2.0 | Older (Aug 2025) |
| **QwQ-32B** | 32.5B dense reasoning model, Apache 2.0 | No SWE-bench score |

---

## License Notes

| License | Models | Output training OK? |
|---------|--------|-------------------|
| **Apache 2.0** | Qwen family, Mistral Large 3, gpt-oss, Seed-OSS | **Yes** -- no restrictions |
| **MIT** | DeepSeek V3.x, Kimi K2.5 | **Yes** -- no restrictions |
| **Modified MIT** | MiniMax M2.x, Kimi K2, Devstral 2 | **Likely yes** -- check specific terms; generally permissive with minor additions |

**Excluded** (on NIM but NOT output-training-safe):
- Meta Llama 3.x/4.x -- Llama Community License prohibits training other LLMs with outputs
- Google Gemma -- Terms prohibit training competing models

---

## Data Gaps

- **BFCL**: Not reported for DeepSeek, Mistral Large 3, Kimi K2/K2.5
- **TAU-bench**: Not reported for Mistral Large 3, Devstral 2, Qwen 3 Coder, Kimi K2.5
- **WebArena/OSWorld**: Only Kimi K2.5 reports these
- **SWE-bench**: Not reported for Mistral Large 3, QwQ-32B
- **LiveCodeBench**: Not reported for gpt-oss models, Devstral 2
