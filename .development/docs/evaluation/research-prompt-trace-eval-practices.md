# Deep Research Prompt: Trace Annotation & Agent Evaluation Practices

Use this prompt with deep research tools (Perplexity Pro, Gemini Deep Research, ChatGPT with browsing, etc.)

---

## Research Prompt

I'm building a trace analyzer for LLM-based agents. When an agent fails a task, I have a detailed execution trace (LLM calls, tool invocations, outputs) and I want to:
1. Annotate traces with "what went wrong" during debugging
2. Build an eval set from annotated traces
3. Evaluate an AI system that automatically diagnoses failures

I need to understand current best practices for trace annotation and agent evaluation. Please research:

### 1. LLM Observability Platforms

How do these platforms handle failure annotation and categorization?

- **Langfuse** - How do they structure scores/annotations? Do they have predefined failure categories or free-form?
- **Weights & Biases (W&B Weave)** - Their approach to LLM trace evaluation and annotation
- **Arize Phoenix** - Their trace annotation and evaluation features
- **LangSmith (LangChain)** - How they handle feedback, annotations, and failure analysis
- **Braintrust** - Their evaluation and annotation approach
- **HoneyHive** - Trace debugging and annotation features

For each, I want to know:
- Do they use predefined failure categories or free-form annotations?
- How do they link annotations to specific parts of a trace (whole trace vs specific span)?
- How do they handle multi-causal failures?
- What does their annotation schema look like?

### 2. Agent Evaluation Benchmarks

How do academic and industry benchmarks evaluate agent failures?

- **SWE-bench** - How do they categorize why agents fail on coding tasks?
- **WebArena / VisualWebArena** - Error taxonomy for web agents
- **GAIA** - How they analyze agent failures
- **AgentBench** - Their failure analysis approach
- **TAU-bench** - Tool-use evaluation methodology
- **BFCL (Berkeley Function Calling)** - Error categorization

Do these benchmarks use:
- Predefined error taxonomies?
- Free-form failure explanations?
- Both?

### 3. Error Taxonomy Research

Are there established taxonomies for LLM/agent failures?

- Academic papers on LLM failure modes
- Industry whitepapers on agent debugging
- Any standardized error categorization schemes (like HTTP status codes but for agents)

### 4. Annotation Best Practices

From ML/data labeling best practices:
- How do teams handle annotator disagreement?
- Structured labels vs free-form explanations - when to use each?
- Multi-label vs single-label classification for failure modes
- How to handle "it's complicated" cases that don't fit categories?

### 5. Evaluation of Evaluators

If I build an AI system to diagnose agent failures, how should I evaluate it?
- Precision/recall on failure categories?
- Human evaluation of explanation quality?
- Comparison to human annotations?
- Any existing work on "evaluating LLM judges" that applies?

### Output Format

Please organize findings by platform/benchmark, and end with a synthesis of:
1. Common patterns across tools (what do most platforms do?)
2. Key differences in approach
3. Recommended approach based on what's working in practice
4. Any emerging standards or best practices

---

## Follow-up Questions to Ask

If the initial research is promising, dig deeper with:

1. "Show me the actual annotation schema/data model used by [Langfuse/LangSmith/etc]"
2. "What failure taxonomies does [SWE-bench/WebArena] use? Show me the actual categories."
3. "How does [platform] handle cases where a failure has multiple root causes?"
4. "Are there any open-source tools for agent trace annotation?"
5. "What does the research say about structured vs unstructured feedback for LLM evaluation?"
