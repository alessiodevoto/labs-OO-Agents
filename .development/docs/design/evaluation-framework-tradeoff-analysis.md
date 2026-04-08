# Evaluation Framework Trade-Off Analysis

**Date**: 2025-12-12
**Purpose**: Evaluate open-source alternatives to current agent006 evaluation infrastructure

## Executive Summary

Our current evaluation infrastructure has reliability issues. This analysis compares open-source LLM agent evaluation frameworks as potential replacements or complements.

**Key Finding**: DeepEval and Arize Phoenix offer the most comprehensive agent evaluation capabilities with native OpenTelemetry support and agent-specific metrics.

---

## Trade Space Matrix

| Framework | License | Agent Support | Tool Call Eval | Multi-Turn | Tracing | Pytest Integration | Benchmarks | Maturity | Community |
|-----------|---------|---------------|----------------|------------|---------|-------------------|------------|----------|-----------|
| **DeepEval** | Apache 2.0 | ✅ Excellent | ✅ Native | ✅ Yes | ✅ OTel + @observe | ✅ Native | MMLU, Custom | High | 2.3k⭐ |
| **Arize Phoenix** | Apache 2.0 | ✅ Excellent | ✅ Native | ✅ Session-level | ✅ OTel native | ⚠️ Via SDK | BFCL, τ-bench | High | 3.6k⭐ |
| **OpenAI Evals** | MIT | ⚠️ Basic | ⚠️ Via custom | ✅ Yes | ❌ No | ❌ No | OpenAI registry | Medium | 14.5k⭐ |
| **Ragas** | Apache 2.0 | ❌ RAG-focused | ❌ No | ⚠️ Limited | ⚠️ Basic | ⚠️ Via pytest | RAG-specific | Medium | 7.3k⭐ |
| **Langfuse** | MIT | ✅ Good | ✅ Via tracing | ✅ Yes | ✅ OTel + SDK | ❌ No | None | High | 6.3k⭐ |
| **Opik** | Apache 2.0 | ✅ Good | ✅ Via metrics | ✅ Yes | ✅ Native | ⚠️ Via SDK | Custom | Medium | 1.9k⭐ |
| **HARBOR** | Research | ⚠️ Competitive | ❌ No | ✅ Yes | ⚠️ Custom | ❌ No | Auction testbed | Low | Research |
| **Agent006 (Current)** | Internal | ✅ Yes | ✅ Custom | ✅ Yes | ✅ OTel | ❌ No | BFCL, Custom | Medium | N/A |

**Legend**: ✅ Excellent/Native support | ⚠️ Partial/Via extension | ❌ Limited/None

**Note**: HARBOR is excluded from recommendations as it's a research testbed for competitive multi-agent scenarios, not a general evaluation framework.

---

## Detailed Framework Analysis

### 1. DeepEval (⭐ Recommended for Agent Evaluation)

**Overview**: The most comprehensive open-source LLM evaluation framework with native agent support.

