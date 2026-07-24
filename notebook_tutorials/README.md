# Notebook Tutorials

These notebooks are the guided learning path for NOOA. They favor explanation,
inspection, and small experiments over compact examples.

Run them in order:

| # | Notebook | Story |
|---|---|---|
| 1 | `01_your_first_agent.ipynb` | Agents are Python objects: generation methods, methods as tools, typed returns, state, and the first trace-viewer hook. |
| 2 | `02_choosing_a_strategy.ipynb` | How to choose between single-shot structured prediction and CodeAct's iterative Python execution. |
| 3 | `03_codeact_tools_and_live_objects.ipynb` | How to design the Python surface CodeAct sees: helper methods over large hidden state. |
| 4 | `04_progressive_disclosure.ipynb` | How `doc()`, `@hidden`, underscores, `spec`, and hidden imports control visibility. |
| 5 | `05_context_management.ipynb` | Event history, static and dynamic context blocks, and letting the agent manage context. |
| 6 | `06_memory_and_summarization.ipynb` | Token-budget summarization for one run, and `nooa_memory` for cross-session facts. |
| 7 | `07_subagents_and_reflection.ipynb` | Small focused subagents, critique/revision loops, and parallel work. |
| 8 | `08_tracing_and_dev_viewer.ipynb` | JSONL tracing, `@no_trace`, the dev viewer, and combining exporters. |

For compact copy-paste runnable examples, use `examples/quickstart/`.
For deeper authoring reference material, use the `skills/nooa-*` documents.

`test.ipynb` is scratch work and is not part of the ordered tutorial path.
