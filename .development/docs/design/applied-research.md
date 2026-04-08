# **Project Nemo Synapse: An R\&D Plan for Enterprise AI Agents**

**Status**: RFC, Oct 6, 2025
**Contributors**: [Paul Furgale CH](mailto:pfurgale@nvidia.com), [Ricardo Da Silveira Cabral CH](mailto:rcabral@nvidia.com), \[add yourself\]
\[[Illustrations](https://lucid.app/lucidchart/7424c125-cb8b-4874-97a2-12989cc23175/edit?page=0_0#)\]

## **Summary**

To unlock the next wave of agentic AI in enterprise applications, we need to solve two problems:

1. **Reliability**: Agents must consistently complete tasks, even complex ones that require multiple steps and domain expertise.
2. **Improvement over time**: Agents must learn from experience and adapt to user preferences, becoming more effective with longer use.

We are proposing to address both of these issues by defining an AI Agent Meta-Architecture with five advances:

1. **Memory**: Externalize semantic, episodic, and procedural knowledge. Make it available to read, write and update.
2. **Active Context Engineering**: Ensure every LLM call has only “*the smallest possible set of high-signal tokens that maximize the likelihood of some desired outcome”[^1],* with high-signal tokens coming from memory and the current task, and others discarded.
3. **Orchestration**: Empower agents to understand task scope and break down large problems into a hierarchical plan consisting of smaller tasks. Each task is then assigned to an expert agent and the process can happen recursively until the tasks are small enough for a single agent to process.
4. **Stable Environment** for Agentic Models: Define a stable set of tools for memory, context, and orchestration, and a clear syntax for a structured context window. Train “agentic models” to expect and perform well in this environment.
5. **End-to-end optimization**: Developing playbooks, optimizers, and developer tooling for deployment-time improvement, simultaneously refining prompts, memory, context management, and orchestration for the best performance at the lowest cost.

These advances directly map to our core problems:

|  | Agent Reliability | Improvement over Time |
| :---- | :---- | :---- |
| **Agent Memory** | Agents leverage past problem-solving experiences.	 | Agents continuously improve through use and adapt to specific project needs. |
| **Active Context Engineering** | Agents operate with the minimal, yet most impactful, set of high-signal tokens for task success.	 | Personalized and historical memory is dynamically injected into the context as needed. |
| **Orchestration** | Tasks are optimally sized for agents, with flexible control from dynamic to static.	 | Optimize over orchestration, as well as prompts, context, and memory. |
| **Stable Environment** | Agentic models focus attention on the task itself, not on learning the environment structure. | Agents have the tools to manipulate memory. |
| **End-to-end Optimization** | Structured agents facilitate a clearly defined optimization process.	 | This process runs continuously throughout the project's lifecycle. |

### **Unlocking Emergent Capabilities**

The history of LLM development shows that new capabilities are often unlocked as emergent properties of model scale, with the techniques to harness them following after.

* **Reasoning**: Chain of Thought (CoT) prompting didn't create the ability for LLMs to reason; it was a technique that unlocked a latent capability present in large-scale models, then specifically trained for.
* **Tool Use**: Similarly, reliable function calling became practical only when models were large enough to understand complex API schemas provided in-context, then trained specifically for tool calling.

The response to the release of Claude Sonnet 4.5 suggests we are on the threshold of this emergent behavior for planning and memory. For example Claude Sonnet 4.5 appears to be [aware of its own context window](https://cognition.ai/blog/devin-sonnet-4-5-lessons-and-challenges#the-model-is-aware-of-its-context-window), changing its behavior based on how much context remains. It also [uses the file system](https://cognition.ai/blog/devin-sonnet-4-5-lessons-and-challenges#the-model-is-aware-of-its-context-window) as an external memory store.

This proposal is founded on the hypothesis that **creating a stable environment – for** **memory**, **context, and orchestration – then training models to use it** will create the next breakthrough in reliable agentic behavior. Our meta-architecture is designed to unlock and structure this emergent capability, moving from simple tool use to complex, multi-step goal execution.

### **Benefits of a Meta-Architecture**

Adopting a standardized meta-architecture provides several key advantages for development, evaluation, and performance:

* **Standardized Post-Training**: The architecture provides a clear structure for generating high-quality training data (e.g., trace formats, common descriptions of state and tool usage, examples of successful plans, verifications, and summaries). Data is interoperable between models and can be used to fine-tune models to operate natively within the agent framework.
* **Composable Expertise**: It establishes a clear, repeatable pattern for building and reusing "Expert Agents." This structured approach simplifies development and ensures that new experts can be reliably composed and orchestrated.
* **Targeted Evaluation**: Each component of the architecture (Plan, Memory, Verify, etc.) can be evaluated independently. This allows for the creation of component-specific test suites, leading to more rigorous validation and eventual automation of agent production.
* **Mitigate the most common LLM failures**: LLMs don’t learn nor can stay on track over long-horizon tasks. This meta architecture addresses both problems.

## **Architecture and Questions Walkthrough**

At the top level, an agent receives a goal, does work to try to solve it, then returns success or failure.
![][image1]
*Figure 1: A single Agent executing a state machine in a react loop.*

To solve the goal, an Agent goes through a Planning, Execution, Verification, Summarization loop. The results and a summary are returned to the caller. For simple problems, this can be solved by a ReAct loop. However, we want to scale to **larger problems** and **agents that learn over time**. Consequently, we propose to augment this in five ways: 1\) Memory, 2\) Active Context Engineering, 3\) Orchestration, 4\) Stable Environment, 5\) End-to-end optimization