**Strengths**:
- 14+ evaluation metrics specifically designed for agents
- Native pytest integration - run evals as unit tests
- `@observe` decorator for tracing components (LLM calls, retrievers, tools, agents)
- Agentic workflow metrics evaluate overall execution flow
- Self-explaining metrics (tells you WHY score can't be higher)
- CI/CD integration with GitHub Actions
- Confident AI platform for team collaboration (optional SaaS)
- OpenTelemetry instrumentation built-in

**Weaknesses**:
- Requires integration with Confident AI platform for full features
- Less mature than OpenAI Evals
- Opinionated metric design

**Agent-Specific Features**:
- Tool calling accuracy metrics
- Agent trajectory evaluation
- Multi-step reasoning assessment
- Component-level metrics for agent subsystems

**Integration Effort**: **Medium**
- Requires wrapping existing tests in DeepEval test cases
- Need to instrument agents with @observe decorators
- Compatible with existing OpenTelemetry setup

**Cost**: Free (open-source) + Optional Confident AI SaaS

**Sources**:
- [DeepEval GitHub](https://github.com/confident-ai/deepeval)
- [AI Agent Evaluation Guide](https://deepeval.com/guides/guides-ai-agent-evaluation)
- [DeepEval vs Ragas Comparison](https://deepeval.com/blog/deepeval-vs-ragas)

---

### 2. Arize Phoenix (⭐ Recommended for Production Monitoring)

**Overview**: Open-source observability platform with advanced agent evaluation capabilities.

**Strengths**:
- Deep visibility into agent reasoning, planning, and actions
- Session-level evaluation (not just individual turns)
- Tool-calling analysis with convergence tracking
- Visual trace inspection with node-level details
- Online evaluations on production traffic
- OpenTelemetry native support
- Real-time alerts for production monitoring
- Supports BFCL and τ-bench benchmarks

**Weaknesses**:
- Less pytest integration (SDK-based)
- Requires Phoenix server for visualization
- Steeper learning curve

**Agent-Specific Features**:
- Trajectory metrics: step completion, task success rate
- Tool-call accuracy tracking
- Agent convergence scoring
- Replayable traces for debugging

**Integration Effort**: **High**
- Requires Phoenix server deployment
- Need to instrument with Phoenix SDK
- More complex setup than DeepEval

**Cost**: Free (open-source)

**Sources**:
- [Arize LLM Evaluation Platforms Comparison](https://arize.com/llm-evaluation-platforms-top-frameworks/)
- [Top Agent Evaluation Tools](https://www.getmaxim.ai/articles/top-agent-evaluation-tools-in-2025-best-platforms-for-reliable-enterprise-evals/)

---

### 3. OpenAI Evals

**Overview**: OpenAI's official evaluation framework with large benchmark registry.

**Strengths**:
- Large community (14.5k stars)
- Extensive benchmark registry
- Well-documented
- Completion Function Protocol for advanced use cases

**Weaknesses**:
- Basic agent support (requires custom evals)
- No native pytest integration
- No built-in tracing
- Less focus on agentic workflows
- OpenAI-centric design

**Agent-Specific Features**:
- Completion Function Protocol supports tool-using agents
- Custom eval creation for agent workflows

**Integration Effort**: **High**
- Need to write custom evals for agent tasks
- No direct compatibility with existing infrastructure
- Would require significant refactoring

**Cost**: Free (open-source)

**Sources**:
- [OpenAI Evals GitHub](https://github.com/openai/evals)

---

### 4. Ragas

**Overview**: Framework purpose-built for RAG pipeline evaluation.

**Strengths**:
- Excellent for RAG-specific metrics
- Incorporates latest RAG research
- Simple to use for RAG pipelines
- 5 core RAG metrics (faithfulness, context relevancy, etc.)

**Weaknesses**:
- **NOT designed for agent evaluation**
- No tool calling support
- Limited multi-turn support
- Metrics are not self-explaining
- RAG-focused, not general-purpose

**Agent-Specific Features**: ❌ None (RAG-focused only)

**Integration Effort**: **N/A** (Not suitable for agent evaluation)

**Cost**: Free (open-source)

**Sources**:
- [RAG Evaluation Tools Comparison](https://research.aimultiple.com/rag-evaluation-tools/)
- [DeepEval vs Ragas](https://deepeval.com/blog/deepeval-vs-ragas)

---

### 5. Langfuse

**Overview**: Open-source LLM engineering platform with observability focus.

**Strengths**:
- Deep insights into latency, cost, error rates
- Good for production monitoring
- Multi-step agent workflow support
- OpenTelemetry compatible
- Session-based evaluation
- Good for debugging complex agents

**Weaknesses**:
- No pytest integration
- SDK-based (not test framework)
- Requires Langfuse server
- Less focus on evaluation metrics vs observability

**Agent-Specific Features**:
- Multi-step agent tracing
- Tool call tracking via traces
- Session-level analysis

**Integration Effort**: **High**
- Requires Langfuse server deployment
- SDK instrumentation needed
- More observability than testing framework

**Cost**: Free (open-source) + Optional Cloud

**Sources**:
- [AI Agent Observability with Langfuse](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)

---

### 6. Opik by Comet

**Overview**: End-to-end LLM observability platform with evaluation support.

**Strengths**:
- End-to-end observability (dev + production)
- Experiment tracking with different prompts
- Pre-configured evaluation metrics
- Compatible with any LLM
- Direct integrations available

**Weaknesses**:
- Less mature (1.9k stars)
- Commercial focus (Comet ML)
- SDK-based approach
- Less community adoption

**Agent-Specific Features**:
- Custom metric definitions
- LLM-agnostic design
- Prompt experiment tracking

**Integration Effort**: **Medium-High**
- SDK integration required
- Platform setup needed
- Less documentation than alternatives

**Cost**: Free (open-source) + Commercial platform

**Sources**:
- [Opik by Comet](https://www.comet.com/site/products/opik/)
- [LLM Evaluation Frameworks Comparison](https://www.comet.com/site/blog/llm-evaluation-frameworks/)

---

### 7. HARBOR

**Overview**: Research testbed for evaluating persona dynamics in competitive multi-agent scenarios.

**Strengths**:
- Focused on multi-agent competition
- Persona-based agent behavior
- Competitive bidding scenarios (auctions)
- Theory of mind strategies
- Competitor profiling capabilities

**Weaknesses**:
- **Research project, not production framework**
- Specific to competitive scenarios (auctions)
- No general evaluation capabilities
- No tool calling support
- Limited documentation
- Not designed for general agent testing

**Agent-Specific Features**:
- Multi-agent competition testbed
- Persona dynamics evaluation
- Auction-based scenarios
- Strategic behavior analysis

**Integration Effort**: **Not Applicable** (Research testbed, not suitable for production)

**Cost**: Free (research project)

**Use Case**: Only suitable for research into competitive multi-agent dynamics, not for general agent evaluation.

**Sources**:
- [HARBOR: Exploring Persona Dynamics in Multi-Agent Competition](https://arxiv.org/abs/2502.12149)
- [Survey on Evaluation of LLM-based Agents](https://arxiv.org/abs/2503.16416)

---

### 8. Agent006 (Current System)

**Overview**: Custom evaluation infrastructure with e2e-optimization and prompt-optimization runners.

**Strengths**:
- Tailored to our specific needs
- OpenTelemetry instrumentation built-in
- File-based test definitions (JSONL)
- Improvement loop support
- Multi-agent support via adapters
- Already integrated with our agents

**Weaknesses**:
- **Reliability issues** (generation failures, empty responses)
- No pytest integration
- Custom implementation requires maintenance
- Limited community support
- Model-specific bugs (nano-v3 recursion loops)
- Two separate frameworks (e2e-opt + prompt-opt) causing confusion

**Agent-Specific Features**:
- Test-level agent specification
- Custom test functions
- Multi-scorer support (prompt-opt only)
- Trace capture (.006trace.jsonl)

**Integration Effort**: **N/A** (Current system)

**Cost**: Internal development + maintenance

---

## Benchmark Support Comparison

| Benchmark | DeepEval | Phoenix | OpenAI Evals | Agent006 |
|-----------|----------|---------|--------------|----------|
| BFCL | ✅ Via adapters | ✅ Native | ❌ No | ✅ Adapter exists |
| τ-bench | ✅ Via adapters | ✅ Native | ❌ No | ❌ No |
| MMLU | ✅ Native | ⚠️ Custom | ✅ Registry | ❌ No |
| HumanEval | ✅ Via adapters | ⚠️ Custom | ✅ Registry | ❌ No |
| Custom Tests | ✅ Easy | ✅ Easy | ✅ Yes | ✅ Yes |

---

## Key Decision Factors

### For Testing & CI/CD: DeepEval
- Native pytest integration
- Easy to write tests
- Self-explaining metrics
- CI/CD friendly

### For Production Monitoring: Arize Phoenix or Langfuse
- Real-time monitoring
- Visual trace inspection
- Production traffic evaluation
- Alert capabilities

### For Quick Migration: Hybrid Approach
Keep agent006 for test definitions, integrate DeepEval metrics:
- Use existing .006eval.jsonl format
- Add DeepEval metrics as evaluators
- Gradual migration path

---

## Recommendations

### Option 1: Adopt DeepEval (⭐ Recommended)

**Strategy**: Replace evaluation infrastructure with DeepEval

**Pros**:
- Battle-tested framework
- Active community and development
- Native agent evaluation support
- Pytest integration = familiar workflow
- Self-explaining metrics reduce debugging time

**Cons**:
- Migration effort required
- Some vendor lock-in with Confident AI (optional)
- Need to rewrite existing tests

**Effort**: **3-4 weeks**
- Week 1: Setup + pilot with 2-3 tests
- Week 2: Migrate capability tests
- Week 3: Integrate with CI/CD
- Week 4: Team training + documentation

**ROI**: **High**
- Reduced maintenance burden
- Better reliability
- Community support
- Standard tooling

---

### Option 2: Hybrid Approach (⭐ Lower Risk)

**Strategy**: Keep agent006 test definitions, integrate DeepEval metrics

**Pros**:
- Gradual migration
- Keep existing test infrastructure
- Add DeepEval metrics as evaluators
- Lower initial effort

**Cons**:
- Still maintain custom infrastructure
- Partial benefits only
- Two systems to understand

**Effort**: **1-2 weeks**
- Week 1: DeepEval integration layer
- Week 2: Migrate key metrics

**ROI**: **Medium**
- Immediate metric improvements
- Path to full migration later

---

### Option 3: Add Arize Phoenix for Production

**Strategy**: Keep agent006 for testing, add Phoenix for production monitoring

**Pros**:
- Separate concerns (testing vs monitoring)
- Production observability gains
- Keep existing tests
- Real-time alerting

**Cons**:
- Doesn't solve testing reliability issues
- Additional infrastructure
- Two systems to maintain

**Effort**: **2-3 weeks**
- Week 1: Phoenix server setup
- Week 2: Instrumentation
- Week 3: Dashboard configuration

**ROI**: **Medium**
- Production visibility
- Doesn't address core testing issues

---

### Option 4: Fix Current System

**Strategy**: Debug and fix agent006 evaluation infrastructure

**Pros**:
- No migration needed
- Keep customizations
- Zero learning curve

**Cons**:
- **Root causes unclear** (model issues? framework issues?)
- Ongoing maintenance burden
- No community support
- Still custom solution

**Effort**: **Unknown** (could be 1 week or 1 month)

**ROI**: **Unknown**
- May not solve reliability issues
- Continues maintenance burden

---

## Cost Analysis

| Option | Initial | Annual | Notes |
|--------|---------|--------|-------|
| DeepEval (OSS only) | $0 | $0 | Self-hosted, free tier |
| DeepEval + Confident AI | $0 | $5k-50k | Team collaboration features |
| Phoenix (OSS) | $0 | $0 | Self-hosted |
| Keep Current | $0 | ~$80k | Engineering time (2 weeks/year maintenance) |

**Recommendation**: Start with OSS-only DeepEval, evaluate Confident AI after 3 months if needed.

---

## Migration Path (Recommended: Option 1 - DeepEval)

### Phase 1: Pilot (Week 1)
1. Install DeepEval: `pip install deepeval`
2. Convert 2-3 capability tests to DeepEval format
3. Run locally + evaluate metrics
4. Document learnings

### Phase 2: Core Tests (Week 2)
1. Migrate all capability tests
2. Set up Confident AI (optional)
3. Configure GitHub Actions CI
4. Create test templates

### Phase 3: Production Integration (Week 3)
1. Integrate with existing OpenTelemetry
2. Add @observe decorators to agent code
3. Set up trace collection
4. Configure dashboards

### Phase 4: Rollout (Week 4)
1. Team training sessions
2. Update documentation
3. Deprecate old framework
4. Post-mortem + lessons learned

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| DeepEval doesn't meet needs | Low | High | Pilot first (Phase 1) |
| Migration takes longer | Medium | Medium | Phased approach |
| Team adoption issues | Low | Medium | Training + templates |
| Lost custom features | Medium | Medium | Document requirements first |
| Vendor lock-in (Confident AI) | Low | Low | Use OSS-only initially |

---

## Conclusion

**Recommendation**: **Adopt DeepEval (Option 1)** with 4-week phased migration.

**Why**:
1. Our current system has **reliability issues** that are unclear to fix
2. DeepEval is **battle-tested** with 2.3k stars and active development
3. **Native agent evaluation** support addresses our core use case
4. **Pytest integration** provides familiar workflow
5. **Community support** reduces maintenance burden
6. **4-week migration** is manageable investment for long-term benefits

**Alternative**: If 4 weeks is too much, start with **Option 2 (Hybrid)** for 1-2 weeks, then migrate fully after validating benefits.

**Not Recommended**: Fixing current system (Option 4) - root causes unclear, ongoing maintenance burden, no community support.

---

## Sources

- [Top 5 Open-Source LLM Evaluation Frameworks](https://dev.to/guybuildingai/-top-5-open-source-llm-evaluation-frameworks-in-2024-98m)
- [DeepEval GitHub](https://github.com/confident-ai/deepeval)
- [DeepEval vs Ragas Comparison](https://deepeval.com/blog/deepeval-vs-ragas)
- [Arize LLM Evaluation Platforms](https://arize.com/llm-evaluation-platforms-top-frameworks/)
- [Top Agent Evaluation Tools 2025](https://www.getmaxim.ai/articles/top-agent-evaluation-tools-in-2025-best-platforms-for-reliable-enterprise-evals/)
- [LLM Agent Evaluation Complete Guide](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide)
- [RAG Evaluation Tools Comparison](https://research.aimultiple.com/rag-evaluation-tools/)
- [Evaluating AI Agents 2025](https://orq.ai/blog/agent-evaluation)
