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
| 5 | `05_two_agents_talking.ipynb` | The smallest multi-agent pattern: call one agent, pass its reply to another, and keep orchestration in plain Python. |

For compact copy-paste runnable examples, use `examples/quickstart/`.
For deeper authoring reference material, use the `skills/nooa-*` documents.