### **Memory**

To enable learning over time, we enable Agents to create, read, update, and delete memories of different kinds:

- **Semantic memory**: Important entities and the relationships between them.
- **Episodic memory**: Important events and sequences of events.
- **Procedural memory:** Refined descriptions with workflows, root cause analysis, or solutions to common problems.

These memory stores are used by the active context management system to retrieve the most relevant information needed for a task. Agents will also have access to domain-specific memory tools like documents retrieved from Enterprise Content Intelligence systems. See MIRIX ([paper](https://arxiv.org/abs/2507.07957), [website](https://mirix.io/), [github](https://github.com/Mirix-AI/MIRIX)), Zep ([paper](https://arxiv.org/abs/2501.13956), [website](https://www.getzep.com/), [github](https://github.com/getzep/graphiti)).

### **Active Context Engineering**

*Given that LLMs are constrained by a finite attention budget, good context engineering means finding the smallest possible set of high-signal tokens that maximize the likelihood of some desired outcome. \- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)*

Active Context Management allows the Agent to manage its context window by pinning useful memory items when they are needed, and removing other information (see [MemGPT](https://arxiv.org/abs/2310.08560), [Letta](https://github.com/letta-ai/letta#)).

![][image2]
*Figure 2: Externalized memory feeds a structured context window including the agent’s prompt, goal, and FIFO message queue. The Working Context contains information important to the goal which is pinned to the context window.*

Curating this smallest-possible set of high-signal tokens is the goal of the Working Context structure (inspired by [MemGPT](https://arxiv.org/abs/2310.08560)). Under this model, the Agent’s context is structured into the following segments:

- **Prompt**: The Agent’s prompt.
- **Goal Description**: Taken from the goal. We’ll discuss this more in the Orchestration section below.
- **Working Context**: Entries pulled from the Agent’s memory or from domain-specific data sources needed to complete the goal (code snippets, RAG chunks, etc). It also includes the ReAct Agent’s To Do list, a plan to keep it on track.
- **FIFO Queue**: The standard message history, containing the trace of the ReAct agent’s progress, chatting, calling tools, reasoning about results. The FIFO queue can be dumped to a database and summarized to support long operation.

### **Orchestration**

We want to enable Agents to operate within larger, multi-step workflows, or to solve large problems that require different expertise. Therefore, we propose adding an orchestration layer.

When a goal is received by an Agent, it goes through the Plan, Execute, Verify, Summarize states. If the plan seems manageable by the single agent, it may just walk through these states in a ReAct loop. If that fails, or if the Goal seems too big for a single step, the Agent makes a plan which involves invoking subexperts to do part of the work then return the results \+ success or failure. This is inspired by [HiAgent](https://arxiv.org/pdf/2408.09559), but it mimics advice given [by Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (look for the heading Sub-agent architectures) and best practices by [expert users of AI Coding systems](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/ace-fca.md).

For example:

| `[Release Orchestrator: Release New '/user/profile' API Endpoint]  │  ├─> [1.0 Develop & Test Feature] -> [Dev Agent]  │   │  │   ├─> [1.1 Plan Endpoint] -> returns development plan & testing plan  │   │  │   ├─> [1.2 Develop Endpoint]  │   │   |  │   │   ├─> [1.2.1 Write Endpoint Interfaces]  │   │   │  │   │   ├─> (dependency 1.2.1) -> [1.2.2 Write Endpoint Logic]  │   │   │  │   │   └─> (dependency 1.2.1) -> [1.2.3 Write Unit & Integration Tests]  │   │   │  │   │   └─> (dependency 1.2.2, 1.2.3) -> [1.2.4 Run Unit & Integration Tests]  │   │  │   └─> (dependency 1.2) -> [1.3 Code Review, & Merge] -> returns "commit_hash"  │  │  ├─> (dependency 1.0) -> [2.0 Build & Scan Artifact] -> [CI/CD Agent]  │   │  │   ├─> [2.1 Build Docker Image from commit_hash] (runs in parallel with 2.2)  │   │  │   ├─> [2.2 Run Static Code Analysis] (runs in parallel with 2.1)  │   │  │   └─> (dependency 2.1, 2.2) -> [2.3 Push Secure Image to Registry] -> returns "image_tag"  │  │  └─> (dependency 2.0) -> [3.0 Deploy Release] -> [Ops Agent]      │      ├─> [3.1 Plan Deployment] -> returns deployment plan & testing plan      │      ├─> (dependency 3.1) -> [3.2 Deploy image_tag to Staging Environment]      │      ├─> (dependency 3.2) -> [3.3 Run Automated End-to-End Tests]      │      └─> (dependency 3.3) -> [3.4 Promote to Production] -> returns "Deployment_Complete"` |
| :---- |

*Figure 3: A hierarchical plan with pieces delegated to experts. The plan becomes a DAG with dependencies. Each item in the plan is executed by an Agent with a clean context.*

Note that in this model, both individual agents and sub-agent experts are orchestrated.

This should have the following benefits:

- **Agents don’t have to know everything**. They have a clear goal, and are given the information to tackle it. This opens the door to using smaller, cheaper models (see [SLM-Agents](http://research.nvidia.com/labs/lpr/slm-agents/))
- **There is a clear structure for planning, execution, and replanning**. Orchestration is part of the Agent’s stable environment, allowing it to stay on track, replan on failure, and manage context per task.
- **Hierarchical plans can be static, semi-static, or dynamic**, depending on what the application needs.

**Plans can be dynamic (LLM generated) or Static (workflows)**. The execution system should support both modes.

### **Stable Environment**

![][image3]
*Figure 4: The Stable Environment supporting Agentic Models includes orchestration, memory, context management, and a way to ask for help or more information.*

To get the most out of memory, context engineering, and orchestration, an Agentic LLM should be post-trained to operate with a stable, predictable set of Agent Tools. For the architecture above, this would include:

- **Orchestration Tool** items within the current plan to achieve its subgoal.
- **Memory Tool**. Store important information to retrieve later.
- **Manage Working Context**. Pin information that is good to achieve the current subgoal.
- **Ask For Help** from the enclosing context. Either through an explicit ask for help, or by examining the calling context.

### **End-to-end Optimization**

By establishing a structured agent—one that effectively integrates memory, context management, and orchestration—we gain the capability to develop systematic tools for **end-to-end optimization on specific tasks**. This continuous process should refine all aspects of the agent based on real-world data, improving performance while minimizing costs. Imagine DSPy for the agent end-to-end ([website](https://dspy.ai/), [video](https://www.youtube.com/watch?v=I9ZtkgYZnOw))

Key areas of focus include:

- **Prompt Optimization**: Systematically refining prompts in real-world deployment through A/B testing, robust evaluation frameworks, and meticulous version control. The goal is to achieve an optimal balance of response quality, operational cost, and processing latency.
- **Memory and Context Optimization:** Refining storage and retrieval policies to improve reliability.
- **Orchestration Optimization**: Strategically streamlining complex workflows by decomposing them into highly granular, parallelizable tasks and eliminating any non-essential steps to enhance efficiency.
- **Tool Optimization**: Optimizing tools for use by LLMs ([Anthropic Blog](https://www.anthropic.com/engineering/writing-tools-for-agents))
- **Model Selection & Optimization**: Model selection and fine tuning to get the best performance at the lowest cost.

## **Open Questions**

- **How do we avoid over-engineering**? Are there simple data structures and interfaces which are adaptable to many problems and improve with data. There’s tension between orchestration \+ experts or an agent managing its tools and context window. Is the complexity of orchestration and experts really necessary? Or can we get the same results with just one agent?
- **What is the right structure and primitives for Working Context?**
  - Is there a benefit to aligning on a structured context format that encompasses orchestration, memory, and tools? What’s the right format for LLMs (json, markdown, xml).
  - Should we adopt a stack-based model with push/pop operations that allow Agents to address a subtask, then pop back to the enclosing scope?
  - Should we allow agents to pin context to orchestrated tasks so that they show up when working on that task and disappear when the task is completed?
  - **Memory management**. For each memory module and for the working context, there are probably three likely architectures to choose from:
1. **Agents Manage**: We could allow agents to manage memory directly through Create, Read, Update, Delete (CRUD) operations in on graph memory and to do CRUD operations on its Working Context. This is the approach taken by [MemGPT](https://arxiv.org/abs/2310.08560).
2. **Subagent Manages**: Have a dedicated subagent which manages the memory, probably running at a lower rate than the ReAct Agent. This is the approach taken by [Memory R1](https://arxiv.org/abs/2508.19828), which resembles a read/write Agentic RAG system.
3. **Hybrid**: Allow both modes simultaneously.
- **What is the right memory representation?** Anthropic has released a filesystem-based memory tool, but it’s likely that graph-based knowledge representations (see [getzep.com](http://getzep.com) and [Graphiti](https://github.com/getzep/graphiti)) will do a better job encoding complicated information.
- **How do we avoid compounding failures?** How to avoid cascading failures where individual tasks don’t have the right information? (see [Don’t Build Multi Agents](https://cognition.ai/blog/dont-build-multi-agents))
- **How do we optimize latency & cost**: How can we minimize cost, token use, and memory?

## **How to Make Progress**

Here’s a short sketch of a plan.

1. **Implement Memory and Orchestration**. Design and build a v0 system based on the plan above. Ensure that developer experience and traceability are first-class concerns.
2. **Implement benchmarks and use cases**. Identify key benchmarks, but also build some agents for the team to use daily.
3. **Perform root cause analysis** of current performance. Attribute failures to model capability vs. memory or orchestration structure. Create a cadence of develop, evaluate, root-cause, then plan next advances.
4. **Build the case for training Agentic LLMs**. Pinpoint the key model capabilities necessary and develop the data pipelines needed to push training upstream.
5. **Create playbooks** for building, evaluating, and improving Agents. Based on experience, collect best practices for building, evaluating, and maintaining agents.

**Potential Benchmarks**:

- [SWE-Bench-CL: Continual Learning for Coding Agents](https://arxiv.org/abs/2507.00014) – SWE Bench, but
- [GitHub \- snap-research/locomo](https://github.com/snap-research/LoCoMo)
- HELMET: [https://huggingface.co/blog/helmet](https://huggingface.co/blog/helmet)

## **Ideal Software**

This section collects ideas about what the ideal software would look like

1. Developer journey from prototype to production in the same framework
2. Great developer experience at every stage
3. Full observability down to the inputs and outputs of every model, orchestration step, and tool call
4. Reusability of agents and tools across applications
5. Ability to mock outputs that have side-effects in the real world (e.g. send an email).
6. Needs to support DSPy or DSPy style optimization over all parts of the system
7. Supports recording and replay.
8. Durable execution with retries, respawn on crash, etc.
9. \[Stretch Goal\] Key components not tied to framework.

## **Next Steps**

Decisions that need to be made:

1. **What do we base our development on?** [Nemo Synapse | Agent Development Kit Evaluation](https://docs.google.com/document/d/19v_ahj4sPQjNVIGHIy_knQEwCTQPBYlCpLbybD1xozI/edit?tab=t.0). Likely build our own orchestration and observability, use off the shelf memory, use LiteLLM for multi-provider support, support opaque execution nodes that come from existing frameworks..
2. **Architecture for Optimization**: Understand DSPy, programmable prompting, and optimization. Propose how to structure our work so that everything (prompts, memory, context management, orchestration) can be similarly optimized.
3. **Architecture for RL**: Understand the NVIDIA RL Pipeline. Propose how to structure our work so that everything (prompts, memory, context management, orchestration) can be RL’d to create Agentic Models with a stable environment.
4. **Choose Evaluation Datasets**: Choose 2 or 3 evaluation datasets to adopt.
5. **Choose Team Use Cases**: Choose 1 or 2 use cases that we will implement for the team to use daily.

## **References**

Ahn, M., & Dosovitskiy, A. (2025). *Memory R1: A unified framework for agent memory*. arXiv. [https://arxiv.org/abs/2508.19828](https://arxiv.org/abs/2508.19828)

Cognition AI. (2024, June 25). *Devin Sonnet 4.5 lessons and challenges*. Cognition Blog. [https://cognition.ai/blog/devin-sonnet-4-5-lessons-and-challenges](https://cognition.ai/blog/devin-sonnet-4-5-lessons-and-challenges)

Fan, J., Zhu, Y., & Fan, L. (2024). *SLM-agents: A framework for generalist agents with small language models*. NVIDIA Research. [https://research.nvidia.com/labs/lpr/slm-agents/](https://research.nvidia.com/labs/lpr/slm-agents/)

getzep. (n.d.). *graphiti: The Python Knowledge Graph library for LLM Applications*. GitHub. Retrieved October 6, 2025, from [https://github.com/getzep/graphiti](https://github.com/getzep/graphiti)

humanlayer. (2024). *Advanced context engineering for coding agents*. GitHub. Retrieved October 6, 2025, from [https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/ace-fca.md](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/ace-fca.md)

Lanham, T. (2024, June 26). *Writing great tools for AI agents*. Anthropic. [https://www.anthropic.com/engineering/writing-tools-for-agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

letta-ai. (n.d.). *Letta: An open source framework to build trustworthy AI agents*. GitHub. Retrieved October 6, 2025, from [https://github.com/letta-ai/letta](https://github.com/letta-ai/letta)

Liu, N. (2024, June 13). *Effective context engineering for AI agents*. Anthropic. [https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

Mirix AI. (n.d.). *Home*. Retrieved October 6, 2025, from [https://mirix.io/](https://mirix.io/)

Mirix-AI. (n.d.). *MIRIX: A knowledge-rich agent for financial information extraction and analysis*. GitHub. Retrieved October 6, 2025, from [https://github.com/Mirix-AI/MIRIX](https://github.com/Mirix-AI/MIRIX)

Packer, C., Fang, V., Patil, S. G., Lin, K., Wooders, S., & Gonzalez, J. E. (2023). *MemGPT: Towards LLMs as operating systems*. arXiv. [https://arxiv.org/abs/2310.08560](https://arxiv.org/abs/2310.08560)

Petrov, A., Svyatkovskiy, A., & Zorin, A. (2025). *SWE-Bench-CL: Continual learning for coding agents*. arXiv. [https://arxiv.org/abs/2507.00014](https://arxiv.org/abs/2507.00014)

snap-research. (2024). *LoCoMo: A benchmark for evaluating long-context foundation models on code tasks*. GitHub. Retrieved October 6, 2025, from [https://github.com/snap-research/LoCoMo](https://github.com/snap-research/LoCoMo)

Sun, Y., & Zhou, J. (2025). *MIRIX: A knowledge-rich agent for financial information extraction and analysis*. arXiv. [https://arxiv.org/abs/2507.07957](https://arxiv.org/abs/2507.07957)

Tunstall, L., & von Werra, L. (2025). *Unlocking the power of long-term memory for conversational AI*. arXiv. [https://arxiv.org/abs/2501.13956](https://arxiv.org/abs/2501.13956)

Wang, Y., Min, B., Zhang, Y., Chen, X., Wang, X., Wang, W. Y., & Wang, L. (2024). *HiAgent: A hierarchical agent for complex interactive tasks*. arXiv. [https://arxiv.org/abs/2408.09559](https://arxiv.org/abs/2408.09559)

Wu, S. (2024, July 11). *Don't build multi-agents*. Cognition Blog. [https://cognition.ai/blog/dont-build-multi-agents](https://cognition.ai/blog/dont-build-multi-agents)

Zep. (n.d.). *Zep: The long-term memory store for AI*. Retrieved October 6, 2025, from [https://www.getzep.com/](https://www.getzep.com/)

[^1]:  [*Effective context engineering for AI agents*](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
