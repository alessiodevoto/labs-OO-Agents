# NeMo OO Agents — Feature Inventory

An exhaustive, developer-facing inventory of what you can **build, configure, and extend** with NeMo OO Agents — every public capability across the framework and its packages, organized by capability area. It is a working reference for documentation planning: one place to see the whole surface and decide, per feature, whether and where to document it.

> **How to read this list.** Each bullet starts with the developer action (*Define…*, *Swap in…*, *Inspect…*) and names the real public symbol. Markers: **(custom)** = an extensibility point where you plug in your own implementation; **(opt-in)** = optional behavior you enable; *no marker* = free/default behavior.

## Packages at a glance

NeMo OO Agents is a single repository that ships as several lockstep packages:

- **`nemo-oo-agents`** — the core framework. Bundles the agent runtime, generation strategies, context blocks (`nemo_oo_agents.context_blocks`), the unified LLM client (`nemo_oo_agents.unifiedllm`), runtime documentation (`nemo_oo_agents.agentdoc`), storage/snapshots, events, summarization, multimodal, MCP, tracing, the viewer backend, the trace explorer, and NeMo Flow integration. Optional extras: `[tracing]`, `[viewer]`, `[mcp]`, `[nemo-flow]`.
- **`nemo-oo-agents-cli`** — the `nemo-oo` command, the agent TUI, and the `nemo-oo term` web terminal. Optional extras: `[datascience]`, `[web]`.
- **`nemo-oo-agents-benchmarks`** — pre-built benchmark agents and the `nemo-harbor` container runner.
- **`nemo-oo-agents-nvidia`** — opt-in NVIDIA-gateway model aliases.
- **`nat-oo-agents`** — bridge for running agents inside the NVIDIA Agent Toolkit (NAT).

> The evaluation harness itself (`eval_pipeline`) lives under `util/eval_pipeline`. Several things an earlier feature list treated as standalone packages — `unifiedllm`, `context-blocks`, `agentdoc`, `trace_explorer` — are now submodules of `nemo-oo-agents` (import as `nemo_oo_agents.<name>`).

## Table of contents

1. [Define Agents](#1-define-agents)
2. [Generation Strategies](#2-generation-strategies)
3. [Configuration, Setup & Credentials](#3-configuration-setup--credentials)
4. [Use Tools](#4-use-tools)
5. [Visibility, Introspection & agentdoc](#5-visibility-introspection--agentdoc)
6. [Skills & Libraries](#6-skills--libraries)
7. [Context Management](#7-context-management)
8. [Events & History](#8-events--history)
9. [Persistence & Snapshots](#9-persistence--snapshots)
10. [Summarization & Long Conversations](#10-summarization--long-conversations)
11. [Multimodal](#11-multimodal)
12. [Code Execution, Inspection & Debugging](#12-code-execution-inspection--debugging)
13. [LLM Client & Model Registry](#13-llm-client--model-registry)
14. [CLI, TUI & Web Terminal](#14-cli-tui--web-terminal)
15. [MCP Integration](#15-mcp-integration)
16. [NeMo Flow Integration & ATIF Export](#16-nemo-flow-integration--atif-export)
17. [Tracing / OpenInference Instrumentation](#17-tracing--openinference-instrumentation)
18. [Trace & Eval Viewer](#18-trace--eval-viewer)
19. [Trace Explorer](#19-trace-explorer)
20. [NVIDIA Agent Toolkit (NAT) Bridge](#20-nvidia-agent-toolkit-nat-bridge)
21. [Evaluation Framework](#21-evaluation-framework)
22. [Container-Side Benchmark Runner (Harbor)](#22-container-side-benchmark-runner-harbor)

---

## 1. Define Agents

*Ships in: `nemo-oo-agents`*

### Define an Agent

- **Subclass `Agent`** — define an agent as a Python class `class MyAgent(Agent, llm=my_llm)`; the metaclass `AgentMeta` wires up generation, tracing, and introspection at class-creation time (`agent.py`, `metaclass.py`).
- **Trigger LLM generation with an ellipsis body** — any `async def` whose body ends in `...` becomes an LLM-generated method; `has_ellipsis_body` detects this via AST so no decorator is needed (`ellipsis_detection.py`).
- **Mix deterministic and generated methods freely** — methods without `...` run as ordinary Python; methods with `...` are filled in by the LLM, and both coexist on one class (`docs/guides/prompt-mechanics.md`).
- **Write pure-Python orchestrators** — a class that only sequences calls to real agents needs no `Agent` subclass at all; subclass only when the class itself has `...` methods or needs framework features (`docs/guides/single-vs-multi-agent.md`).
- **Apply `AgentMeta` to non-Agent classes** — the metaclass works on any class (e.g. strategy classes), auto-wrapping ellipsis/async methods and tracing, independent of `Agent` (`metaclass.py`). **(custom)**
- **Override the system prompt** — subclass and override `_system_prompt()` to replace the default identity/truncation-convention system block (`agent.py`). **(custom)**

### Prompt from Docstrings

- **Use the docstring as the prompt** — a generation method's docstring is sent to the LLM as the task USER message; there is no separate template system (`docs/guides/prompt-mechanics.md`).
- **Template with `{expression}` placeholders** — `{param}`, `{self.attr}`, `{doc(self)}`, `{pformat(self)}`, even `{len(self.tools)}` are evaluated against call args and runtime state via `expand_variables` (`runtime/actor.py`).
- **Prefill setup code before the `...`** — statements written between the docstring and the ellipsis are extracted by `get_pre_ellipsis_code` and injected as prefill so the LLM continues from your code (`ellipsis_detection.py`, `prompts.py`).
- **Force structured output via return type** — annotating the return type (e.g. a Pydantic model) makes the strategy validate the LLM output against that contract (`docs/guides/writing-generation-methods.md`).

### Reserved Parameters

- **Add `reasoning` for chain-of-thought** — including a `reasoning` parameter is reserved and rejected by `_validate_reserved_parameters` for normal use; the runtime instead surfaces a `reasoning()` builtin/prefill so the model emits its thinking (`metaclass.py`, `strategies/prefill.py`).
- **Use `message` for multi-turn replies** — the `message()` builtin lets a generation method send messages back to the caller; it is provided by `PurePythonStrategy` (`strategies/pure_python.py`), not the default CodeAct strategy.

### Compose and Nest Agents

- **Nest agents inside generated code** — instantiate another `Agent` inside a method/`execute_python()`; each subagent is fully isolated (own context, events, history) (`docs/guides/single-vs-multi-agent.md`).
- **Cascade the LLM to children** — omit `llm` and `_resolve_llm` resolves it from instance → class MRO → parent agent via `_parent_agent_var`, so nested agents inherit the parent's client (`agent.py`).
- **Pass different LLMs per subagent** — set `llm=` per subagent class/instance to route cheap models to classification and strong models to implementation (`docs/guides/single-vs-multi-agent.md`). **(opt-in)**
- **Call generation methods from each other** — generated methods can `await self.other_method(...)`, composing multiple LLM tasks within one agent (`docs/guides/writing-generation-methods.md`).

### Standalone Generation Functions

- **Define agent-less generation functions** — decorate an `async def` with no `self` with `@strategy(...)`; `create_standalone_wrapper` runs it on a fresh per-call stub agent with no shared state or history (`standalone.py`, `decorators.py`). **(opt-in)**
- **Scope context on standalone functions** — pass `ScopedContext(context=..., events=...)` and `llm=` through `@strategy` to supply per-call context blocks and a client (`standalone.py`). **(opt-in)**

### Configure the Agent Class

- **Set class-level config via subclass kwargs** — `class MyAgent(Agent, llm=..., truncation=..., execution=..., context=..., event_query=...)` is handled in `__init_subclass__` (`agent.py`). **(opt-in)**
- **Override config per instance** — `__init__` accepts `llm`, `truncation`, `render_config`, `context`, `event_query`, and `storage`, merging/overriding class-level values (`agent.py`). **(opt-in)**
- **Opt out of tracing with `@no_trace`** — decorate any method to suppress its span while still allowing generation; works in either order with `@strategy` (`metaclass.py`). **(opt-in)**
- **Override generation strategy with `@strategy`** — attach a `GenerationStrategy` (plus optional `context`, `llm`, `truncation`) to a single method; default is `CodeActStrategy` (`decorators.py`). **(opt-in)**

### Inspect Prompts and Introspection

- **Print the exact prompt with `print_prompt`** — render the system/task/prefill messages for a bound method and real args without an LLM call (`prompts.py`).
- **Build prompt data programmatically with `build_prompt_data`** — returns a `PromptData` dataclass (system_prompt, task_prompt, inspect_prefill, pre_ellipsis, strategy_name, method_path) for assertions/debugging (`prompts.py`).
- **Expose the auto-generated API via `doc(self)`** — `Agent.__type_info__` and `__instance_values__` implement the agentdoc protocol, filtering `@hidden`/private members so `doc()` shows the LLM the agent's methods and fields (`agent.py`).
- **List every method with `list_methods`** — `agent.runtime.list_methods()` returns a dict mapping each method name to metadata (type generator/implemented/generated, ellipsis flag, signature, docstring, strategy, has_code) for introspection/debugging (`runtime/actor.py`).

## 2. Generation Strategies

*Ships in: `nemo-oo-agents`*

### Built-in Strategies

- **Run code-and-iterate by default** — `CodeActStrategy` (the default when no `@strategy` decorator is present) gives the LLM an `execute_python` tool plus structured `return_result`, looping until it returns a value matching the method's return type.
- **Extract/classify in one shot** — `@strategy(PredictStrategy())` makes a single structured-output LLM call validated against the return-type annotation, retrying on parse/validation failure (no code execution). **(opt-in)**
- **Render templates without an LLM** — `TemplateStrategy` expands `{expression}` placeholders in a method's docstring via `runtime.expand_variables()`; it is non-traceable and lock-free, and underpins prompt-building inside other strategies. **(opt-in)**

### Configuring Strategy Behavior

- **Tune CodeAct via `CodeActConfig`** — pass `CodeActStrategy(config=CodeActConfig(...))` to set `max_iterations`, `max_retries`, `max_consecutive_text_only`, `cell_timeout`, `max_tokens`/`temperature`/`top_p`, `max_tool_calls`, and `translate_tool_calls`. **(opt-in)**
- **Choose how text-only stops are handled** — `CodeActConfig.text_only_stop_behavior` (`"return_result"` default vs `"synthetic_reasoning"`) controls whether a plain-text `finish_reason="stop"` is routed through `return_result()` validation or preserved as a no-op `reasoning()` call. **(opt-in)**
- **Tune Predict via `PredictConfig`** — pass `PredictStrategy(config=PredictConfig(...))` to set `max_retries`, sampling params, `max_error_chars`, `max_param_chars` (parameter-size guard, `None` disables), and `output_serialization` (`"event"` vs `"tool_call"`). **(opt-in)**
- **Restrict the code sandbox** — `CodeActConfig.restrictions` takes a `RestrictionsConfig` (`blocked_modules`, `blocked_calls`) that also filters which symbols appear in the execution-context block. **(opt-in)**
- **Merge partial configs** — `CodeActConfig.merge_with()` / `PredictConfig.merge_with()` overlay only the explicitly-set fields of another config onto a base. **(opt-in)**

### Selecting & Overriding Strategies

- **Override per method** — `@strategy(strategy_instance, context=ScopedContext(...), llm=..., truncation=...)` swaps the strategy, scoped context/events, LLM, and truncation config for a single generation method (stacking multiple `@strategy` decorators raises). **(opt-in)**
- **Override the global default** — `set_default_strategy(strategy)` sets the default for all agents in the current async context (call with `None` to reset); `get_default_strategy()` returns it or a fresh `CodeActStrategy()`. **(opt-in)**

### Code-Execution Tools & Builtins

- **Run a Python cell** — the `execute_python(code)` tool runs code in a persistent Jupyter-style REPL with parameters preloaded as locals and state carried across cells.
- **Submit the final answer** — `return_result(value)` (or keyword form) ends the session and validates against the return type; it is also callable from inside `execute_python()` to compute and return in one tool call.
- **Record hidden reasoning** — the `reasoning(text)` builtin logs chain-of-thought into events without surfacing it to the caller.
- **Auto-inspect inputs on turn one** — `InspectInputsPrefill` (the default `CodeActConfig.prefill`) emits prefill code that `pprint`s every parameter and `doc()`s the return type within truncation limits.
- **Translate stray tool calls** — when `CodeActConfig.translate_tool_calls` is on, an unknown tool call the LLM emits is rewritten into equivalent `execute_python()` code instead of erroring.

### Building Custom Strategies

- **Subclass the strategy ABC** — implement `GenerationStrategy.execute(runtime, call)` and override `name`, `traceable`, `requires_lock`, `get_block_overrides()`, and `get_block_order()` to control prompt blocks and tracing. **(custom)**
- **Compose existing strategies** — subclass `CompositeStrategy` and delegate to sub-strategies via `@strategy`-decorated methods; for wrapped base strategies that must share the current lock/session, call `runtime.execute_nested(strategy, call)`. **(custom)**
- **Use runtime services** — strategies receive the `RuntimeServices` protocol (`generate()`, `execute_code()`, `execute_nested()`, `expand_variables()`, `get_generation_id()`, plus `agent`/`event_manager`/`truncation_config`) as their execution interface. **(custom)**
- **Read the call snapshot** — each `execute()` gets a frozen `CurrentCall` (method name, signature, docstring, args/kwargs, return type, pre-ellipsis code, per-parameter specs) with helpers `format_parameters_as_code()`, `format_signature()`, and `bound_parameters()`. **(custom)**
- **Plug in a custom prefill** — supply any object implementing the `Prefill` protocol (`get_code(call, config) -> str | None`) as `CodeActConfig.prefill`, or `None` to disable prefill. **(custom)**
- **Swap the error formatter** — pass `CodeActStrategy(error_formatter=...)` (any object with `format(error, code) -> str`) to customize the error feedback shown to the LLM. **(custom)**

### Experimental Strategies

- **Opt into unmaintained strategies** — `PurePythonStrategy`, `CodeActLiteStrategy`, and `ReflexionStrategy` are importable from `nemo_oo_agents.experimental`; instantiating any of them emits a `FutureWarning`. The top-level `nemo_oo_agents.CodeActLiteStrategy` / `nemo_oo_agents.ReflexionStrategy` names are routed through the same warning factories, so they warn on instantiation too. **(opt-in)**
- **Run raw-Python generation** — `PurePythonStrategy(max_iterations=..., max_retries=..., prefill=...)` executes the LLM's raw code output directly and exposes a `message(text)` builtin for caller-facing messages. **(opt-in)**
- **Render clean CodeAct turns** — `CodeActLiteStrategy` subclasses CodeAct to scope events to the current call and render messages as plain text without XML/type wrappers. **(opt-in)**
- **Reflect and retry** — `ReflexionStrategy(base=..., config=ReflexionConfig(max_iterations=...))` wraps a base strategy with a self-reflection loop. **(opt-in)**

## 3. Configuration, Setup & Credentials

*Ships in: `nemo-oo-agents` · `nemo-oo-agents-cli`*

### Layered config files (the "one config story")

- **Discover any config file across layers** with `layered_paths(filename, env_var, prepend=...)` — returns existing YAML paths lowest-priority-first across bundled defaults → user (`~/.config/nemo_oo/`) → project (`<root>/.nemo_oo/`) → comma-separated env-var override, with resolved-path dedup keeping the highest slot.
- **Deep-merge layered YAML into one dict** with `load_layered_yaml(filename, env_var, prepend=...)` — last-wins merge where `null` deletes a key and a non-mapping layer is skipped with a warning.
- **Locate the user config dir** with `get_user_dir(*parts)` — `~/.config/nemo_oo/` (honors `XDG_CONFIG_HOME`), overridable with `NEMO_OO_USER_DIR`.
- **Locate the project config dir** with `get_project_dir(*parts)` — nearest-ancestor `<root>/.nemo_oo/` (walks up to `pyproject.toml`), overridable with `NEMO_OO_PROJECT_DIR`; `find_project_root()` exposes the walk.
- **Override file resolution from the shell** with `NEMO_OO_LLM_CONFIG`, `NEMO_OO_SETTINGS`, `NEMO_OO_SECRETS` — comma-separated YAML paths, highest priority, no file edits needed. **(opt-in)**

### LLM registry config (llm_config.yaml)

- **Build the LLM-registry config chain** with `llm_config_chain()` — bundled defaults → user → project → `NEMO_OO_LLM_CONFIG`, fed to `reload_registry(*llm_config_chain())`.
- **Ship default model aliases from an external package** by registering a zero-arg `Path`-returning callable under the `nemo_oo_agents.bundled_configs` entry-point group; `bundled_config_paths()` collects them (e.g. `nemo-oo-agents-nvidia`). **(custom)**
- **Read a typed model alias** with `get_model_config(name)` → `ModelConfig`, or `ModelConfig.from_registry(name, raw)`; `ModelConfig` types `model_name`/`api_base`/`api_key_env`/`client_type`/`context_window`/`max_tokens`/`temperature`/`top_p` and is `extra="allow"`+`frozen` for litellm passthrough.
- **Reload the registry from explicit or discovered paths** with `reload_registry(*paths)` (or no args to re-discover), with idempotent auto-load via `ensure_loaded()`.

### Secrets (secrets.yaml → env)

- **Load API keys from `secrets.yaml` into `os.environ`** with `load_secrets_into_env()` — non-clobbering (a shell `export` always wins), idempotent, returns the names it actually set; schema is a single `env:` name→value map.
- **Inspect resolved secrets without leaking values** via `Secrets` — values are `pydantic.SecretStr`, masked on `repr`/`str`/`model_dump`; call `.get_secret_value()` for plaintext.

### Resolved view (programmatic `config show`)

- **Get the fully resolved config as typed objects** with `resolved_config()` → `ResolvedConfig{models, secrets, settings, sources}`, where `sources` reports the winning file paths per layer (the "which file is winning?" answer).

### Per-class / per-instance / per-method selection

- **Pick the LLM at class level** with `class MyAgent(Agent, llm=llm)`, **at instance level** with `MyAgent(llm=...)`, or **per method** with `@strategy(..., llm=...)`; omitting `llm` cascades instance → class MRO → runtime parent agent (`_resolve_llm`).
- **Set framework execution guards per class** with `class MyAgent(Agent, execution=ExecutionConfig(max_nesting_depth=5))` — caps agent-in-agent recursion depth (default 10).
- **Set truncation config at class or instance level** with `Agent(..., truncation=...)` / `class MyAgent(Agent, truncation=...)`, **or per method** with `@strategy(..., truncation=...)`; levels are merged (default → class → instance, later wins) via `_resolve_truncation`.
- **Override context blocks and the default event query** at class or instance level with the `context=` / `event_query=` Agent kwargs.

### Config dataclasses (typed, frozen, mergeable)

- **Tune CodeAct execution** with `CodeActConfig` knobs: `max_iterations`, `max_retries`, `max_consecutive_text_only`, `text_only_stop_behavior`, `cell_timeout`, `max_tokens`, `temperature`, `top_p`, `max_tool_calls`, `translate_tool_calls`, `restrictions`, `prefill`.
- **Swap in a turn-1 prefill plugin** via `CodeActConfig.prefill` — defaults to `InspectInputsPrefill()`, set `None` to disable, or pass any object with a `get_code(call, config=None)` method. **(custom)**
- **Tune Predict extraction** with `PredictConfig` knobs: `max_retries`, `max_tokens`, `temperature`, `top_p`, `max_error_chars`, `max_param_chars`, `output_serialization`.
- **Cap output size at render time** with `TruncationConfig` — top-level `max_context_tokens`, `max_event_tokens`, `min_preserved_events`, `response_reserve_tokens`, plus sub-configs `capture` (`CaptureConfig`: `max_stdout`/`max_stderr`/`max_error`/`tail`/`file_backed`), `media_capture` (`MediaCaptureConfig.max_attachments_per_execution`), and `event_format`/`prefill_format`/`context_block_format` (`FormatConfig`: `max_string`/`max_length`/`max_depth`).
- **Restrict agent code execution** with `RestrictionsConfig` — `blocked_modules` (hard block, stripped + AST-denied), `blocked_calls` (per-module function/method denylist), `restricted_imports` (AST-denied soft block); flip the process-global default with `set_restricted_imports()` / inspect via `get_restricted_imports()`. **(opt-in)**
- **Configure the BashTool** with `BashConfig` — `default_timeout`, `use_sandbox`, `srt_settings`, `srt_executable`.
- **Configure summarizers** with `TokenBudgetConfig` (`max_tokens`/`preserve_recent`/`target_chars`) and `MethodSummarizerConfig` (`min_events`/`exclude_root`/`target_chars`).
- **Layer selected configs field-by-field** with `merge_with(other)` — supported by generation/truncation/doc configs; only fields in `other.model_fields_set` override, and guards reject `model_dump()`/`model_validate()` round-trips where applicable. `TruncationConfig.merge_with` additionally merges sub-configs field-by-field.

### Install, setup & TUI settings

- **Bootstrap a managed install** with `scripts/install.sh` (`curl … | sh`) — installs `uv`, a managed Python ≥3.12, and `nemo-oo-agents-cli` (+ core + nvidia aliases) as a uv tool; tunable via `NEMO_OO_INSTALL_REPO`/`_REF`/`_SECRETS_FILE`/`_REINSTALL`/`_NONINTERACTIVE`.
- **Capture credentials at install time** — `install.sh` prompts (via `/dev/tty`) for `NVIDIA_INTERNAL_API_KEY` and writes a chmod-600 `~/.config/nemo_oo/secrets.yaml`; skip with `NEMO_OO_INSTALL_NONINTERACTIVE=1`. **(opt-in)**
- **Set up a dev checkout** with `setup.sh` — `uv sync --all-extras` + `pre-commit install`.
- **Auto-load secrets on every CLI run** — the CLI preloads `secrets.yaml` into the env on each invocation (and the TUI bootstrap reloads the registry from `llm_config_chain()`); no shell-rc edit needed.
- **Inspect and edit the LLM config chain** with `nemo-oo config show` (per-layer presence, alias counts, dedup notes, referenced `api_key_env` vars + whether set, plus settings/secrets layers), `nemo-oo config path`, and `nemo-oo config eject [--force]` (copies bundled defaults to the user path; refuses on multiple providers / symlink / dir targets).
- **View, set, and inspect TUI settings** with the in-TUI `/config` skill — `/config show|set|libs|skills|path`; `/config set` writes the project `settings.yaml` with friendly key aliases (`model`→`default_model`, `python`→`show_python`, `vi`→`vi_mode`, `trace`→`trace_dir`).
- **Configure the TUI declaratively** with layered `settings.yaml` (`Config`/`TUIConfig`/`AgentConfig`/`SummarizationConfig`) loaded via `Config.load(**overrides)` — defaults → layered file → CLI flags; round-trips through `dump_settings`/`load_settings`, with a commented `SETTINGS_TEMPLATE` scaffold on first run.

## 4. Use Tools

*Ships in: `nemo-oo-agents`*

### Persistent shell + editable-grep (ShellTools)

- **Attach a persistent shell** — `ShellTools(cwd=...)` (a `Skill`, registered as the builtin `nemo.shell` entry point) keeps `cd`, `export`, env vars, and cwd alive across calls via an internal `BashSession`. **(opt-in)**
- **Run commands with stdin and timeout** — `await self.shell.run(command, stdin=..., timeout=30.0)` returns a `ShellResult` (a `str` subclass whose value is stdout, plus `.stdout`/`.stderr`/`.returncode`/`.success`); `stdin=` feeds a payload via a base64 tempfile so scripts need no heredoc quoting.
- **Edit straight off a grep hit** — when `run()` is a pure search (bare `grep`/`rg`/`egrep`, no mangling pipe or anchor-dropping flag) `ShellResult.matches` carries verified `Match` objects you pass to `replace()`; fail-closed (`None`) on any ambiguity, cross-checked against `rg --json`.
- **Read a file region as a Match** — `await self.shell.read(path, lines=(start, end))` returns a `Match` with `.text`, `.numbered`, `.path`, `.start`, `.end`, sliceable by 1-indexed line range.
- **Replace by anchor or unique string** — `await self.shell.replace(match, new_text)` edits a `Match`'s line region, or `replace(path, old, new)` edits a uniquely-matching string (errors on 0 or >1 matches).
- **Overwrite a file without quoting** — `await self.shell.write_file(path, content)` creates/overwrites and reports line count; both write forms return a `FileWrite` with a diff/message.

### Web rich-output publisher

- **Render charts and rich content inline** — `WebPublisher` (`self.web`) pushes content to the `nemo-oo term` web panel via `plot(fig)`, `markdown(text)`, `html(html)`, `image(src)`, `json(data)`, and `clear()`; fire-and-forget, silently skipped if no browser. **(opt-in)**
- **Persist and replay rich output** — published items are stored as `RichOutput` events on the agent's `EventManager` and replayed on session resume (`--continue`); `WebPublisher.attach()` wires the event manager automatically.

### Skill / library authoring tools

- **Scaffold, lint, hot-reload skill libraries** — `SkillWriting` (`self.libs`) provides `create(name, description)`, `path(lib, rel)`, `list()`, `reload(name)` (lints via `SecurityValidator`, returns a formatted `LintReport` string, then hot-reloads through the `SkillRegistry`), `repo_tree()`, and `run_tests(name)`. **(opt-in)**
- **Teach REPL-cell helper authoring** — `MethodWriting` (`self.writing`) is a doc-only skill whose docstring shows the LLM how to define plain helpers and `@strategy(...)` ellipsis sub-call functions at the top of a CodeAct cell. **(opt-in)**

### In-memory todo tracker

- **Plan and track multi-step work** — `TodoManager` (`self.todo`, a `@snapshotable` `Skill`) offers `add`/`get`/`done`/`reopen`/`remove`/`clear`/`update`, dependency edges (`add_dep`/`remove_dep`, computed `blocked` status), per-todo vars (`set_var`/`get_var`/`del_var` or attribute proxy `t.v.x`), `comment`/`comments` journalling, `list_todos(status=...)`, and `status()`. **(opt-in)**
- **Survive turns and snapshots** — todo state serializes via `to_dict`/`from_dict`, persists across calls and snapshots, and auto-publishes a `todo_status` context block to the LLM.

### Legacy file/bash tools

- **Run a one-shot bash command** — `BashTool(working_dir=..., config=BashConfig(...))` with `await self.bash.run(command, timeout=, working_dir=)` returns a `BashResult` (`stdout`/`stderr`/`return_code`/`success`/`sandboxed`); kills the whole process group on timeout. **(opt-in)**
- **Tune bash behavior via BashConfig** — knobs `default_timeout` (30s), `use_sandbox`, `srt_executable`, and `srt_settings` control timeout and the optional SRT sandbox; `sandbox_available` reports SRT presence. **(opt-in)**
- **Do gitignore-aware file ops** — `FileTool(bash)` (`self.files`) wraps a `BashTool` for `read`, `write`, `edit_file` (fuzzy-match diagnostics + py syntax check), `list`, `exists`, `find` (rg `--files`, gitignore-aware), and `grep` (rg with grep fallback), all returning `FileResult` (adds `.lines`). **(opt-in)**

### Showing media & wrapping objects as tools

- **Show media inside a CodeAct cell** — `show(obj)` from `nemo_oo_agents.runtime.media_capture` lets agent code surface an `Image`/`Audio`/`Video`/`File` (or PIL image / matplotlib `Figure`, auto-converted to PNG) into the LLM's next turn via a task-local media buffer, capped by `MediaCaptureConfig.max_attachments_per_execution`.
- **Wrap any object as a discoverable tool** — `Skill(obj)` adopts an arbitrary object's docstring/`dir()` so its methods appear in `doc(self.<skill>)`; assign it as an agent attribute (e.g. `self.pd = Skill(pd)`) to expose third-party libraries. **(custom)**
- **Expose user-typed slash commands from a skill** — `@slash_command(name, argument_hint=, completions=, user_only=)` on a `Skill` method registers a `/name` action whose return value is injected as a prompt; discovered automatically on skill activation/reload. **(custom)**

## 5. Visibility, Introspection & agentdoc

*Ships in: `nemo-oo-agents` (`nemo_oo_agents.agentdoc`)*

### Visibility Model

- **Hide a method or module-level function** — decorate with `@hidden` (the `nemo_oo_agents.hidden` singleton, also re-exported from `nemo_oo_agents.agentdoc`); works on plain methods, `property`, and `cached_property`.
- **Hide a field or module variable** — annotate with `Annotated[T, hidden]`; excluded from `doc()`, `pformat()`, and exec_globals.
- **Hide a block of imports/unannotated names** — use `with hidden:` at module level; every name bound inside the block is recorded in `_agentdoc_hidden_names` and stripped from agent globals.
- **Hide via spec when you also need metadata** — `@spec(hidden=True)` or `Annotated[T, spec(hidden=True)]` hides while letting you attach a description/expand hint in one call.
- **Opt a `_private` name back into docs** — `@spec(hidden=False)` (or `spec(self, "field", hidden=False)`) force-shows an underscore-prefixed method/field that would otherwise be hidden by the `_` convention.
- **Rely on the default rule** — public names are visible, single/double-underscore names are hidden, dunders and `_agentdoc_*` internals are always stripped.
- **Use the no-op `visible` context manager** — `with visible:` exists purely for backward compatibility (everything is visible by default) and does nothing.

### Authoring Render Metadata with spec()

- **Attach metadata three ways** — `spec(**kwargs)` returns a `SpecAnnotation` usable as an `Annotated[T, spec(...)]` marker or a `@spec(...)` decorator; passing a positional target (`spec(Cls, "field", ...)`) mutates types you don't own in place.
- **Describe a field or method** — `spec(description="...")` renders as an inline `#` comment in `doc()` output.
- **Collapse a sub-type to a one-liner** — `@spec(expand=False)` (or `Annotated[T, spec(expand=False)]`) renders the type as `ClassName()` instead of expanding it inline.
- **Force concise docstrings for a type** — `spec(concise=True)` shows only first-line docstrings for that type in `doc()`.
- **Override pformat bounds per annotation** — `spec(max_length=…, max_string=…, max_depth=…)` on a field annotation overrides container/string/depth limits for just that value.

### Rendering the API Contract — doc()

- **Render a prompt-ready API contract** — `doc(obj)` (in `nemo_oo_agents.agentdoc`) formats a class, function, module, or instance; types show default field values, instances show current values.
- **Document several objects with dedup** — `doc(A, B, fn)` or `doc([A, B])` flattens documentable collections and emits each referenced type exactly once under a `## Referenced Types` section.
- **Control referenced-type expansion** — `inline_depth` (0 = none, 1 = direct refs default, 2+ = transitive; `None` = auto from `concise`) governs how deep nested types expand.
- **Show first-line docstrings only** — `doc(obj, concise=True)` trims docstrings and defaults `inline_depth` to 0.

### Lower-Level Introspection

- **List method signatures only** — `introspect.methods(obj, detail="summary"|"full")` emits `def`/`async def` signatures (plus docstrings in full mode), respecting `@hidden` and the `_` convention.
- **List field values only** — `introspect.variables(instance)` emits non-callable attributes with current values and drill-down hints, respecting `@hidden`, `Annotated[T, hidden]`, and `_` prefixes.
- **Mirror doc()'s filtering in your own framework** — `nemo_oo_agents.agentdoc.visibility.filter_module_globals(module)`, `is_hidden_field(cls, name)`, `is_hidden_method(func)`, and `is_hidden_module_variable(module, name)` reproduce the exact visibility decisions for building exec_globals or prompt payloads.
- **Tune doc/introspection formatting** — pass a frozen `DocConfig` (`max_value_chars`, `max_list_items`, `hidden_prefixes`, `hidden_names`, `include_types`/`include_docstrings`/`include_hints`) to override defaults.

### Pretty-Formatting with Truncation

- **Format an object as a string** — `pformat(obj, …)` is a Rich-`pformat`-compatible drop-in that auto-excludes hidden fields and honors `@spec(expand=False)`; knobs include `max_length`, `max_string`, `max_depth`, `expand_all`, `concise`, `instance_mode` ("repr"/"type"), `unquote_strings`.
- **Print to stdout with truncation** — `pprint(obj, …)` is the Rich-`pprint`-compatible streaming variant (also re-exported via `nemo_oo_agents.runtime.pprint` for agent-generated code).
- **Hard-cap total rendered size** — `truncating_pformat(obj, max_chars=…)` routes non-strings through `pformat` into a `TruncatingStringIO` so huge objects can't OOM; strings pass through verbatim.
- **Bound output with head+tail notices** — `TruncatingStringIO(limit, tail_chars)` keeps a verbatim head plus rolling tail and emits a `<truncated-output>` prose notice when the budget is exceeded.
- **Spill full output to a temp file** — `FileBackedTruncatingStringIO(...)` streams the complete output to disk and embeds the file path in the truncation notice (`file_path`, `close()`, `cleanup()`).

### Custom Extractors & Extension Points

- **Plug in a custom type/module extractor** — `@spec.define_doc(Type)` (or `(module)`) completely replaces default introspection, returning a `TypeInfo`/`(TypeInfo, values)` or `ModuleInfo` **(custom)**.
- **Register extractors via the low-level registry** — `nemo_oo_agents.agentdoc.ext` exposes `register_type_info_extractor` (the `@spec.define_doc(Type)` backend), `get_type_info_extractor` (MRO-aware lookup), `unregister_type_info_extractor`, and `clear_registry`. Module-level extractors (`register_module_info_extractor`) live in `nemo_oo_agents.agentdoc.registry` and are normally reached via `@spec.define_doc(module)` **(custom)**.
- **Look up a registered extractor** — `ext.get_type_info_extractor(obj)` returns the MRO-aware extractor for an object's type (or `None`), the introspection-time counterpart to `register_type_info_extractor`. **(custom)**
- **Build Info objects directly** — `ext.TypeInfo`, `FieldInfo` (with `repr=False` to drop a field), `CallableInfo`, `ModuleInfo`, and the `REQUIRED` sentinel are the composable structures extractors return **(custom)**.
- **Override extraction on the object itself** — implement the `SupportsTypeInfo` (`__type_info__`), `SupportsCallableInfo` (`__callable_info__`), or `SupportsInstanceValues` (`__instance_values__`) protocols; detect via `ext.has_type_info()` **(custom)**.
- **Reuse the built-in extraction helpers** — `ext.extract_type_info`, `extract_callable_info`, `extract_module_info`, and `format_type` expose the default introspection machinery.
- **Adapt third-party libraries** — import `nemo_oo_agents.agentdoc.adapters.plotly` / `.pandas` for curated `doc()` output, or call `adapters.register_all()` to register every installed adapter **(opt-in)**.

## 6. Skills & Libraries

*Ships in: `nemo-oo-agents`*

### Authoring skills

- **Subclass `Skill` for a custom capability** — a docstring (the written guide) plus public methods the LLM discovers via `doc(self.<skill>)`; attached as an agent attribute. **(custom)**
- **Wrap a third-party object with `Skill(obj)`** — registers any library object for discovery (`__dir__` forwards to the wrapped object), optionally naming it via `name=`. **(custom)**
- **Wrap inline text with `Skill(content=...)`** — turns a raw string into a skill whose docstring is that content, no Python object required. **(custom)**
- **Load a SKILL.md directory with `TextSkill(path=...)`** — Claude-Code-compatible SKILL.md (YAML frontmatter + body); exposes `id`, `description`, `source_dir`, `run_script()`, and `read_file()`. **(opt-in)**
- **Run bundled scripts with `TextSkill.run_script(name, *args, interpreter=, timeout=)`** — executes any `scripts/<name>` via shebang or explicit interpreter in a `BashSession` subprocess rooted (cwd) at the skill dir; the requested script path is resolved with path-escape protection.
- **Read skill assets with `TextSkill.read_file(path)`** — returns any file within the skill directory, with path-escape protection.
- **Declare skill dependencies with `requires`** — a tuple of skill names auto-loaded transitively (cycle-safe) when the skill is activated. **(opt-in)**
- **Attach a dynamic context block with `context_block`** — `(key, expr)` pair registered as a dynamic context entry on activate and removed on deactivate. **(opt-in)**
- **Hook setup/teardown with `attach(agent)` / `detach()`** — override to wire up the agent reference (e.g. grabbing `event_manager`) when installed or removed. **(custom)**
- **Locate a skill's own code via `source_dir`** — returns the directory of the skill's source so an agent can view or edit its implementation.

### Discovering, loading & activating skills

- **Access the per-agent registry as `self.skills` (`SkillRegistry`)** — instantiated by the host (`self.skills = SkillRegistry(self)`); runs the three-stage discover → load → activate lifecycle.
- **Discover entry-point skills automatically** — every package advertising the `nemo_oo_agents.skills` entry-point group is found at construction; `discovered()` lists them. **(custom)**
- **Scan skill directories with `discover_skills_dirs(dirs)`** — registers SKILL.md dirs as `cmd.<id>` TextSkills and `.py` files (with a `Skill` subclass) as `ext.<name>`.
- **Scan a libs tree with `discover_libs(libs_path)`** — imports each subdir containing a `pyproject.toml`, reading its registry name from the pyproject entry-point or falling back to `local.<lib_name>`.
- **Load skills by glob with `self.skills.load([patterns])`** — fnmatch patterns (`'nemo.*'`, `'*'`) instantiate and attach skills as `self.<leaf_name>`; `loaded()` lists them.
- **Register a constructed skill with `self.skills.register(name, cls_or_instance, **kwargs)`** — three modes: load class from entry point with kwargs, explicit class + kwargs, or a pre-built instance. **(custom)**
- **Activate skills with `self.skills.activate([patterns])`** — auto-loads if needed, resolves `requires` deps transitively, unhides the attr to the LLM, registers context blocks, and refreshes slash commands; `activated()` lists them.
- **Deactivate with `self.skills.deactivate([patterns])`** — hides the skill from the LLM and tears down its context block while leaving it loaded.
- **Access skills by namespace or leaf** — `self.skills['cat.name']`, `self.skills.cat.name` (dotted namespace proxy), or `self.skills.name` (bare leaf).
- **Render the live skills panel via `self.skills.status()`** — the `skills` dynamic context block listing active tools and available-but-inactive skills (excludes `cmd.*`).
- **Hot-reload skills with `await self.skills.reload(name=None)`** — re-imports a skill (or all) from disk; user/lib skills get a full package purge, builtin tool skills under `nemo_oo_agents` take a safe single-module reload; accepts FQ name, glob, or unambiguous leaf.
- **Extract a `Skill` from a module with `skill_from_module(module, name)`** — resolution order: module-level `skill` instance, first local `Skill` subclass, then re-exported subclass.

### Slash commands

- **Mark a method as a user command with `@slash_command(name, ...)`** — turns a Skill method into a `/name` action; options: `argument_hint`, `user_only`, `completions`, `output_to_agent`. **(custom)**
- **Route results with `output_to_agent`** — `True` feeds the command's return into the agent as a turn via the `slash_commands` queue; `False` shows output to the user only without spending a turn.
- **Restrict commands to the human with `user_only=True`** — prevents the LLM from invoking the command (e.g. destructive ops). **(opt-in)**
- **Offer tab-completion via `completions=(...)`** — subcommand names surfaced by the TUI; also auto-derived from a leading `Literal[...]` parameter through `derive_completions()`.
- **Bind typed args with `parse_typed_args(method, raw_args)`** — shlex-splits and coerces arguments to the method's annotations (`int`, `float`, `bool`, `Literal`, `Optional`/unions), falling back to the legacy single `args: str` passthrough; `CoercionError` carries a usage hint.
- **Carry command output with `SlashCommandResult`** — dataclass (`command`, `args`, `value`, `text`, `output_to_agent`) whose `__str__` renders the agent-facing prompt.
- **Enumerate a skill's commands with `get_slash_commands(skill)`** — returns `(meta, bound_method)` pairs for every `@slash_command` method.
- **Define commands declaratively in SKILL.md** — frontmatter `user-invocable` (default true), `install-as`, `argument-hint`, `allowed-tools`, `metadata`, parsed leniently to match Claude Code's behaviour.

### Persistent libraries

- **Manage a libs directory with `LibraryManager.install(agent, libs_dir=)`** — scans each `pyproject.toml` package, imports it, and attaches the exported `Skill` (or a `Skill(module)` fallback) as `self.<lib_name>`. **(opt-in)**
- **List libraries without loading via `LibraryManager.discover(path)`** — returns sorted package names under a path.
- **Hot-reload every library with `LibraryManager.reload()`** — cache-busts `sys.modules` and re-attaches each installed library, refreshing slash commands.

### Agent-facing skills

- **Author libraries with the `SkillWriting` skill (`nemo.libwriting`)** — scaffolds a package (`create`), resolves paths (`path`), lints + hot-reloads (`reload`), lists (`list`), shows the tree (`repo_tree`), and runs pytest (`run_tests`); requires `nemo.shell`. **(opt-in)**
- **Lint library code on reload** — `SkillWriting.reload` parses every non-`__init__.py` file with the `SecurityValidator` (`library_writing_lib.py`); E001 (forbidden builtins) is a hard error that blocks the reload, all other findings (e.g. E003 `from ... import *` / forbidden dunder access) are reported as warnings via a `LintReport`. (No restricted/blocked import list is passed, so E002 does not fire on this path.)
- **Teach helper/sub-call patterns with the `MethodWriting` skill (`nemo.methodwriting`)** — a docstring-only guide on defining plain helpers and `@strategy(...)` ellipsis sub-functions at the top of a REPL cell. **(opt-in)**
- **Publish rich web output with the `WebPublisher` skill (`nemo.web`)** — `self.web.plot/html/image/markdown/json/clear` push inline content to the `nemo-oo term` web panel; fire-and-forget, persisted as `RichOutput` events for session replay. **(opt-in)**

## 7. Context Management

*Ships in: `nemo-oo-agents` (`nemo_oo_agents.context_blocks`)*

### Managing context blocks (`self.context`)

- **Set a static (cacheable-prefix) block** — `agent.context_manager.set_static(key, value)` places a once-computed value in the cacheable prefix. The LLM-facing `self.context` (`ContextApi`) has no `set_static`; assignment via `self.context[key] = value` goes to the volatile partition (see next bullet).
- **Set a dynamic block** — register a per-turn expression with `self.context.set_dynamic(key, "expr")`, or store a plain value with the keyword form `set_dynamic(key, value=...)`; `self.context[key] = value` is shorthand that routes into the dynamic (volatile) partition.
- **Read / check / remove blocks** — use the dict-like `ContextApi`: `self.context[key]`, `.get(key, default)`, `key in self.context`, `del self.context[key]`, `.pop(key, default)`, `.keys()`, `len()`, iteration (protected framework blocks like `system_prompt`/`self`/`state` stay hidden from iteration and cannot be mutated).
- **Mark a block as cacheable vs volatile** — `BlockMetadata.static` declares stable content for prefix caching; `CachedBlockFormatter` partitions static blocks into the system prefix and wraps volatile blocks in a trailing `<context>` user message so the event tail stays byte-stable for provider prompt caching.
- **Expose the API to the LLM** — `self.context` is hidden by default; opt in with `spec(self, "context", hidden=False)` in `__init__` so it appears in `doc(self)`.

### Temporary scoped overrides

- **Override blocks within a scope** — `ScopedContext(context={...})` as a `with` block applies static/`DynamicContext`/removal (`None`) overrides for all LLM calls inside it; nested scopes inherit and override the parent **(opt-in)**.
- **Filter events within a scope** — pass `ScopedContext(events=EventQuery(...))` to replace the event list shown in context with a filtered subset **(opt-in)**.
- **Scope an ellipsis method** — supply `ScopedContext(...)` as a second argument to `@strategy(...)` since `...`-bodied methods cannot use a `with` block **(opt-in)**.

### Assembly & rendering pipeline

- **Render blocks to provider output** — `render_context(blocks, block_formatter=..., provider_formatter=..., context_limit=..., count_tokens=..., event_format=..., event_format_resolver=..., model_context_window=...)` partitions by role, pre-serializes non-tool events, evicts over-budget context blocks in place (no event truncation), and returns a `RenderResult(output, stats, messages)` NamedTuple.
- **Describe a resolved block** — `ResolvedBlock(key, content, role, metadata, event)` carries pre-evaluated content, a `Role`, typed `BlockMetadata`, and the original `EventBase` for event blocks.
- **Mark dynamic expressions** — `DynamicContext("expr")` wraps a Python expression, validating its syntax at construction (raises `BlockSyntaxError`).
- **Carry typed block metadata** — `BlockMetadata` exposes `expr`, `tag`, `truncated`, `user_block`, `static`, and `source_dynamic` flags that drive truncation order and formatter behavior.
- **Configure the formatter pair** — `RenderConfig(block_formatter=..., provider_formatter=...)` selects formatting; defaults to `CachedBlockFormatter` + `OpenAIProviderFormatter`.
- **Re-use stock message wrapping** — `format_message_content(block, format_type)` applies the XML/Markdown/plain event wrapper outside the render pipeline.

### Formatters (extensibility points)

- **Plug in a block formatter** — subclass `BlockFormatter` (implement `format_type`, `format`, optional `format_event`/`format_description`) to control how blocks become a neutral `list[RenderedMessage]` **(custom)**.
- **Choose XML or Markdown wrapping** — `XMLBlockFormatter` and `MarkdownBlockFormatter` are stock block formatters; `FormatType` / `FORMAT_XML` / `FORMAT_MARKDOWN` / `FORMAT_PLAIN` identify the format for block-level truncation.
- **Enable cache-aware assembly** — `CachedBlockFormatter` (the default) splits static prefix from a volatile `<context>` suffix to maximize provider prompt-cache hits.
- **Plug in a provider formatter** — subclass `ProviderFormatter` to reshape `RenderedMessage`s into a target wire format **(custom)**.
- **Target a provider wire format** — stock `OpenAIProviderFormatter` (Chat Completions `list[dict]`), `AnthropicProviderFormatter` (`{"system", "messages"}`), and `ResponsesProviderFormatter` (OpenAI Responses API) handle tool calls, tool results, and LiteLLM-shaped images.
- **Emit block-aware message parts** — `RenderedMessage.parts` (`TextPart` / `BlockPart` discriminated union) lets block-aware formatters expose per-block boundaries for journal/trace content-addressing; `ToolCallInfo` carries structured tool-call payloads.

### Truncation & utilization

- **Cap total context tokens** — `TruncationConfig.max_context_tokens` evicts over-budget system blocks at assembly, dropping `user_block` (non-static) blocks first then others from the end, replacing content with an `EVICTED: over context budget` notice (requires a `count_tokens` counter) **(opt-in)**.
- **Declare an event-token budget** — `TruncationConfig.max_event_tokens`, with `min_preserved_events`/`response_reserve_tokens` knobs, currently only surfaces in `ContextWindowStats` utilization display; event-history eviction is not yet wired into the render path.
- **Bound per-value rendering** — `FormatConfig(max_string, max_length, max_depth)` sets `pformat` bounds, applied separately as `event_format`, `prefill_format`, and `context_block_format` (the latter unlimited by default for author-curated `self.context` values).
- **Inspect window utilization** — `ContextWindowStats` reports `context_blocks_tokens`/`count`, `events_tokens`/`count`, `total_tokens`, configured limits, `context_blocks_dropped`, `events_dropped`, `context_utilization`/`event_utilization` properties, and a human-readable `format()` summary.

### Typed events & metadata

- **Use stock event types** — `UserEvent`, `AssistantEvent`, and `ToolCallEvent` (with nested `ToolResult` carrying `result_status`) make up the `Event` union; `EventStatus` and `ResultStatus` are the status enums.
- **Define custom event types** — subclass `EventBase`; `event_type` auto-derives from the class name and the class auto-registers in the global event registry for backend deserialization **(custom)**.
- **Attach stored metadata** — subclass `Metadata` (`extra="allow"`, `Role.METADATA`) to persist arbitrary structured data that is auto-registered, never shown to the LLM, and excluded from the core `Event` union **(custom)**.
- **Control LLM-visible event fields** — `repr=False` fields are excluded from rendering and `__instance_values__` suppresses empty/`None` fields (preserving meaningful `0`/`False`) when events are serialized via `pformat`.
- **Map messages to provider roles** — the `Role` enum (`SYSTEM`/`USER`/`ASSISTANT`/`TOOL`/`RUNTIME_EVENT`/`METADATA`) governs partitioning and provider formatting; `RUNTIME_EVENT`/`METADATA` roles are dropped from provider output.

### Error signals

- **Catch block errors** — `BlockError` (base), `BlockSyntaxError` (invalid `DynamicContext` expression), `DynamicNotResolvedError` (reading a dynamic block before its first resolution), and `ProtectedBlockError` (mutating a framework-protected block) are the public exception types.

## 8. Events & History

*Ships in: `nemo-oo-agents`*

### Querying & Compacting History (EventsApi)

- **Query past events** — `self.events.query(type=, call_id=, query=, regex=, limit=)` returns matching events in chronological order with AND semantics across all filters; the LLM-facing read-only view of conversation history.
- **Look up an event by tag** — `events["5"]` / `events["2..40"]` returns an event by its stable string tag (or UUID), raising `KeyError` if missing; `events[["2","3"]]` fetches a list.
- **Get an event safely** — `events.get(key)` returns the event (or list) by tag/uuid, returning `None` (or filtering out missing) instead of raising.
- **Test for an event** — `"5" in events` checks tag/uuid existence via `__contains__`.
- **List active tags** — `events.keys()` returns the current ordered tags the LLM sees (e.g. `["1", "2..40", "41"]`), reflecting collapses.
- **Compact history manually** — `events.collapse(start_tag, end_tag, summary_text=None)` archives a tag range into a single `Summary` marker, returning the range tag; `None` text truncates, text summarizes. Originals stay reachable via `summary.children_tags`.
- **Expose the query API to the LLM** — `self.events` (an `EventsApi`) is always present but hidden by default; opt in with `spec(self, "events", hidden=False)`. **(opt-in)**

### Scoping What the LLM Sees (EventQuery)

- **Declare an event filter** — `EventQuery(type=, call_id=, query=, regex=, limit=)` is a frozen filter config; `call_id="current"` resolves to the active method call.
- **Scope to the current call** — `EventQuery.current_call(limit=)` shows the LLM only events from the executing method's call. **(opt-in)**
- **Filter by event type** — `EventQuery.by_type(event_type, limit=)` factory restricts context to one event type. **(opt-in)**
- **Keep only recent events** — `EventQuery.last_n(n)` factory caps context to the last N events. **(opt-in)**
- **Set a class/instance default filter** — pass `event_query=` to `class MyAgent(Agent, event_query=...)` or `MyAgent(event_query=...)`; instance overrides class. **(opt-in)**
- **Filter per method** — `@strategy(ScopedContext(events=EventQuery.current_call()))` applies a query for one generation method. **(opt-in)**
- **Filter for a block** — `with ScopedContext(events=...)` applies a query for a scoped region. **(opt-in)**
- **Override the filter at runtime** — `event_manager.set_event_query(query)` (clear with `None`); `get_event_query()` reads it. Effective precedence is runtime > scoped > decorator > agent > unfiltered. **(opt-in)**

### Subscribing & Recording (EventManager)

- **Subscribe to events** — `event_manager.on(event_type, handler)` registers a fire-and-forget observer (use `"*"` for all types) and returns an idempotent unsubscribe callable. **(custom)**
- **Subscribe to concrete framework events** — the runtime emits typed `EventBase` subclasses (`events.py`) observable via `event_manager.on("<TypeName>", fn)`: lifecycle pairs `BeforeAgentCall`/`AfterAgentCall`, `BeforeTurn`/`AfterTurn`, `LLMCallStart`/`LLMCallEnd`, the metrics event `LLMComplete` (tokens/cost/tool_calls), `SystemPrompt`, conversation events (`Task`, `Message`, `Reasoning`, `Error`, `Feedback`, `LLMOutput`, `PythonOutput`), `Summary`, and TUI session events `TuiSessionResumed`/`TuiSessionCleared`. **(opt-in)**
- **Signal that something happened** — construct a `Notification(source=, description=)` (`events.py`) and `event_manager.add(...)` it to surface an asynchronous signal (queue input, job completion, timer, webhook) into the LLM context; `source` is a namespaced string the outer dispatcher keys off. **(custom)**
- **Record an event** — `event_manager.add(event, record=True)` emits to subscribers and stores it with an auto-assigned tag, auto-tagging with the current `call_id`; `Role.RUNTIME_EVENT` events are emitted but never recorded.
- **Register a custom event type** — `event_manager.register_event_type(cls)` registers an `EventBase` subclass so persistent backends can reconstruct it on read. **(custom)**
- **Mutate stored events** — `event_manager.update(key, **fields)`, `remove(key)`, and `clear()` edit/delete recorded events by tag/uuid without re-emitting.
- **Inspect the store** — `event_manager.filter(...)`, `get(key)`, `items()`, `keys()`, `values()`, `len()`, and `manager["5"]` provide the full backend-facing query/iteration surface that `EventsApi` wraps.
- **Collapse a range** — `event_manager.collapse(start_tag, end_tag, summary_text=None)` archives events into a `Summary` and emits it to `"Summary"` subscribers.
- **Inspect a code-execution result** — `ExecutionResult` (`events.py`) carries `stdout`/`stderr`/`error`/`signal`/`returned_value`/`explicit_return`/`images` with `.success`, `.has_return`, `.has_method(name)`, and `.format_output(fenced=)` for formatting captured output.

### Event-Driven Input (Channels)

- **Feed the agent via named channels** — `QueueManager` (`runtime/channels.py`) registers `Channel`s in queue or event mode; `channel.put(item)` emits a `Notification` (queue mode) or a `QueueOutput` event (event mode) so the LLM is told new input arrived, and `QueueManager.spawn(...)` runs background jobs surfaced as `JobHandle`/`JobError`. **(opt-in)**

### Intercepting Live Operations (Middleware)

- **Wrap a lifecycle operation** — `event_manager.intercept(kind, fn)` registers async middleware `(ctx, nxt) -> ctx` that can transform inputs, modify outputs, or block; registration order = execution order (first = outermost), returns an unsubscribe callable. **(custom)**
- **Intercept the three middleware kinds** — `"agent_call"`, `"llm_call"`, `"execute_python"` (constants `MIDDLEWARE_AGENT_CALL` / `MIDDLEWARE_LLM_CALL` / `MIDDLEWARE_EXECUTE_PYTHON`) wrap the whole method, the LLM round-trip, and code execution respectively. **(custom)**
- **Read/write typed middleware context** — `AgentCallContext`, `LLMCallContext`, `ExecutePythonContext` (with their `*Middleware` / `*Next` type aliases) expose mutable `messages`/`code`/`params` and settable `result`/`response` fields for guardrails. **(custom)**

### Instrumentation Hooks

- **Install instrumentation hooks** — `set_hooks(hooks)` registers an `InstrumentationHooks` implementation (per async context); `set_hooks(None)` removes it and `get_hooks()` reads the current one. **(custom)**
- **Implement lifecycle callbacks** — `InstrumentationHooks` is a runtime-checkable Protocol with before/after pairs for agent calls, generation, code execution, method invocation, tool execution, plus `on_messages_built`; before-hooks return a context object threaded to their after-hook, and hook exceptions are swallowed so they never break execution. **(custom)**

### Storage Backends

- **Swap the event backend** — `event_manager.set_backend(backend)` replaces persistence while preserving handlers and middleware; backends implement the `EventBackend` protocol. **(custom)**
- **Persist events to SQLite** — `SQLiteEventBackend` stores events in SQLite tables (vs the default `InMemoryBackend`), with `register_event_type` for round-tripping custom event classes. **(opt-in)**

## 9. Persistence & Snapshots

*Ships in: `nemo-oo-agents`*

### Storage backends

- **Persist agents to SQLite** — pass `SQLiteStorageManager(db_path)` (defaults to `":memory:"`) as `Agent(storage=...)`; it stores events, the active-tag order, and snapshots in stdlib `sqlite3` tables with no extra dependencies. **(opt-in)**
- **Run with no persistence** — the default `InMemoryStorageManager` keeps events in an `InMemoryBackend` and raises `StorageNotConfiguredError` on `save_snapshot`/`restore_snapshot`, so "no storage" is a uniform code path rather than a special case.
- **Plug in a custom backend** — implement the `StorageManager` `Protocol` (`@runtime_checkable`): expose an `event_backend` property plus `save_snapshot(agent) -> str` and `restore_snapshot(snapshot_id, agent)`, then pass it as `storage=`. **(custom)**
- **Clean up storage deterministically** — use `SQLiteStorageManager` as a context manager (`__enter__`/`__exit__`) or call `close()` to commit and release the connection and session lock.
- **Share the DB connection across threads** — pass `check_same_thread=False` to `SQLiteStorageManager` when all access is serialized through one asyncio loop (the TUI relies on this); the default `True` enforces stdlib sqlite3's single-thread check. **(opt-in)**

### Saving & restoring snapshots

- **Save a snapshot** — `storage.save_snapshot(agent)` extracts state via `AgentSnapshot.from_agent`, writes a row to the `snapshots` table, and returns a UUID `snapshot_id`.
- **Restore a snapshot** — `storage.restore_snapshot(snapshot_id, agent)` loads the row and applies it additively into a freshly constructed agent, raising `SnapshotNotFoundError` if the id is missing.
- **Resume the most recent session** — `restore_latest_snapshot(agent)` restores the newest snapshot (returns `False` if none exist); `get_latest_snapshot_id()` and `get_latest_snapshot_created_at()` inspect it without restoring.
- **Serialize an agent to a JSON dict** — `snapshot_to_json(agent)` returns a `json.dumps`-ready dict (shorthand for `AgentSnapshot.from_agent(agent).model_dump()`).
- **Restore an agent from a JSON dict** — `snapshot_from_json(snapshot, agent)` validates and applies the dict back onto an agent.
- **Work with the snapshot model directly** — `AgentSnapshot` (Pydantic, `version` = `SNAPSHOT_VERSION` = 2) carries `context`, `attributes`, and a `type_allowlist`; `from_agent`/`restore` convert to and from an agent; for plain-dict conversion of the model itself use `model_dump()`/`model_validate()`.

### What gets captured

- **Snapshot static and dynamic context blocks** — user context blocks round-trip as `StaticContextBlock` (serialized value) or `DynamicContextBlock` (expression string); framework/protected blocks are skipped on save and rebuilt via `set_static_protected`/`set_dynamic_protected` on restore.
- **Snapshot instance attributes** — all non-callable entries in `agent.__dict__` are captured, except `_agentdoc_*` internals; a single non-serializable attribute is logged and skipped rather than aborting the whole snapshot.
- **Keep durable agent vars snapshot-clean** — `SnapshotVars` (a `@snapshotable` `MutableMapping` backing per-todo `Todo.vars`, accessed via `t.v`) validates each write through `serialize()` and silently drops values that can't be persisted, so one bad value can't poison the snapshot.

### Controlling serialization

- **Exclude a field from snapshots** — annotate it `Annotated[T, nosnapshot]`; `is_nosnapshot_field` walks the MRO so subclasses can override parent annotations (orthogonal to LLM `hidden`). **(opt-in)**
- **Exclude a whole class from snapshots** — set `__nosnapshot__ = True` on the class so untyped/dynamic attributes of that type are skipped via `is_nosnapshot_value`. **(opt-in)**
- **Make a plain class snapshotable** — decorate it `@snapshotable` so `serialize()` round-trips it via `vars()`/`__init__` (`__snapshot_dict__` envelope), alongside built-in support for Pydantic models, dataclasses, enums, dicts, lists, and tuples. **(custom)**
- **Serialize/deserialize values directly** — `serialize(value) -> (blob, allowlist)` and `deserialize(blob, allowlist)` expose the 8-step isinstance dispatch; `SKIP` is the sentinel for nosnapshot values, and `deserialize` rejects any class FQN not in the captured `type_allowlist`.

### Persistent event storage

- **Stream events to durable storage** — every `StorageManager` owns an `event_backend` that the agent's `EventManager` writes through (store/get/get_by_id/update/remove/set_status/active_tags/all_events/find_tag), so swapping storage preserves subscribers and middleware on the stable `EventManager`.
- **Register custom event types for replay** — `SQLiteEventBackend.register_event_type(cls)` adds an `EventBase` subclass to the per-instance deserialization registry; `Metadata` subclasses also auto-register globally via `__pydantic_init_subclass__`, and unknown `event_type`s fall back to `Metadata`. **(custom)**
- **Drive the SQLite event store directly** — `SQLiteEventBackend` exposes the full EventBackend surface plus `clear()`, `allocate_next_tag()`, `max_tag_num()`, and `insert_active_tag()`/`remove_active_tag()` for managing the persisted active-tag order (`storage/sqlite.py`).
- **Survive flaky/corrupt storage** — the SQLite backend retries on `disk I/O error` (reconnecting the connection), skips corrupt rows with warnings, and detects virtiofs mounts to switch to `journal_mode=DELETE` + `synchronous=FULL` (otherwise WAL).
- **Guard against double-open sessions** — an on-disk DB takes an exclusive `flock` (`.lock` file recording the owner PID); a second process gets `SessionAlreadyActiveError` carrying `session_id` and `owner_pid`.
- **Catch serialization failures** — `SerializationError`, `DeserializationError`, `SnapshotNotFoundError`, and `StorageNotConfiguredError` (all subclasses of `NemoOOAgentsError`) signal the distinct failure modes.

## 10. Summarization & Long Conversations

*Ships in: `nemo-oo-agents`*

### Built-in Summarizers

- **Cap conversation history by real token usage** — install `TokenBudgetSummarizer` (from `nemo_oo_agents.agents`) to collapse the oldest events once the provider-reported prompt token count exceeds the budget, while keeping the most recent turns intact **(opt-in)**.
- **Summarize after each method call** — install `MethodSummarizer` to collapse all events belonging to a completed method invocation (including nested child calls) once it finishes **(opt-in)**.
- **Attach a summarizer to any agent** — call `TokenBudgetSummarizer.install(agent, config=...)` / `MethodSummarizer.install(agent, config=...)` (the classmethod is defined on the `SummarizationAgent` base in `agents/summarization.py`), which constructs the summarizer, wires it to the agent's `event_manager`, inherits the agent's LLM, and stores it on the agent (`agent._summarizers`) so its lifetime tracks the agent **(opt-in)**.

### Configuring Summarizers

- **Tune the token-budget trigger** — pass `TokenBudgetConfig(max_tokens=, preserve_recent=, target_chars=)` (from `nemo_oo_agents.config`) to set the prompt-token threshold, how many recent events to keep, and the summary's target length.
- **Tune the per-method trigger** — pass `MethodSummarizerConfig(min_events=, exclude_root=, target_chars=)` to set the minimum events before summarizing, whether to skip the top-level call, and the summary's target length.
- **Layer config overrides** — call `config.merge_with(other)` on either config to overlay only the explicitly-set fields of another frozen config instance.
- **Derive a budget from the model's context window** — call `context_budget(llm, percent=0.8, fallback=100_000)` to compute `max_tokens` as a fraction of the LLM's `context_window` (falling back to `context_limit`, then the fallback constant) **(opt-in)**.

### Custom Summarizers

- **Define your own summarization policy** — subclass `SummarizationAgent` and override `_should_summarize(event)` (when to summarize) and `_compute_range(event)` (which event tag range to collapse) **(custom)**.
- **Customize the summary prompt** — override the `summarize(history_markdown, target_chars)` generation method (a `PredictStrategy` method whose docstring drives the LLM) to change the produced summary structure **(custom)**.
- **Inspect raw events programmatically** — call `self._get_events_in_range(start_tag, end_tag)` inside generated/override code to retrieve `(tag, event)` tuples for a range before summarizing **(custom)**.

### How Collapsing Works

- **Collapse a range into a Summary marker** — the summarizer applies results via `EventManager.collapse(start_tag, end_tag, summary_text)`, replacing the range with a single `start..end` tag while keeping original events individually accessible.
- **Apply summaries safely at turn boundaries** — summaries are computed in a background `asyncio` task on `AfterTurn` and applied on the next `BeforeTurn`, so the LLM always sees a consistent collapsed event list.
- **Bound the summarizer's own input** — long ranges are head-dropped to ~70% of the summarizer model's context window (via `count_tokens`, falling back to a char-approximate counter) with an explicit omission marker so the single `summarize()` call always fits.

## 11. Multimodal

*Ships in: `nemo-oo-agents`*

### Media Types

- **Construct media from disk** — `Image.from_file()`, `Audio.from_file()`, `Video.from_file()`, and `File.from_file()` read bytes off a path and infer the MIME type via `mimetypes` (falling back to `application/octet-stream`).
- **Construct media from raw bytes** — `Image.from_bytes()`, `Audio.from_bytes()`, `Video.from_bytes()`, `File.from_bytes()` wrap bytes plus an explicit `media_type` into a base64 data URL.
- **Construct media from a URL** — `Image.from_url()`, `Audio.from_url()`, `Video.from_url()`, `File.from_url()` reference a remote URL with no download (passed straight to the LLM); each subclass supplies a sensible default `media_type` (`image/jpeg`, `audio/wav`, `video/mp4`, `application/pdf`).
- **Attach provider-specific hints** — pass extra `**vendor_metadata` kwargs to any constructor (e.g. `Image.from_url(url, detail="high")`) which merge into the emitted image content block.
- **Inspect media payloads** — read `.data_url`, `.media_type`, `.modality`, `.vendor_metadata`, `.content_hash` (8-char SHA-256), and `.size_bytes` (None for URL refs) on any `Media` instance.
- **Subclass the Media base** — extend `Media` for a custom modality by overriding the `_modality` class attribute; unknown subclasses fall back to an `image_url` content block. **(custom)**

### Showing Media in CodeAct

- **Show media mid-cell** — call `show(media)` inside an `execute_python` cell on any `Image`/`Audio`/`Video`/`File` to append a content block the LLM can perceive on the next turn; prints `[shown: ...]`.
- **Auto-convert PIL and matplotlib** — `show()` also accepts a `PIL.Image.Image` or `matplotlib.figure.Figure` and renders it to a PNG `image_url` block via lazy imports.
- **Use media types in generated code** — `show`, `Media`, `Image`, `Audio`, `Video`, and `File` are pre-injected into the CodeAct exec namespace so generated code can construct and display media without imports.

### Passing Media as Method Arguments

- **Type a method parameter as media** — annotate a generation-method parameter as `Image`/`Audio`/`File` and pass an instance; the framework routes it to the LLM as a content block on the `Task` event.
- **Auto-show media params under CodeAct** — the inputs-inspection prefill calls `show(param)` for any `Media`-typed argument so the model perceives it before generating code (other params are `pprint`-ed).
- **Attach media params under PredictStrategy** — `PredictStrategy` collects every `Media` bound parameter via `media_to_content_block()` and attaches the blocks to the single-shot `Task` event.

### Attachment Capping & Config

- **Cap attachments per cell** — `MediaCaptureConfig.max_attachments_per_execution` (default 5) bounds how many `show()` blocks a single `execute_python` cell keeps; the cap is per-cell, not per-turn or per-run. **(opt-in)**
- **Observe spillover** — once the cap is hit, further `show()` calls in that cell are dropped and `[show() limit reached (N), attachment not added]` is printed to stdout for the LLM to see.
- **Wire the cap through TruncationConfig** — set `media_capture` on `TruncationConfig` (validated to be > 0, merges field-by-field via `merge_with()`); both `MediaCaptureConfig` and `TruncationConfig` are exported from `nemo_oo_agents.config`. **(opt-in)**
- **Convert a Media object to a content block** — call `media_to_content_block(media)` to produce LiteLLM-universal blocks (`image_url` / `input_audio` / `video_url` / `file`) that LiteLLM maps to provider-native formats.

## 12. Code Execution, Inspection & Debugging

*Ships in: `nemo-oo-agents`*

### AST Safety & Code Validation

- **Block dynamic execution and namespace escapes** — `SecurityValidator` rejects `exec`/`eval`/`compile`/`__import__`/`input`/`breakpoint`/`globals`/`locals`/`vars`/`exit`/`quit`, dunder access (`__class__`, `__subclasses__`, `__globals__`, `__builtins__`, ...), `object`/`type`/`super().__dunder__`, `from ... import *`, and `setattr`/`getattr`/`delattr` with dunder names (error codes E001–E104).
- **Reject blocking calls that freeze the event loop** — `BlockingCallValidator` resolves AST names against `exec_globals` and flags blocked-module calls and partial blocked calls like `time.sleep`, `os.system`, `Thread.join`, `asyncio.run`, including chained and aliased calls and locals tracked from constructors (error code E310).
- **Enforce REPL coding conventions** — `REPLPolicyValidator` flags missing `await` on async self-methods (E301) and `while True` loops without break/return/raise (W303); enabled via `include_repl_policy=True`. **(opt-in)**
- **Prevent class-level corruption** — `ClassAssignmentValidator` blocks `ClassName.method = ...`, `type(self).attr = ...`, and `setattr(ClassName, ...)` that would mutate shared class state across instances (E401/E402).
- **Prevent return-type shadowing** — `ReturnTypeShadowValidator` rejects local `class`/`def` definitions whose name matches the executing method's declared return type, which would break `return_result()` Pydantic identity checks (E501).
- **Reject dynamic method addition on agents** — `guard_dynamic_method` raises `DynamicMethodAdditionError` when generated code attaches a callable to a public agent attribute (`self.foo = lambda ...`, `setattr(self, 'foo', fn)`).
- **Run all validators through one entry point** — `UnifiedCodeValidator` (and the `validate_code()` convenience wrapper) orchestrates the validators, stops on first error by default, and formats IPython-style `Cell In[N]` errors with source line, caret, and fix hint.
- **Patch cross-block deadlock risks at runtime** — `agent_async_safety_context()` wraps `concurrent.futures.Future.result`/`exception`/`wait`/`as_completed` to raise instead of deadlocking when called from the event-loop thread during agent execution.

### Error Formatting & Taxonomy

- **Turn tracebacks into model-readable feedback** — `format_error_for_llm(error, code, line_offset=...)` (and the `IPythonErrorFormatter` class) rewrites exceptions into `Cell In[N], line X` form, strips framework frames, and appends targeted hints (bash-heredoc-in-string detection, and the callable's agentdoc signature on bad-call `TypeError`s).
- **Catch failures by category** — the `nemo_oo_agents.errors` taxonomy roots at `NemoOOAgentsError`, with `GenerationError`/`GenerationAborted`, `ValidationError`/`RestrictedCodeError`/`XMLFormatError` (carrying `line_number`/`original_exception`), `NemoOOAgentsRuntimeError`, and `DynamicMethodAdditionError`.

### Configurable Execution Restrictions

- **Tune blocked/restricted imports per strategy** — `RestrictionsConfig` (frozen Pydantic model) defines a three-tier model: `blocked_modules` (hard block + stripped from `exec_globals`), `blocked_calls` (specific calls on allowed modules), and `restricted_imports` (deny-list at import validation), embedded in `CodeActConfig.restrictions`. **(opt-in)**
- **Start from curated defaults** — `DEFAULT_BLOCKED_MODULES`, `DEFAULT_BLOCKED_CALLS`, `DEFAULT_RESTRICTED_IMPORTS` (empty), and the stricter `RESTRICTED_MODULES` superset are exported constants you can compose. **(opt-in)**
- **Override import restrictions process-wide** — `set_restricted_imports(frozenset|None)` / `get_restricted_imports()` set a global deny-list applied to every subsequently constructed `RestrictionsConfig`; these symbols are themselves forbidden from agent code. **(opt-in)**
- **Match modules against deny lists** — `match_blocked_module()` and `is_from_blocked_module()` resolve a module/object name against a lookup including parent-module matching.

### REPL Persistence & Output Access

- **Persist variables and imports across cells** — the actor captures `__repl_captured_locals__` from each `__repl_wrapper__` run so names and imports defined in one execution remain available in the next.
- **Pre-load common names into agent code** — `exec_globals` ships `self`, `asyncio`, `typing` (+`Annotated`/`Any`/`Literal`/`Optional`/`Union`), `doc`/`methods`/`variables`, `pprint`, `show`/`Media`/`Image`/`Audio`/`Video`/`File`, strategy classes, and the `strategy` decorator; `help` is shadowed by `doc` to avoid blocking on stdin.
- **Access prior results Jupyter-style** — `OutAccessor` exposes `Out[n]` (by execution count, sparse-safe), `Out[-n]` (from end), `Out.last`, `len(Out)`, and `in`, backed by `PythonOutput` events.
- **Pretty-print with truncation** — `pprint()` offers a Rich-compatible API (`max_length`, `max_string`, `max_depth`, `expand_all`) for large structures in generated code.
- **Signal non-error control flow** — `ExecutionSignal` (subclass of `BaseException`, so `except Exception` won't swallow it) is the base for control-flow signals like `return_result()`'s internal `_ReturnResultSignal`, distinguished from errors by the actor.
- **Register cells for readable tracebacks** — executed code is registered in `linecache` under `Cell In[N]` filenames so tracebacks and the debug dump show real source lines.

### Inspect Prompts Without Running

- **Print the exact prompt without an LLM call** — `print_prompt(method, *args, **kwargs)` renders the system prompt, task prompt, and prefill for a bound generation method using real agent state and arguments, writing plain text to stdout.
- **Get structured prompt sections** — `build_prompt_data(method, *args, **kwargs)` returns a frozen `PromptData` (`system_prompt`, `task_prompt`, `inspect_prefill`, `pre_ellipsis`, `strategy_name`, `method_path`); `render_prompt_data()` formats it.
- **Inspect context window utilization** — `agent.context_stats` returns the latest `ContextWindowStats` (token/block counts, per-category limits, `context_utilization`/`event_utilization`, evicted/dropped counts, `.format()`), or `None` before first generation.

### Debugging, Logging & Activity Tracking

- **Dump live state on SIGUSR2** — `install_debug_handler(dump_dir=None)` (auto-installed on import) registers a SIGUSR2 handler that writes traceback, all registered Cell code, pending LLM calls, and LLM-in-stack detection to `debug_dump_<pid>.txt`; `dump_debug_info()` does it programmatically.
- **Track in-flight LLM calls** — `llm_call_context(model, prompt_tokens, endpoint, **meta)` / `register_llm_call` / `unregister_llm_call` / `get_pending_llm_calls()` record pending model calls for debug dumps. **(opt-in)**
- **Track in-flight code executions** — `code_exec_context()` / `register_code_exec` / `get_pending_code_execs()` record running cells, and `get_activity()` reports the primary phase (`executing_python`/`waiting_llm`/`idle`) for the `/activity` command.
- **Drive activity tracking from events** — `attach_activity_tracking(event_manager)` subscribes `LLMCallStart`/`LLMCallEnd` observer hooks so activity reflects in-flight calls; returns an unsubscribe callable. **(opt-in)**
- **Enable library logging quickly** — `enable_logging(level, name, fmt, datefmt, stream)` attaches a single `StreamHandler` to the `nemo_oo_agents` logger hierarchy; idempotent and scope-narrowable by logger name. **(opt-in)**
- **Auto-attach OTel tracing on first agent** — the first `Agent` construction calls `_try_auto_enable_tracing()`, which enables OTLP export when the viewer is reachable; per-agent opt-out via `_enable_tracing`.
- **Capture debug-only trace events** — the `DebugTrace` event records diagnostics into traces without showing them to the LLM (METADATA role).

## 13. LLM Client & Model Registry

*Ships in: `nemo-oo-agents` (`nemo_oo_agents.unifiedllm`) · `nemo-oo-agents-nvidia`*

### Creating & Routing Clients

- **Create a client by model string** — `get_llm_client(name, *, client_type=None, **overrides)` resolves a registry alias or passes the name straight through to litellm, which routes OpenAI / Anthropic / Google / Azure / Bedrock / NVIDIA NIM and any common provider.
- **Override registry defaults per call** — pass `**overrides` (e.g. `max_tokens=`, `temperature=`, `api_key=`) to `get_llm_client()`; explicit kwargs win over both YAML defaults and an alias's `api_key_env`.
- **Construct clients directly** — instantiate `CompletionClient(model=..., **config)` or `ResponsesClient(...)` yourself, bypassing the registry, for full control over litellm params (**custom**).
- **Select the Responses API path** — set `client_type="responses"` (or `client_type: responses` in YAML) to get a `ResponsesClient` backed by `litellm.responses()` instead of chat completions (**opt-in**).
- **Strip `<think>` reasoning tags** — use `ReasoningCompletionClient` for NIM/Nemotron-style models that emit `<think>...</think>`; it moves the thinking text into `LLMResponse.reasoning` and returns clean content (**opt-in**).
- **Call sync or async** — every client implements `call()` and `acall()` returning a standardized `LLMResponse`; pass `tools=` and `output_model=` to either.
- **Force structured output** — pass a Pydantic `output_model`; the client builds a provider-appropriate `response_format`/`text.format` (strict when the schema allows, non-strict fallback otherwise) and parses+validates the result.

### Model Registry (YAML config chain)

- **Register custom model aliases in YAML** — declare `models:` entries (`model_name`, `api_base`, `api_key_env`, `context_window`, `temperature`, `top_p`, `max_tokens`, `drop_params`, `client_type`, `reasoning`) so a short alias maps to a routing string + gateway + key (**custom**).
- **Layer config files last-wins** — `llm_config_chain()` orders bundled defaults → `~/.config/nemo_oo/llm_config.yaml` → project `.nemo_oo/llm_config.yaml` → `NEMO_OO_LLM_CONFIG` env var (comma-separated paths); set an entry to `null` in a higher layer to remove it.
- **Reload or force-discover the registry** — `reload_registry(*paths)` loads explicit YAMLs (or auto-discovers with no args) and `ensure_loaded()` lazily populates `MODELS` on the first `get_llm_client()` call.
- **Read the merged registry dict** — `MODELS` is the live merged `dict[str, dict]` consumed by the TUI `/model` command, eval pipeline, and NAT plugin.
- **Resolve an alias's API key** — `resolve_api_key_from_config(model_name, config)` reads the declared `api_key_env`, returning its value and WARN-logging when the env var is unset.
- **Ship aliases as an installable package** — register a zero-arg callable under the `nemo_oo_agents.bundled_configs` entry-point group to inject a lowest-priority YAML layer; installing `nemo-oo-agents-nvidia` adds the NVIDIA-gateway aliases automatically (**opt-in**).
- **Use NVIDIA-gateway aliases** — `nemo-oo-agents-nvidia` bundles aliases like `claude-opus-4-8`, `claude-haiku`, `claude-sonnet`, `claude-*-reasoning-high`, `nemotron-3-ultra`, `qwen3-80b`, `gpt-5.5`, `gpt-5.3-codex`, `gemini-3-pro`, routed via `NVIDIA_INTERNAL_API_KEY` / `NVIDIA_API_KEY` (**opt-in**).
- **Inspect & eject config from the CLI** — `nemo-oo config show` lists which layers load and `nemo-oo config eject` writes a per-user copy of the bundled YAML.

### Reliability & HTTP

- **Retry transient failures with backoff** — `RetryConfig` drives exponential backoff + jitter on 429 / 500 / 502 / 503 / 504, timeouts, and connection resets via `with_retry()` (async), `sync_retry()`, or `RetryingWrapper` around any callable.
- **Give rate limits an extra retry budget** — `rate_limit_extra_retries` / `rate_limit_base_delay` / `rate_limit_backoff_base` add 429-specific attempts beyond `max_retries`.
- **Retry empty reasoning-model responses** — set `retry_on_empty_content=True` so a model returning reasoning but blank content raises `EmptyContentError` and retries (**opt-in**).
- **Hook each retry** — supply an `on_retry(attempt, error, delay)` callback on `RetryConfig` for custom logging/metrics (**custom**).
- **Auto-retry a client's own calls** — pass `retry_config=RetryConfig(...)` to `CompletionClient`/`ResponsesClient`; its `call()`/`acall()` then transparently wrap each request in `sync_retry()`/`with_retry()` so transient failures retry without an external wrapper (**opt-in**).
- **Tune HTTP pooling and timeouts** — `HttpConfig` (max_connections, max_keepalive_connections, keepalive_expiry, connect/read/write/pool timeouts) is applied process-wide via a global `httpx.AsyncClient` monkey-patch that disables keep-alive to prevent CLOSE_WAIT hangs (**opt-in**).
- **Apply an HttpConfig to the process** — pass `http_config=HttpConfig(...)` to a client; the most recently constructed client's config is installed globally (`_set_http_config`) and read by the httpx pooling patch for all subsequent `AsyncClient` instances (**opt-in**).

### Tools, Schemas & Provider Quirks

- **Build a tool from any callable** — `create_tool_from_callable(fn)` auto-generates a `Tool` whose JSON schema is derived from the signature; or pass a Pydantic `parameters_model` to `Tool` directly (**custom**).
- **Get a cleaned parameter schema** — `Tool.get_parameter_schema(strict=)` inlines `$ref`/`$defs`, strips Pydantic `title`/`default` noise, and optionally enforces OpenAI strict-mode constraints.
- **Disable parallel tool calls** — clients set `parallel_tool_calls=False` whenever tools are sent, and strip dangling `tool_choice`/`parallel_tool_calls` when no tools are present.
- **Sanitize schemas for Bedrock** — output schemas are deep-copied and stripped of Bedrock-unsupported keywords (numeric bounds, `maxItems`, `pattern`, `additionalProperties != false`) with client-side Pydantic validation as backstop.
- **Recover non-standard tool calls** — XML `<tool_call><function=…>` blocks emitted by Nemotron/NIM and structured JSON placed in `reasoning_content` are parsed as a fallback when standard `tool_calls` are empty.
- **Insert a placeholder tool** — clients auto-add a dummy tool when Bedrock/Anthropic messages contain tool-call blocks but no `tools=` param, avoiding provider rejections.
- **Parse messy JSON output** — `extract_and_parse_json(text)` strips code fences/markdown bold, removes control chars, fixes escapes, and recovers nested/double-encoded JSON.

### Token Counting & Prompt Caching

- **Count tokens for a model** — `client.count_tokens(text)` uses litellm's tokenizer with a per-model EMA calibration ratio derived from API-reported usage.
- **Approximate tokens without the model** — `char_approximate_token_counter(text)` (`len//4`) is a drop-in `count_tokens` for clients lacking a real counter (**opt-in**).
- **Look up context window and model info** — `client.context_window` resolves registry → litellm metadata, and `get_model_info()` exposes litellm's model registry data.
- **Read usage and reasoning off responses** — `LLMResponse.usage`, `.reasoning`, `.tool_calls`, `.finish_reason`, `.assistant_message`, and `.content` (with `.message` alias) standardize every provider's output.
- **Mark prompt-cache breakpoints** — `cache_control_injection_points` (default: cache the system message and the last tool message) injects Anthropic `cache_control: ephemeral` markers per role or only the last message of a role; a litellm patch preserves the markers for Anthropic-served models behind OpenAI-compatible gateways (**opt-in**).
- **Render messages as plain text** — set `_block_formatter = PlainBlockFormatter()` on an agent to serialize conversation events without XML wrapping for more token-efficient prompts (**opt-in**).

### Observability, Testing & NeMo Flow

- **Log raw HTTP requests/responses** — `enable_http_request_logging(output_dir, url_filter=, save_responses=, errors_only=, force_httpx=)` patches httpx to dump request JSON (and optionally responses or an errors-only JSONL), returning a disable callback (**opt-in**).
- **Replay captured failed requests** — `replay_from_file()` / `replay_request()` (also `python -m nemo_oo_agents.unifiedllm.replay_requests <jsonl>`) re-send logged errors with fresh env-var API keys to classify transient vs payload failures.
- **Dump pending LLM calls on signal** — when the runtime debug handler is installed, in-flight LLM calls are tracked and dumped on SIGUSR2 via the `_track_llm_call` context.
- **Emit fire-and-forget LLM metrics** — an injectable metrics callback (set by the framework via ContextVar) receives `token_usage` and response-cleanup events without affecting the call path (**custom**).
- **Script deterministic responses in tests** — `FakeLLMClient` returns queued `LLMResponse`s and records `call_count`, `last_messages`, `last_tools`; factory helpers `simple_message()`, `with_tool_call()`, `with_reasoning()`, `with_code_responses()`, plus `reset()` (**custom**).
- **Route calls through NeMo Flow** — `install_nemo_flow(event_manager)` / `nemo_flow_scope(agent, name)` register middleware (`nemo_flow_llm_middleware`, `nemo_flow_tool_middleware`, `nemo_flow_agent_call_middleware`) routing LLM calls, code execution, and agent methods through NeMo Flow guardrails/intercepts/ATIF export; requires `uv sync --extra nemo-flow` (**opt-in**).

## 14. CLI, TUI & Web Terminal

*Ships in: `nemo-oo-agents-cli`*

### `nemo-oo` Subcommands

- **Launch the agent REPL** with `nemo-oo tui`, taking `--model/-m`, `--agent MODULE:CLASS`, `--working-dir/-w/-d`, `--mcp-file`, `--skills-dir` (repeatable), `--context-limit`, `--no-trace`, `--vi`, `--python`, `--no-splash`, and `--continue/-c [hash]` to resume a session.
- **Serve the web terminal** with `nemo-oo term`, an xterm.js browser frontend over a real PTY; accepts the same agent flags plus `--host` and `--port/-p`.
- **Run an eval-pipeline job** with `nemo-oo eval`, a passthrough that forwards all args to `eval_pipeline.cli` (e.g. `--config`, `--runs`, `--parallel`, `--test`, `--limit`, `--models`).
- **Start the trace/eval viewer** with `nemo-oo start-dev`, taking `--port/-p` (default 5001), `--host`, and `--db` (defaults to `~/.config/nemo_oo/traces.db` or `$NEMO_OO_TRACE_DB`).
- **Sweep trace/eval files** with `nemo-oo traces delete|list|stats`, where `delete` supports `--dry-run/-n`, `--older-than DAYS`, `--all`, `--evals`, `--evals-only`, and `-y/--yes`.
- **Import OTLP traces into the viewer** with `nemo-oo import-traces <path>`, taking `--endpoint` and `--batch-id`.
- **Import Harbor job traces** with `nemo-oo import-harbor <path>`, taking `--endpoint`, `--experiment`, `--batch-id`, `--batch-lines`, and `--batch-bytes` (enriches with trial name, task, reward score).
- **Delete an imported batch** with `nemo-oo delete-traces --batch-id <id>` (calls the viewer's DELETE API), taking `--endpoint`.
- **Install shell completions** with `nemo-oo completion bash|zsh|fish|install` (or `eval "$(_NEMO_COMPLETE=bash_source nemo-oo)"`).
- **Add custom subcommands** by dropping a `.py` file exporting a module-level `command` (a `click.Command`/`Group`) into `commands/`; auto-discovered, with an optional `NAME` override **(custom)**.

### TUI Chat Interface

- **Compose multi-line input** with `Enter` to submit and `Alt/Option+Enter`, `Ctrl+J`, or `Shift+Enter` (CSI-u terminals) to insert a newline.
- **Recall prior input** with `Up`/`Down` history navigation on an empty buffer plus `Ctrl+R` reverse-search.
- **Type ahead while the agent works** — submitted lines queue in a visible `│` pane, drained in submission order, with consecutive plain-text lines merged into one multi-line message.
- **Edit a queued message** by pressing `Up` on an empty buffer, which pops the last queued item back into the input.
- **Soft-cancel a running turn** with `Esc` (cancels the agent task but preserves queued input); `Ctrl+C` cancels the turn while thinking or exits when idle; `Ctrl+C ×2`/`Ctrl+D` exit.
- **Run a shell command inline** by prefixing `!` (e.g. `!ls -la`); routed to a dedicated TUI-owned `ShellTools` that tracks the agent's cwd, not recorded as a conversation turn.
- **Complete as you type** — Tab/Shift+Tab cycle a completion menu for `/`-commands, `!`-command executables from `$PATH` (then file paths), and `@file` mentions which expand inline on submit.
- **Enable vi keybindings** in the input prompt with `--vi` (or `tui.vi_mode`).
- **Resume the previous session** at launch with `--continue/-c` (last) or `-c <short-hash>`.
- **Push the agent toward its todos** with goal mode (`/goal-mode on`), which auto-injects the next open todo as a turn between user messages **(opt-in)**.
- **Hot-swap the running agent class** (slash-inception) via a `SwapAgentRequest` that re-points the dispatcher loop onto a new agent sharing the live channels.

### Slash Commands

- **Discover all commands** with `/help`; exit with `/exit` or `/quit`.
- **Manage the model** with `/model [name]`, `/switch <model>` (Tab-completes), and `/models` to list the registry.
- **Switch color theme** with `/theme [mocha|latte|vsdark|vslight]`.
- **Manage sessions** with `/session list|new|resume <id>|delete <id>|export|rename <name>` and `/clear` (new session, preserves old history).
- **Fork a session at a point** with `/time-travel <session-id> --at <tag>`, copying and truncating the SQLite DB into a new session.
- **Manage history/context** with `/compact` (summarize into a block), `/context` (window utilization), and `/history status|tags`.
- **Inspect execution** with `/events [tag]`, `/show-last-python`, `/activity` (what the agent is doing right now: python/LLM/idle, with the suspended source line), `/jobs [name]` (background queue jobs), and `/trace-url`.
- **Edit a file** with `/edit <file>` (opens `$EDITOR`, shows a diff on save) and drop into an embedded IPython REPL with `/ipython`.
- **Toggle the Python execution panel** with `/python status|on|off` and customize the toolbar label with `/toolbar [reset|<python-snippet>]`.
- **Manage skills** with `/skills list|activate <id>|deactivate <id>|commands|debug` and view todos with `/todo [id]`.
- **Manage MCP servers** with `/mcp list|connect <server>|disconnect <server>` and `/mcp-add <server info>` (hands details to the agent to wire up), both provided by the `MCPRegistry` skill via `@slash_command`.
- **Inspect/edit TUI config** with `/config show|set <k> <v>|libs|skills|path`, provided by the `TuiConfigurationSkill`.
- **Capture a bug report** with `/bug [note]`, which backs up the session `.db` and files it to GitLab via `glab`.
- **Add your own slash commands** by shipping a `SKILL.md` (YAML frontmatter, user-invocable by default) in any skills dir, or a `@slash_command`-decorated method on a `Skill`; typed args are coerced and results optionally fed back to the agent **(custom)**.

### Configuration & Persistence

- **Inspect the config chain** with `nemo-oo config show` (resolved `llm_config.yaml`, `settings.yaml`, `secrets.yaml` layers with redaction and dedup notes), `nemo-oo config path`, and `nemo-oo config eject [--force]`.
- **Layer TUI settings** via `settings.yaml` (built-in defaults → `~/.config/nemo_oo/` → project `.nemo_oo/` → `$NEMO_OO_SETTINGS`), with CLI flags layered on top by `Config.load`.
- **Tune TUI behavior** under the `tui:` section: `default_model`, `show_python`, `vi_mode`, `trace_dir`, `libs_dirs`, `skills_dirs`, `mcp_file`, `mcp_servers`, `mcp_auto_connect`, `goal_mode` (persists goal mode across launches), `toolbar_snippet` **(opt-in)**.
- **Set a persistent toolbar label** via `tui.toolbar_snippet` (a Python snippet evaluated each render, with `datetime`/`config`/`model`/`short_model`/`time`/`agent` in scope) (`tui/config.py`). **(opt-in)**
- **Tune agent behavior** under the `agent:` section: `working_dir` and `summarization` (`policy` token_budget/none, `max_tokens` null=80% of context window, `preserve_recent`, `target_chars`).
- **Auto-connect MCP servers at startup** by listing them in `tui.mcp_auto_connect`; each attaches as `self.<server>` hidden from `doc(self)` **(opt-in)**.
- **Load a custom agent class** via `--agent MODULE:CLASS` / `tui.agent_spec`, resolved by `load_agent_class` from a dotted module or a `./file.py:Class` path **(custom)**.
- **Override paths via env vars**: `NEMO_OO_USER_DIR`, `NEMO_OO_PROJECT_DIR`, `NEMO_OO_LLM_CONFIG`, `NEMO_OO_SETTINGS`, `NEMO_OO_SECRETS`, `NEMO_OO_TRACE_DB`.

### Web Terminal (`self.web.*`)

- **Push rich content to a browser side-panel** from agent code: `self.web.plot(fig)`, `self.web.html(html)`, `self.web.image(src)`, `self.web.markdown(text)`, `self.web.json(data)`, and `self.web.clear()` — `self.web` is wired onto the TUIAgent only when `NEMO_OO_RICH_URL` is set (i.e. under `nemo-oo term`).
- **Render inline and scroll-aware** — payloads POST to `/rich`, broadcast over a `/ws/rich` WebSocket, and overlay anchored to xterm.js buffer markers so plots clip as they scroll.
- **Replay rich content on reload/resume** — the PTY server keeps a bounded `_rich_history` replayed to new browser connections, and resumed-session plots re-POST with `_replay=True` so they don't push the prompt down.
- **Bridge a real PTY** over `/ws/pty` (base64-framed I/O + resize), spawning `nemo-oo tui` as a child with the rich-URL env injected; SIGINT shows a shutdown countdown and kills PTY children cleanly.

## 15. MCP Integration

*Ships in: `nemo-oo-agents` extra `[mcp]`*

### Connecting to MCP servers

- **Attach a server's tools to an agent** — declare `tool = MCPManager.create_from_server("name")` as a class attribute; it connects, lists tools, and returns an `MCPTool` instance whose methods are exposed to the LLM.
- **Configure servers in `.mcp.json`** — `MCPManager.create_from_server`/`list_servers` read a VS Code / Claude-Code-style `.mcp.json` (`mcpServers` map) from cwd, or any path via the `mcp_file` argument.
- **Override config inline** — pass `servers=` (a name→config dict, e.g. from TUI `config.toml`) to `create_from_server`/`list_servers`; inline entries are merged over file entries.
- **Specify connection details directly** — pass `url`, `command`, `args`, `env`, `headers`, and `transport` to `create_from_server` to skip config-file lookup; explicit args take precedence over config.
- **Expand secrets from the environment** — `${VAR}` references in any config string (url, headers, args, env) are expanded against `os.environ` at connect time, raising on unset variables.
- **List configured servers** — call `MCPManager.list_servers(mcp_file=..., servers=...)` to get the names of every server from `.mcp.json` plus inline config.

### Transports

- **Connect over stdio** — `MCPStdioClient(command, args, env)` spawns a local server process and talks over stdin/stdout (the default transport when none is given).
- **Connect over SSE** — `MCPSSEClient(url)` opens a server-sent-events session to a remote endpoint.
- **Connect over streamable-http** — `MCPStreamableHTTPClient(url, headers)` opens an HTTP session, supports custom headers, and exposes the server-assigned `mcp_session_id` for session correlation.
- **Authenticate with custom headers** — pass `headers={"Authorization": "Bearer ..."}` for HTTP transports to send auth or other custom headers on every request.
- **Build a client by transport name** — `create_mcp_client(transport, url=..., command=..., ...)` returns the matching `MCPBaseClient` subclass, validating that required params are present for the chosen transport.
- **Tune the tool-call timeout** — every client accepts `tool_call_timeout: timedelta` (default 60s) via the `MCPBaseClient` base.
- **Subclass the base client** — implement `MCPBaseClient` (`transport`, `server_config`, `connect_to_server`) to add a custom transport. **(custom)**
- **Read back a client's connection details** — every `MCPBaseClient` exposes `transport`, `server_config`, and `tool_call_timeout`; concrete clients add read-only `url`/`command`/`args`/`env`/`headers` accessors (`mcp/client.py`).

### OAuth authentication

- **Auto-authenticate on a 401** — `create_from_server` catches `HTTPStatusError` 401 on connect, runs the OAuth flow, retries with the bearer token injected into `Authorization`, and surfaces flattened, readable errors otherwise.
- **Discover endpoints automatically** — `handle_mcp_oauth` follows RFC 9728 protected-resource metadata (via the `WWW-Authenticate: resource_metadata=` pointer and well-known probes) and RFC 8414 authorization-server metadata to find authorize/token/registration endpoints and supported scopes.
- **Register a client dynamically** — when no `oauth_client_id` is supplied, the handler POSTs to the discovered registration endpoint to obtain a `client_id`/`client_secret`.
- **Pin a pre-registered OAuth client** — pass `oauth_client_id` to `create_from_server` (or set it per server in config) to skip dynamic registration and authorize against an existing client. **(opt-in)**
- **Run the browser PKCE flow** — `OAuthHandler.authorize` (RFC 8252 §7.3) binds a temporary loopback callback server on an OS-assigned port, uses PKCE S256, and captures the code without copy-paste.
- **Use the out-of-band (manual) flow** — set `oauth_manual=True` (or config `oauth_manual`) to show the auth URL and accept a pasted code/callback URL, for headless/remote sessions; supply an async `oauth_code_prompt` callback to collect the code without blocking `input()`. **(opt-in)**
- **Hand off the browser to a reachable host** — pass an async `oauth_browser_open` hook that opens the consent URL elsewhere while the loopback callback is forwarded back, preferred over OOB when no in-process browser exists. **(custom)**
- **Use the non-interactive client_credentials grant** — when the server advertises `client_credentials`, `handle_mcp_oauth` exchanges `client_id`/`client_secret` for a token with no browser or consent (machine-to-machine).
- **Control browser opening, scopes, and timeout** — `oauth_open_browser` and `oauth_scope` on `create_from_server` tune the flow and fall back to per-server config; `oauth_redirect_uri` and `oauth_timeout` are caller-only (no config fallback).
- **Cache and refresh tokens per project** — `handle_mcp_oauth` reads/writes `.nemo_oo_agents/mcp_tokens.json` (chmod 600), returns non-expired cached tokens, and silently refreshes via the refresh-token grant before re-prompting; disable with `use_cache=False`.
- **Drive the flow programmatically** — `OAuthConfig`, `OAuthToken` (with `is_expired()`), and `OAuthHandler` (`authorize`, `exchange_code_for_token`, `client_credentials_token`, `complete_flow`) are public for building custom auth flows. **(custom)**

### Typed tool methods

- **Call each MCP tool as a typed async method** — `create_from_server` generates one async method per server tool, with tool names normalized to identifiers (e.g. `find-references` → `find_references`) and the server's tool description as the docstring.
- **Get schema-typed parameters** — method signatures are built from each tool's JSON-Schema `properties`: JSON types map to Python types, and optionality follows the schema's `required` list (params not in `required` become optional — carrying their `default` if any, otherwise `T | None = None` — while params in `required` stay mandatory even if they declare a `default`).
- **See validation constraints in docstrings** — generated docstrings document numeric (min/max/exclusive/multipleOf), string (pattern/format), array (min/max/uniqueItems), object (min/max properties), and enum/const constraints from the schema.
- **Omit unset optionals automatically** — generated methods drop arguments left at their schema default and `_call_tool` strips `None` values, since MCP servers reject nulls for optional params.
- **Define a custom typed tool class** — subclass `MCPTool` and implement methods that call `await self._call_tool(name, args)` to hand-write a server interface instead of using the generated class. **(custom)**
- **Inspect tool specs** — each tool is described by a public `MCPToolSpec` dataclass (`name`, `description`, `input_schema`, `required`).

## 16. NeMo Flow Integration & ATIF Export

*Ships in: `nemo-oo-agents`. The NeMo Flow middleware subsection needs the `[nemo-flow]` extra (`uv sync --extra nemo-flow`); ATIF trajectory export ships in core `nemo-oo-agents` with no extra required.*

### NeMo Flow Middleware

*Requires the `[nemo-flow]` extra (`uv sync --extra nemo-flow`).*

- **Route an agent through NeMo Flow** — `nemo_flow_scope(agent, scope_name)` is an async context manager that pushes a root `ScopeType.Agent` scope, installs all three middleware on `agent.event_manager`, yields the NeMo Flow scope handle (`.uuid` for ATIF correlation), and tears down on exit. **(opt-in)**
- **Install middleware on an EventManager directly** — `install_nemo_flow(event_manager)` registers the agent-call, LLM, and code-execution middleware via `event_manager.intercept(...)` and returns an `uninstall()` callable that removes all three; use for explicit lifecycle control. **(opt-in)**
- **Route LLM calls through the NeMo Flow LLM pipeline** — `nemo_flow_llm_middleware` wraps each call through `nemo_flow.llm.execute()`, applying conditional guardrails, request intercepts, and response guardrails; the caller still receives the original `LLMResponse`.
- **Route code execution through the NeMo Flow tool pipeline** — `nemo_flow_tool_middleware` wraps `execute_python` through `nemo_flow.tools.execute()`, extracting the return value (`returned_value` → `signal.result` → `stdout`) and serializing it with `nemo_flow.typed.BestEffortAnyCodec` for inspection.
- **Scope every agent method as a NeMo Flow Function** — `nemo_flow_agent_call_middleware` pushes a `ScopeType.Function` scope named `"ClassName.method_name"` around each method call, giving ATIF per-method granularity.
- **Author NeMo Flow guardrails, intercepts, and subscribers** — request/response guardrails (reject via `GuardrailRejected`), request intercepts that mutate the LLM request or tool args, and event subscribers/ATIF export all run inside the NeMo Flow pipeline that the middleware feeds. **(custom)**
- **Enable the integration via the optional dependency** — `nemo_flow` is guarded behind `_HAS_NEMO_FLOW`; install with `uv sync --extra nemo-flow` (pulls `nemo-flow>=0.1.0`). Absent it, `install_nemo_flow`/`nemo_flow_scope` raise `ImportError` with install instructions and there is zero behavior change. **(opt-in)**

### Pipeline Serialization Boundaries

- **Strip secrets before NeMo Flow sees a request** — `api_key`, `api_base`, and `base_url` (`_SENSITIVE_KEYS`) are removed from params before constructing the `LLMRequest`.
- **Exclude non-serializable objects from LLM requests** — `tools` and `output_model` (`_NON_SERIALIZABLE_KEYS`) are dropped to avoid `AttributeError` in the native Rust pipeline.
- **Propagate intercept edits back to the call** — supported LLM params (`temperature`, `top_p`, `max_tokens`, `stop`, `frequency_penalty`, `presence_penalty`, `seed`) and tool args (`tool_call_id`, `timeout`) modified by a NeMo Flow request intercept are written back onto the middleware context so the real call and rest of the chain observe them.

### ATIF Trajectory Export — Activation

- **Globally auto-export every agent** — `enable_atif(output_dir=..., agent_version=...)` monkeypatches `Agent.__init__` so each subsequently-constructed agent attaches the exporter to its `EventManager`; writes to `<output_dir>/<AgentClass>/<session_id>.json`, idempotent, mirrors `enable_tracing`. **(opt-in)**
- **Scope export to a single run** — `atif_scope(agent, ...)` async context manager installs + uninstalls the exporter around a block, deriving `path`/`session_id`/`agent_name`/`agent_version`/`trajectory_id` from defaults, arming the standalone cascade, marking `extra.crashed` on exception, and yielding the `AtifExporter` for introspection. **(opt-in)**
- **Wire the exporter onto an EventManager by hand** — `install_atif(event_manager, *, path, session_id, agent_name, agent_version, agent_model_name=, agent_tool_definitions=, agent_extra=, trajectory_id=, cascade_to_standalones=)` subscribes a read-only `AtifExporter` and returns an `uninstall()` callable exposing the exporter on `uninstall.exporter`. **(opt-in)**
- **Redirect output with an env var** — `ATIF_OUTPUT_DIR` overrides the default `./logs/atif` base directory for `enable_atif` and `atif_scope` (the two paths that derive a path); `install_atif` always takes an explicit `path` and ignores the env var.
- **Cascade export into delegated sub-runs** — standalone generation functions and nested sub-agents automatically attach their child exporter (via `_atif_exporter_var`) so their events land in the parent trajectory's `subagent_trajectories[]` with a handoff/dispatch reference step.

### ATIF v1.7 Schema & Exporter

- **Validate and serialize trajectories with the v1.7 Pydantic models** — `Trajectory`, `AgentSchema`, `StepObject`, `ToolCallSchema`, `ObservationSchema`, `ObservationResultSchema`, `MetricsSchema`, `FinalMetricsSchema`, `ContentPart`, `ImageSource`, and `SubagentTrajectoryRef` enforce the spec (`extra="forbid"`, conditional text/image parts, agent-only fields, deterministic-dispatch and subagent-uniqueness rules); parse downstream via `Trajectory.model_validate_json(...)`.
- **Pin the schema version** — `SCHEMA_VERSION` is the `Literal["ATIF-v1.7"]` constant stamped on every `Trajectory`.
- **Consume the framework event stream** — `AtifExporter` subscribes once via `event_manager.on("*", ...)` and routes Task/SystemPrompt/BeforeTurn/LLMComplete/LLMOutput/Reasoning/ToolCallEvent/PythonOutput/AfterTurn/Error/Notification/Summary/BeforeAgentCall/AfterAgentCall into an in-memory `Trajectory`; it is a pure read-only subscriber (no `intercept()`).
- **Route custom events into trajectories** — any user-defined `EventBase` subclass falls through to a role-based generic handler (`USER`→user, `ASSISTANT`→agent with `llm_call_count=0`, `TOOL`→user; `RUNTIME_EVENT`/`METADATA` skipped), so new event types are captured without an allow-list. **(custom)**
- **Capture multimodal task input** — `Task.images` are rendered as a `ContentPart[]` array with base64 data-URLs decoded and written to an `images/` directory beside the trajectory file.
- **Record compaction boundaries** — `Summary` events emit a system step with `extra.context_management.boundary="replace"` and flag all prior steps `is_copied_context=True` for SFT filtering.
- **Aggregate final metrics and crash-safety** — every `AfterTurn` atomically writes the JSON; the top-level final turn computes `FinalMetricsSchema` (tokens, cost, steps, reasoning tokens), and `AtifExporter.finalize_on_exception()` stamps `extra.crashed`/`exception_type`.
- **Introspect the live trajectory** — `AtifExporter.get_trajectory()` returns a deep-copied snapshot and `close()` releases cascade bindings and flushes buffered Task/dispatch steps (used by tests driving handlers directly).

## 17. Tracing / OpenInference Instrumentation

*Ships in: `nemo-oo-agents` extra `[tracing]`*

### Enabling tracing

- **Enable tracing in one call** — `enable_tracing()` sets up the OTel `TracerProvider`, instruments the framework and LiteLLM, and assigns a default session id; called with no arguments it probes the local viewer and silently disables tracing if unreachable.
- **Route to chosen exporters** — pass `enable_tracing(exporters=[...])` a list of `SpanExporter` instances; multiple exporters fan spans out to multiple destinations simultaneously. **(opt-in)**
- **Group traces by experiment** — pass `experiment="..."` (or set the `TRACE_EXPERIMENT` env var) to stamp an experiment name as a resource attribute on every span. **(opt-in)**
- **Attach extra resource attributes** — pass `extra_resource_attrs={...}` to merge custom key/values (e.g. `{"eval.model": "gpt-4o"}`) into the trace resource. **(opt-in)**
- **Swap exporters on a live provider** — re-calling `enable_tracing(exporters=[...])` after tracing is enabled shuts down the old exporter processors (flushing them) and installs new ones, preserving the session processor and hooks; a no-arg re-call only re-registers hooks in the current async context. **(opt-in)**
- **Probe an OTLP endpoint** — `probe_otlp_endpoint(endpoint, timeout=None)` returns whether a viewer/collector is reachable (GET `/api/eval/health`), honoring the `OTLP_PROBE_TIMEOUT` env var (default 2.0s); used to decide live-vs-file export. **(opt-in)**
- **Point at a non-default viewer** — set the `OTLP_ENDPOINT` env var to send default traces to a viewer on another host/port; an explicit value also makes unreachable-endpoint failures print a warning instead of staying silent. **(opt-in)**

### Exporters

- **Write JSONL trace files** — `exporters.jsonl(trace_dir=None)` writes OTLP `TracesData` (one object per line) to `{trace_dir}/{session_id}.jsonl`, auto-detecting the directory from `TRACE_DIR` env var or `./traces/`. **(opt-in)**
- **Send to the local viewer journal** — `exporters.journal(endpoint=None)` (the default local-viewer exporter) posts spans with LLM message attributes stripped plus a content-addressed message sideband, reducing per-call storage from O(N) to O(delta); defaults to `OTLP_ENDPOINT` or `http://localhost:5001`. **(opt-in)**
- **Send full-message spans to the local viewer** — `exporters.local_otlp(endpoint=None)` is a dependency-free urllib OTLP-JSON/HTTP exporter for the viewer when you want traditional full-message spans. **(opt-in)**
- **Export to a third-party collector** — `exporters.otlp(endpoint, headers=None)` posts OTLP over HTTP to Jaeger/Tempo/Phoenix etc., raising `ImportError` with install hints if `opentelemetry-exporter-otlp-proto-http` is missing. **(opt-in)**
- **Export to Langfuse** — `exporters.langfuse(host=None, public_key=None, secret_key=None)` builds a Basic-auth OTLP exporter for Langfuse, reading `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` when args are omitted. **(opt-in)**
- **Print spans to stdout** — `exporters.console()` returns a `ConsoleSpanExporter` for debugging. **(opt-in)**
- **Keep stripped LLM values when journaling** — set the `NEMO_TRACE_KEEP_LLM_VALUES` env var to retain `input.value` / `output.value` attributes that the journal exporter otherwise strips. **(opt-in)**

### Sessions and span attribution

- **Set the session id** — `set_session(session_id)` writes `session.id` into the OTel context so all spans in the current async task carry it; pass `None` to clear. **(opt-in)**
- **Read the current session id** — `get_session()` returns the session id for the current async context (`None` if unset).
- **Get an auto session id** — `enable_tracing()` assigns a timestamp+uuid default session so spans always have one and file/journal routing works without manual setup.
- **Attribute concurrent sessions correctly** — `SessionSpanProcessor` stamps `session.id` per span at start (per-task ContextVar), and exporters group/route spans by each span's own `session.id` so `--parallel` eval runs and multi-agent processes stay separated.

### Span emission and customization

- **Get automatic spans for every method** — framework hooks emit OpenInference spans for agent (ellipsis) methods, generation sessions (LLM), code execution, generated-method invocations, and tool executions, nested by call hierarchy; private and dunder methods are traced too.
- **Exclude a method from traces** — decorate with `@no_trace` (from `nemo_oo_agents`) to keep a public, private, or dunder method out of the trace while still allowing generation. **(opt-in)**
- **Snapshot/diff the system prompt** — `on_messages_built` emits a `context_snapshot` span carrying the full system message on the first turn and a unified diff thereafter, skipping unchanged turns.
- **Plug in a custom instrumentor** — `NemoOOAgentsInstrumentor().instrument(tracer_provider=...)` / `.uninstrument()` wires the framework hooks onto any OTel `TracerProvider`. **(custom)**
- **Capture reproducibility metadata** — git commit/branch/dirty state, framework version, Python version, and hostname are attached as resource attributes via `get_all_metadata()`.
- **Hint the viewer rendering plugin** — spans carry the `nemo_oo_agents.viewer.plugin` attribute (`ViewerPlugin` values: method/generation/code_execution/tool_execution) to select the trace-viewer renderer.

### Secret scrubbing and lifecycle

- **Redact secrets before export** — every exporter is wrapped in `SecretScrubSpanProcessor`, which regex-matches AWS/GitHub/GitLab/Slack/Stripe/NVIDIA/Anthropic/OpenAI/Google keys, bearer/PEM/hex tokens and generic key=value secrets in span attributes, replacing them with `[REDACTED]` and adding a `secrets.redacted_count`.
- **Inspect scrub statistics** — the module-level `stats` (`ScrubStats`) singleton exposes `total_scrubbed`, `spans_scrubbed`, `snapshot()`, and `reset()` for observability into how often scrubbing fires.
- **Flush pending spans** — `flush_traces(timeout_millis=30000)` force-flushes all exporters (including the journal callback's POSTs).
- **Shut down tracing** — `shutdown_traces()` flushes and releases the provider and its exporters.
- **End dangling spans on timeout/cancel** — `end_active_spans(reason="timeout")` closes all un-ended spans with an error status so they still export, returning the count ended. **(opt-in)**
- **Cap oversized span payloads** — error messages are truncated to ~5K chars and serialized args/results/code to ~50K chars (head+tail) so OTLP payloads and storage don't blow up; the provider sets no per-span attribute cap so `session.id` is never FIFO-evicted.

## 18. Trace & Eval Viewer

*Ships in: `nemo-oo-agents` extra `[viewer]`*

### Launch & Ingest

- **Start the unified trace + eval viewer** — `nemo-oo start-dev` (CLI) or `python -m nemo_oo_agents.viewer` boots the FastAPI app on port 5001 serving the React SPA from `frontend-react/dist`; `start-dev` accepts `--port`/`-p` and `--host`/`-h`, while `python -m nemo_oo_agents.viewer` honors `NEMO_OO_TRACE_VIEWER_PORT` (host fixed to `0.0.0.0`).
- **Point the store at a SQLite DB** — traces persist in `traces.db` (in the working directory by default; the `start-dev` CLI resolves it to the user config dir instead); override with `--db` or `NEMO_OO_TRACE_DB`, letting you run side-by-side viewers **(opt-in)**.
- **Ingest OTLP traces** — `POST /v1/traces` accepts OTLP JSON `ExportTraceServiceRequest`, queued onto an async write queue and committed by a single SQLite-writer thread so parallel eval runs never block ingest.
- **Block until spans are queryable** — `POST /v1/sync` drains the ingest queue (30s timeout) so a freshly flushed trace is readable before scoring fetches it.
- **Stream content-addressed LLM message journals** — `POST /v1/journal/messages`, `/v1/journal/blocks` (with `X-Session-Id`), and `/v1/journal/calls` store deduplicated message blocks and per-call input/output hash lists, reconstructed via `GET /api/traces/{session_id}/calls`.
- **Import .jsonl traces from disk** — `nemo-oo import-traces <file_or_dir> [--endpoint] [--batch-id]` recursively POSTs OTLP-JSON files, derives `session.id` from filenames, and tags each with a `batch_id` resource attribute **(opt-in)**.

### Browse, Search & Manage Traces

- **List trace sessions** — `GET /api/traces` paginates (page/limit, max 500), `search`es by name, filters by `experiment` or `batch_id`, and sorts by name/event_count/size/modified (`sort_by`/`sort_dir`).
- **Load and paginate a session's spans** — `GET /api/trace` returns OTLP spans (augmented with reconstructed journal messages) with `limit`/`offset`; `GET /api/trace-count` and `GET /api/trace/resource` expose span count and OTLP resource attributes.
- **Export a trace** — `GET /api/trace/export` streams a `.jsonl` download of the session's OTLP spans plus its annotations.
- **Delete traces** — `DELETE /api/traces/{session_id}` removes one session; `DELETE /api/traces?batch_id=X` clears an import batch; `DELETE /api/traces?confirm=true` wipes all.
- **Filter and search in the trace view** — the SPA filters events by type checkboxes, span/parent id, and case-insensitive text search; eval spans float to the top, and filter/timeline state is encoded in shareable URL params.
- **Navigate by keyboard** — `j/k`, arrows, `l/h/Enter` to expand/collapse 3-state spans, `a` annotate, `+`/`-` expand-all/collapse-all, `r` toggle per-span Raw JSON, `/` focus search, `?` shortcut help.
- **Copy a trace-explorer debug prompt** — the DEBUG button on trace and eval-test detail copies a `uv run trace-explorer --viewer … --session-id …` prompt to hand the trace to Claude Code / Cursor.

### Span Inspection (Plugin Renderers)

- **Render spans by type via the plugin registry** — `registerPlugin(eventType, component)` maps exact / prefix / `*`-wildcard event types to React renderers; add your own to display custom span types **(custom)**.
- **Inspect agent method calls** — `MethodPlugin` shows agent name, method, strategy, call id, file path, args/kwargs, result, and error.
- **Inspect LLM calls** — `LLMCallPlugin` reconstructs OpenInference input/output messages, tool calls, and tool results across flattened, `input.value`, and JSON-array attribute formats.
- **Inspect code execution** — `CodeExecutionPlugin` shows executed source, stdout, defined methods, and returned value.
- **Inspect generation, tool execution, and runtime errors** — `GenerationPlugin` (strategy/generation id/errors), `ToolExecutionPlugin` (tool name/result/execution+generation id), and `RuntimeErrorPlugin`.
- **Inspect agent messages and reasoning** — `AgentMessagePlugin` and `AgentReasoningPlugin` render multi-turn `message()` output and chain-of-thought reasoning.
- **Inspect eval spans** — `EvalPlugin` parses `eval.scorer.<name>.{score,passed,reasoning}` (and `eval.scores`) into per-scorer score / pass-fail / reasoning rows.
- **Fall back to a generic span renderer** — `SpanPlugin` (`span.*`) shows hero content (system/user message, code, result), duration, status, and a Raw JSON disclosure; `DefaultPlugin` covers unknown types.

### Server-Side Trace Explorer

- **Run TraceExplorer methods server-side** — `/api/explorer/*` builds (LRU-cached) `TraceExplorer` instances from DB spans and returns rendered text for `overview`, `session`, `session-list`, `turn`, `errors`, `first-error`, `search`, `timeline`, `find-span`, and `eval-context`.
- **Drill into a subtree without loading the full trace** — `/api/explorer/{summary,agent-spans,error-spans,descendant-spans,overview-fast,session-fast,turn-fast}` query the DB directly for lightweight summaries, AGENT-only call graphs, error spans, or a single span subtree.
- **Full-text search spans** — `GET /api/explorer/search-fast` uses an FTS5 index (words, "phrases", AND/OR/NOT) returning snippet matches; `POST /api/explorer/backfill-fts` indexes pre-existing spans (idempotent).

### Annotations

- **Annotate traces and spans** — `POST /api/annotations` creates score / label / comment / tags annotations targeting a session or span (`source` defaults to `human`); `GET /api/traces/{session_id}/annotations` lists them and the SPA renders badges.
- **Edit and remove annotations** — `PATCH /api/annotations/{id}` and `DELETE /api/annotations/{id}`; the trace view offers quick one-click +/- feedback.
- **List all tags** — `GET /api/tags` returns every tag with usage counts.

### Experiments & Evals

- **Browse experiments** — `GET /api/eval/experiments` paginates and searches eval experiments (sessions sharing an `experiment` resource attribute); `GET /api/eval/experiments/all` returns the full list with model/test/pass counts.
- **Drill into an experiment** — `GET /api/eval/experiment/{id}` returns per-test rows with dynamically discovered `metadata_keys`/`columns`, keyword search, equality and `~substring` metadata filters, sorting, and pagination.
- **Aggregate pass rates** — `GET /api/eval/experiment/{id}/summary` computes overall success rate / avg score plus `by_model`, `by_tier`, `by_test_type`, and a test-type × model matrix; `GET /api/eval/experiments/metrics` returns cross-experiment history with per-tier rates.
- **Get tests, status, and trace per test** — `GET /api/eval/experiment/{id}/tests`, `/status`, and `/trace/{test_id}` (returns OTLP spans for the test's session); test detail embeds the full `TraceView` plus configurable, persisted columns.

### Playground

- **Chat with any model** — `POST /api/playground/inference` runs LiteLLM completion with model/temperature/max_tokens, multi-role messages, default sandbox tools (`execute_python`/`return_result`) when tool calls are present, and returns content, tool calls, reasoning, and token usage.
- **List and register models** — `GET /api/playground/models` returns built-in models from `models.yaml` (`AGENT006_MODELS_CONFIG`), custom models, and which API keys are present; `POST /api/playground/models` and `DELETE /api/playground/models/{model_id}` manage `custom_models.json` entries **(custom)**.

### Live Updates & Status

- **Auto-refresh running experiments** — the SPA `useLiveUpdater` hook polls every 5s while any experiment is `running`, pausing via the Page Visibility API.
- **Query store status** — `GET /api/version`, `/api/config`, `/api/provider/status`, `/api/eval/health`, and `POST /api/refresh` report version, DB path, experiment list, and session/experiment counts.

## 19. Trace Explorer

*Ships in: `nemo-oo-agents` (`nemo_oo_agents.trace_explorer`)*

### Load & Navigate Traces

- **Load a trace from a JSONL file** — `await TraceExplorer.from_file(path, eval_result=..., benchmark_context=..., root_generation_index=...)` reads spans once and builds a unified parent/child session tree; rejects files that are not valid traces (e.g. eval-result `.noo-eval.jsonl` files).
- **Load a trace from the viewer API** — `await TraceExplorer.from_viewer(base_url, session_id, eval_result=..., root_generation_index=...)` paginates `GET /api/trace` and builds an explorer.
- **Bulk-load an entire experiment** — `await TraceExplorer.load_experiment_sessions(base_url, experiment_id, root_generation_index=...)` fetches all spans in one call and returns a `{session_id: TraceExplorer}` map grouped by OTel `trace_id`.
- **Select a root generation when multiple roots exist** — pass `root_generation_index` (0-based) to any loader, or `set_root_generation_index()` / `get_root_generation_index()` directly. **(opt-in)**
- **Inspect the loaded source** — `sessions`, `trace_file`, `trace_path`, `eval_result`, `benchmark_context`, `agent_count`, and `max_agent_depth` properties expose the parsed tree.
- **Get the API usage guide** — `await trace.help()` returns the navigation guide from the class docstring.
- **List all sessions** — `await trace.get_session_list()` returns `SessionSummary` objects (root-first, including children).
- **Resolve a turn's span ID** — `await trace.get_span_id(session_id, turn_index)` returns the full hex span ID for correlation.
- **Abbreviate session IDs** — every lookup accepts 6-character short IDs as well as full IDs.

### Analyze & Drill Down

- **See the call graph overview** — `await trace.get_overview(concise=True)` renders the session tree with inputs, outputs, and error status; `get_overview_data()` returns typed `OverviewData`.
- **Drill into a session** — `await trace.get_session(session_id, concise=False)` shows turn-by-turn execution; `get_session_data()` returns typed `SessionData`.
- **Inspect a single turn** — `await trace.get_turn(session_id, turn_index)` shows the LLM context window, output, and execution result; `get_turn_data()` returns typed `TurnInfo`.
- **List all errors** — `await trace.get_errors()` shows every error with its session/turn context; `get_errors_data()` returns structured data.
- **Jump to the first error** — `await trace.find_first_error()` navigates straight to the earliest failure; `find_first_error_data()` returns structured data.
- **View the eval context** — `await trace.get_eval_context(concise=True)` shows inputs, expected outputs, and scorer results; `get_eval_context_data()` returns structured data backed by `EvalContextData`/`ScoreDetail`.
- **Render a chronological timeline** — `await trace.get_timeline(max_events=50)` orders events across sessions; `get_timeline_data()` returns typed `TimelineData`.
- **Inspect recursion** — `await trace.get_recursion_pattern()` classifies self/mutual recursion and `await trace.get_method_counts()` counts invocations per `Agent.method`.
- **Read raw span JSON** — `await trace.get_raw_span(span_id)` / `get_raw_span_data()` for one span, `await trace.get_raw_spans(session_id)` for a whole session.
- **Find a span by ID** — `await trace.find_span(span_id, json_output=False)` locates a span (full or prefix) and shows navigation breadcrumbs.
- **Pull turn context text** — `await trace.get_turn_context(session_id, turn_index, max_length=..., include_system=False)` reconstructs the full context window. (opt-in for system messages)

### Search & Compare

- **Search across all trace content** — `await trace.search(pattern, concise=True)` regex-matches messages, code, stdout, and responses; `search_data()` returns typed `SearchMatches`.
- **Search within one turn's context** — `await trace.search_in_turn_context(session_id, turn_index, pattern, max_matches=10, include_system=False)` returns `SearchResult` matches with surrounding context.
- **Compare two traces** — `await trace.compare(other)` and the classmethod `await TraceExplorer.diff(trace1, trace2)` do side-by-side call-graph comparison, first-divergence detection, and prompt-expression path diffing; `compare_data()` / `diff_data()` return structured data.
- **Read harness telemetry** — `await trace.get_harness_telemetry(session_id=None)` extracts `harness.*` span attributes (model-helping interventions) driven by `get_span_schema()` (`runtime/harness_metrics.py`); `get_harness_telemetry_data()` returns the raw merged metrics.

### CLI (`trace-explorer`)

- **Run from a file or viewer** — positional `trace.jsonl`, or `--viewer URL` with `--session-id ID`; mutually exclusive sources are validated.
- **Pick a command** — `--errors/-e`, `--first-error`, `--timeline`, `--eval`, `--search PATTERN`, `--diff OTHER`, `--raw SPAN_ID`, `--harness`, `--session/-s ID`, `--turn/-t N` (requires `--session`), and `--span-id ID` (viewer-only jump).
- **Run experiment-level analysis** — `--experiment ID` (viewer-only) prints a pass/fail summary, with `--errors` (Python exceptions), `--failures` (eval wrong-answer reasons), and `--search` modifiers (mutually exclusive).
- **Control output** — `--json` emits structured JSON for any command, `--verbose/-v` shows full (non-concise) detail, `--quiet/-q` suppresses parser warnings, `--root-generation N` selects a root.
- **Get help** — `--api-help` prints the programmatic API guide, `--help` shows usage; `--install-skill` installs the Claude Code skill.
- **Invoke via module** — `python -m nemo_oo_agents.trace_explorer` runs the same `main()` entry point as the `trace-explorer` console script.

### Thin Client (large traces)

- **Delegate analysis to the viewer server** — `TraceExplorerClient(base_url, session_id, timeout=60.0)` mirrors the `TraceExplorer` async API but runs all logic server-side, avoiding local span downloads. **(opt-in)**
- **Use cached full-analysis endpoints** — `get_overview`, `get_session`, `get_session_list`, `get_turn`, `get_errors`, `search`, `get_timeline`, `find_first_error`, `find_span`, `get_eval_context`, and `help` match the local explorer.
- **Use fast lightweight endpoints** — `get_summary()`, `get_overview_fast()`, `get_agent_spans()`, `get_error_spans()`, `get_descendant_spans(span_id)`, `get_session_fast(session_id, span_id)`, `get_turn_fast(session_id, span_id, turn_index)`, and `search_fast(query, limit)` (FTS5 syntax) skip the full tree build for huge traces.
- **Fall back automatically** — the CLI probes `/api/explorer/summary` and routes viewer commands through the thin client when available, falling back to full loading otherwise.

### Typed Outputs & Extension Points

- **Consume typed result objects** — `OverviewData`/`OverviewStats`/`RootSessionInfo`, `SessionData`/`SessionSummary`, `TurnInfo`, `EvalContextData`/`ScoreDetail`, `SearchMatches`/`SearchResult`, `TimelineData`/`TimelineEvent`, plus tree primitives `AgentSession`, `LLMTurn`, `ExecutionTurn`, `LLMMessage`, `ToolCall`, `ToolDefinition` are all exported from `nemo_oo_agents.trace_explorer`.
- **Reconstruct eval context from a dict** — `EvalContextData.from_dict(data)` rebuilds the typed eval result; `.to_dict()` serializes it back.
- **Toggle quiet parsing globally** — `set_quiet_mode(bool)` / `get_quiet_mode()` suppress span-parser warnings programmatically. **(opt-in)**
- **Install the Claude Code skill** — `trace-explorer --install-skill` idempotently copies the bundled `trace-explorer` SKILL.md into `~/.claude/skills/`. **(opt-in)**

## 20. NVIDIA Agent Toolkit (NAT) Bridge

*Ships in: `nat-oo-agents`*

### Register an agent as a NAT workflow

- **Register the wrapper workflow type** — `nemo_oo_agents_wrapper` (set as `workflow._type` in NAT YAML) wraps any NeMo OO agent method as a NAT `Function`, configured/run/observed through NAT, via the `@register_function`-decorated `register` in `nemo_oo_agents_wrapper.py`.
- **Point at the agent class** — the required `agent: path/to/module.py:ClassName` field dynamically imports the module from file and resolves the class (unique synthetic module name per load).
- **Name the target method** — the required `method:` field selects the async method to invoke; the runtime validates it exists and is a coroutine function, else raises with a list of available public methods.
- **Add import paths** — `dependencies:` is a list of existing directories prepended to `sys.path` during registration and removed again in a `finally` block.
- **Load environment variables** — `env:` accepts either a `.env` `FilePath` (loaded via `dotenv.load_dotenv(..., override=True)`) or an inline `dict[str, str]` written into `os.environ`. **(opt-in)**
- **Set a function description** — `description:` overrides the NAT function description (defaults to `ClassName.method`).
- **Accept flexible input** — `NemoOOAgentsWrapperFunction._convert_input` coerces a raw string, a `{messages|content|input}` dict, or a list of messages (last message's `.content`/str/dict) into a `NemoOOAgentsWrapperInput` (`extra="allow"`, field `messages: list[Any] | str`); `_ainvoke` then reduces it to a single message string passed to the agent method.
- **Return string content** — output is wrapped in `NemoOOAgentsWrapperOutput(content=...)` with a registered `convert_to_str` converter; `_astream` falls back to a single `_ainvoke` (streaming not yet supported).
- **Reject unknown config keys** — `NemoOOAgentsWrapperConfig` sets `extra="forbid"` (`nemo_oo_agents_wrapper.py`), so a typo or unsupported key in the `workflow:` block fails validation loudly instead of being silently ignored.

### Bridge NAT LLMs into agents

- **Reference a NAT-configured LLM** — `llm_name:` resolves a model from the YAML `llms:` section via `builder.get_llm(name, wrapper_type="nemo_oo_agents")`, instantiating the agent as `AgentClass(llm=llm)`; on failure it logs a warning and the agent falls back to its own LLM. **(opt-in)**
- **Wrap the OpenAI provider** — `OpenAIModelConfig` is registered (`@register_llm_client`, `wrapper_type="nemo_oo_agents"`) and built into a `CompletionClient`.
- **Wrap the NIM provider** — `NIMModelConfig` is registered the same way for NVIDIA NIM endpoints.
- **Wrap the LiteLLM provider** — `LiteLLMModelConfig` is registered as the generic fallback provider. Each provider registration is guarded by a try/except ImportError so missing NAT provider modules are skipped.
- **Fall back to the unifiedllm registry** — `_build_llm` merges NAT YAML values (api_key, base_url, temperature) with `nemo_oo_agents.unifiedllm` registry defaults (`api_base`, `top_p`, `max_tokens`, `temperature`, litellm `model_name` routing prefix); NAT config always wins, registry fills gaps, `ensure_loaded()` triggers lazy registry bootstrap under `_registry_lock`.
- **Resolve API keys and secrets** — `_get_secret_value` unwraps Pydantic `SecretStr`, and `resolve_api_key_from_config` pulls the key from the registry env var when the NAT config omits it; clients are always built with `drop_params=True`.

### Inject NAT tools as native methods

- **Inject NAT functions onto the agent** — `tools:` lists NAT function names; `inject_nat_tools` resolves each via `builder.get_function`, generates a native async method, and `setattr`s it on the agent class so `doc(self)`/agentdoc sees it. **(opt-in)**
- **Generate introspectable tool wrappers** — `create_tool_method` exec-builds an `async def <tool_name>(self, ...) -> str` from the NAT function's Pydantic `input_schema`, preserving typed params/defaults and docstring, delegating to `self._nat_fns[tool_name].ainvoke(...)`.
- **Drop NAT dummy parameters** — params named `unused`/`dummy`/`_unused`/`_dummy` (`_DUMMY_PARAM_NAMES`) are filtered out of the generated signature; tools with no real params invoke with an empty string.
- **Tolerate injection failures** — each tool injection is wrapped in try/except that logs a warning and continues, so one bad tool doesn't abort the whole agent.

### Dual tracing through NAT

- **Enable dual OTel + JSONL tracing** — the `enable_tracing` field (default `True`) calls `nemo_oo_agents.tracing.enable_tracing(extra_resource_attrs={"tags": ["nat_integration"]})` to create the JSONL TracerProvider, then `setup_shared_tracer()` so spans flow to both NAT's OTLP collector and the NeMo OO trace viewer.
- **Piggyback on NAT's TracerProvider** — `setup_shared_tracer(otlp_endpoint=None)` reuses an existing SDK `TracerProvider` (detected via `add_span_processor`) or creates a new one, and optionally attaches an `OTLPSpanExporter` (HTTP) via a `SimpleSpanProcessor` when an endpoint is given. **(opt-in)**

### Plugin discovery

- **Auto-discover via entry point** — the `nat.components` entry point `nat_nemo_oo_agents = "nat.plugins.nemo_oo_agents.register"` makes NAT import `register.py`, which imports `llm` and `nemo_oo_agents_wrapper` to trigger the registration decorators.

## 21. Evaluation Framework

*Ships in: `eval_pipeline` · `nemo-oo-agents-benchmarks` · `nemo-oo-agents-cli`*

### Run evaluations

- **Run an eval suite from YAML** — `nemo-oo eval --config config.yaml` forwards verbatim to `eval_pipeline` (`eval_pipeline.cli.main_async`), loading models, tests, scorers, and `default_strategy` from the config.
- **Scope a run with CLI flags** — `--test`/`--models` (comma lists), `--runs N` (self-consistency), `--limit N`, `--task-ids ID...`, `--parallel N`, `--timeout SECS`, `--output-dir`, and `-q/--quiet`.
- **Pick an execution engine** — `--engine asyncio` (default, I/O-bound LLM APIs) or `--engine subprocess` with `--batch-size` and `--memory-limit MB` (per-worker RSS cap, diagnostics captured at 85%, worker killed past the cap).
- **Override the strategy for all agents** — `--default_strategy` accepts `pure_python`, `codeact`, `codeact_lite`, `reflexion`, `predict`, or `structured_output`; YAML `default_strategy:` may instead name a custom `module`/`class`. **(opt-in)**
- **Swap in a custom agent** — `--agent [OLD=]SPEC` replaces the agent class on matching tests, where SPEC is `module.Class`, `path/to/agent.py`, or `path/to/agent.py::ClassName`. **(custom)**
- **Compare multiple agents on one dataset** — `--agents [LABEL:]SPEC...` runs each variant on the same samples, writes per-agent subdirs, and prints a comparison table. **(opt-in)**
- **Disable provider caching for pass@k diversity** — `--no-cache` (or per-model/config `no_cache`) sets the NVIDIA inference API `no-cache` body flag. **(opt-in)**
- **Control file output** — `--no-files` suppresses the experiment dir and trace files (traces still go to OTLP); `--trace-files` forces JSONL traces even when the viewer is live. **(opt-in)**
- **Detect hangs and dump state** — `--hang-timeout SECS` runs a `HangWatchdog` thread that fires `SIGUSR2` after no progress. **(opt-in)**
- **Log raw HTTP traffic** — `--http-logging [DIR]`, `--http-logging-url-filter`, `--http-logging-responses`, plus the `CAPTURE_LLM_ERRORS` env var for errors-only capture. **(opt-in)**

### Define tests and scorers (eval_pipeline Python API)

- **Build an evaluator in Python** — `Evaluator(models=..., output_dir=..., name=..., pass_threshold=, timeout_seconds=)`, then `evaluator.add_test(name, agent_class, method, data, scorers, tier, description)` and `await evaluator.run(...)`.
- **Load an evaluator from config** — `Evaluator.from_config(path)` / `evaluator_from_config(path, no_cache_override=)`; `load_config`/`load_tasks` parse the YAML and JSONL data files; `EvalConfig`, `TestConfig`, `StrategyConfig` model the schema.
- **Reference models three ways** — full `ModelSpec`, a registry-name string, or `{registry: name, ...overrides}`; unlisted `agent_models` entries auto-resolve from the unifiedllm registry, with `reasoning_effort`, `max_thinking_tokens`, `max_retries`, `retry_on_empty_content`, and `client_type` (`completion`/`responses`) honored.
- **Tag tests by tier** — `Tier.STABLE`/`FRONTIER`/`HORIZON` on each test, carried through to results.
- **Score with built-in scorers** — `ExactMatchScorer`, `TypeMatchScorer`, `ModeSelectionScorer(expected=)`, `LLMJudgeScorer(rubric=, model_spec=, skip_prefill=)`, `LLMMethodologyScorer(rubric=, model_spec=, skip_prefill=)`, combined via weighted `ScorerConfig`.
- **Plug in a custom scorer** — give a dotted `class:` path in YAML (extra keys forwarded as kwargs); the class implements `score(self, ctx: ScoringContext) -> ScoreResult` and reads per-task `ctx.metadata`. **(custom)**
- **Attach arbitrary eval metadata** — config/test/task-level `eval_metadata` merges into each result and becomes `eval.{key}` span attributes plus dynamic viewer columns. **(opt-in)**
- **Drive scoring directly** — low-level `score_task`, `build_scoring_context`, `execute_task`, `process_sample`, `run_evaluation`, with `Task`, `ExecutionResult`, `ScoringContext`, `ScoreResult`, `PipelineConfig`, `Sample`.

### Persist and inspect results

- **Write incremental `.noo-eval.jsonl`** — `ExperimentWriter.start/append_result/finalize` emits a versioned `EvalMetadataLine`, one `EvalTestResult` per sample (`scores`, token counts, `trace_file`, timing, `peak_rss_mb`, `eval_metadata`), and a final `EvalCompletionLine`; `NullExperimentWriter` is the `--no-files` no-op.
- **Parse and check experiment status** — `EvalFileParser`, `get_experiment_status`, `EvalParseError`, `SUPPORTED_VERSIONS`, and the `EvalLine` union (`metadata`/`result`/`completion`/`annotation`).
- **Read run results in code** — `EvalResults` exposes `.summary()`, `.pass_rate`, `.passed`, `.total`, and `.output_file`.
- **Browse runs in the viewer** — `nemo-oo start-dev` launches the unified trace + evaluation viewer (default port 5001), which recognizes `.noo-eval.jsonl` files and supports appended `EvalAnnotationLine` notes.
- **Append eval spans to a trace** — `write_eval_span_to_trace(trace_file, test_id, passed, weighted_score, model, agent_class, method, scores, ...)` writes a `name="eval"` span (with `eval.*` attributes) into an existing trace JSONL so the viewer's EvalPlugin picks it up (`trace_eval_span.py`).

### Self-improvement runner (nemo_oo_agents_benchmarks.evaluation)

- **Run benchmarks with an iterative improvement loop** — `SelfImprovementRunner(agent_factory, adapters, config, trace_analyzer, llm_client)` with `run_all()` / `run_benchmark()`; each failed attempt is analyzed and fed back as `improvement_context` until success or `max_improvement_iterations`.
- **Tune the loop via `RunnerConfig`** — `max_improvement_iterations`, `stop_on_success`, `trace_dir`/`results_dir`, `limit`, `timeout_seconds`, `max_concurrent_tasks`, `save_traces`, `save_intermediate_results`, `generate_html_report`.
- **Hook into per-task completion** — `RunnerConfig.on_task_complete(result, done, total)` for incremental progress/UI updates. **(custom)**
- **Specify a per-task agent class** — set `task.metadata["agent_class"]` to a dotted path; the runner dynamically imports and instantiates it, falling back to `agent_factory`. **(custom)**
- **Build a benchmark adapter** — subclass `BenchmarkAdapter` implementing `name`/`get_tasks`/`format_for_agent`/`evaluate`, optionally overriding `get_tools`, `setup`, `teardown`, and `create_repair_task` for self-repair tasks. **(custom)**
- **Build an interactive environment** — subclass `BenchmarkEnvironment` (Gym-style `reset`/`step`/`close`/`get_tools`, `max_steps`, `requires_docker`, `ensure_docker_available`) returning `StepResult`. **(custom)**
- **Categorize failures** — `ErrorCategory` enum spanning validation, tool-calling, execution, logic, and policy errors, attached to each `EvalResult`.
- **Model task lifecycle** — `Task`, `EvalResult`, `TaskResult` (with `first_attempt_success`/`improvement_delta`/`solved_after_improvement`), and `BenchmarkReport` (with `success_rate`/`duration_seconds`).

### Scaling, metrics, and trace analysis

- **Run a generic task batch** — `TaskRunner` (Layer 2) with swappable `ExecutionEngine`, `EngineConfig`, checkpoint/resume via `TaskState`, and `run_evaluation` convenience; `EvaluationTask`/`EvaluationResult` carry opaque data.
- **Pick a concurrency engine** — `ConcurrencyEngine` (semaphore-bounded asyncio) or `SubprocessEngine` (persistent JSON-line worker pool, one-shot workers under memory limiting). **(opt-in)**
- **Adapt NeMo OO agents into the runner** — `NemoOOAgentsAdapter` (`AgentConfig`) instantiates agents, dispatches `run`/`execute`/callable interfaces, applies timeouts, and routes per-task traces; `execute_agent_on_tasks` is the convenience wrapper. **(custom)**
- **Resume from checkpoints** — `TaskRunner` loads/saves `TaskState` JSONL atomically and skips completed tasks via `_split_checkpoint`. **(opt-in)**
- **Compute self-improvement metrics** — `MetricsCalculator.compute_from_reports/compute_from_results` produces `ImprovementMetrics` (success/first-try/improvement rates, pass@1, error distribution, errors-fixed-by-iteration).
- **Generate reports** — `MetricsCalculator.generate_html_report` (Chart.js dashboard) and `save_metrics_json`; `SelfImprovementRunner` also emits a combined `.noo-summary.json`.
- **Extract usage from traces** — `TraceAnalyzer.analyze_trace` reads JSONL spans into `TaskUsageStats`/`ModelUsageStats`/`AggregateUsageStats` (per-model tokens, p95 latency, runtime); `extract_failures`/`identify_patterns`/`generate_improvement_context` produce `ExtractedFailure`/`FailurePattern` and the LLM-or-rule-based improvement hints.
- **Run agents per-task with isolated traces** — both runners switch the tracing session per task (`set_session`) so each attempt writes a discrete `.jsonl` trace discoverable by the viewer.

> Container-side benchmark agents and the `nemo-harbor` runner are covered in [§22](#22-container-side-benchmark-runner-harbor).

## 22. Container-Side Benchmark Runner (Harbor)

*Ships in: `nemo-oo-agents-benchmarks`*

### nemo-harbor CLI

- **Run an agent on a task** with `nemo-harbor --instruction <text> --model <litellm-name> --agent-type <type>` — the `nemo-harbor` console entry point (`runner:main`) instantiates the agent, runs `_run_evaluation({"user_message": instruction})`, and writes results.
- **Select the agent variant** with `--agent-type` — defaults to `baseline`; validated against the `AGENT_CLASSES` registry before any heavy imports, with a clean error listing valid types **(custom)**.
- **Inject container tool sets** with `--tools swebench` (comma-separated) — wires `SWEBenchLocalTools` onto `self.swebench` (and onto the opt1/pro feedback sub-agent); `terminal` injects `TerminalBenchTools` and is auto-added for any `terminal-bench*` agent type **(opt-in)**.
- **Override the API base URL** with `--api-base`, falling back to `OPENAI_BASE_URL`; `OPENAI_API_KEY` is honored as an LLM override when set **(opt-in)**.
- **Set the agent shell working directory** with `--working-dir`, threaded into `task_input["working_dir"]` **(opt-in)**.
- **Emit results to fixed container paths** — answer text to `/app/answer.txt`, `result.json` (model, agent_type, success, response, error, token counts) to `/logs/agent/`, and a `nemo_oo_agents_benchmarks.log` log file alongside it.
- **Track token usage automatically** — wraps the run in `start_task_tokens()` / `get_task_tokens()` so input/output token counts land in `result.json`.

### Tracing & OTel export

- **Auto-detect the trace viewer** — probes `OTLP_ENDPOINT` (default `http://localhost:5001/v1/traces`) via `probe_otlp_endpoint`; if reachable, streams live through the journal exporter, otherwise falls back to JSONL files in `/logs/artifacts/traces/`.
- **Tag spans with eval metadata** — injects `eval.model` and `eval.agent_type` as resource attributes so viewer sessions are identifiable without a separate import step.
- **Set `OTLP_ENDPOINT` for Docker containers** — point at `http://host.docker.internal:5001/v1/traces` when the container does not share the host network namespace **(opt-in)**.

### Pre-built benchmark agents

- **Run the general-purpose CodeAct baseline** — `baseline` (`BaselineAgent`): data-science imports pre-loaded (pandas/numpy/matplotlib), `max_iterations=100`, injects built-in `_BashTools` (`run_command`/`read_file`/`write_file`) when no environment tools are present.
- **Run the ReAct baseline** — `react-baseline` (`ReActBaselineAgent`): classic Thought/Action/Observation loop with discrete tool dispatch via `_ToolRegistry`, stop sequences, one-action-per-turn enforcement, and built-in bash fallback.
- **Solve SWE-bench Verified with a single CodeAct loop** — `swebench/basic` (`SWEBenchBasicAgent`): `max_iterations=250`, `self.swebench` tools, `git diff HEAD` fallback on failure.
- **Solve SWE-bench Verified with the multi-phase opt1 pipeline** — `swebench/opt1` (`SWEBenchOpt1Agent`): clarify → root-cause → repo-overview → implement → `FeedbackAgent` review loop (up to 3 iterations) with structured `ClarifiedRequirements`/`RootCauseAnalysis`/`Overview` outputs.
- **Solve SWE-bench Pro (multi-language)** — `swebench/pro` (`SWEBenchProAgent`): opt1 pipeline tuned for Python/JS-TS/Go, repo at `/app`, higher iteration budgets, up to 5 review iterations, language-aware test runners, and extra `requirements`/`interface` task fields merged into the prompt.
- **Solve SWE-bench Verified with a todo-driven single agent** — `swebench/todo` (`SWEBenchTodoAgent`): `max_iterations=300`, persistent `ShellTools` with `_init_command` conda activation, pre-filled `TodoManager` phases (explore→reproduce→trace→fix→verify), in-place grading (no patch), and strict/auto-detected working dir (`/testbed` vs `/app`).
- **Select the SWE-bench-todo shell backend** via `SHELL_VARIANT=legacy` (`ShellToolsLegacy`) vs default `ShellTools`, tagged into harness metrics **(opt-in)**.
- **Run the DABStep payment-fee analysis pipeline** — `dabstep` (`DABStepAgent`, opt63): orchestrates `RulesLawyer` → `compute_answer` → `SolutionVerifier` in a retry loop (`MAX_RETRIES=3`), auto-loads `/app/data` (csv/json/md), and defaults to DeepSeek V3.2 on NVIDIA NIM.
- **Override DABStep sub-agents** — `RulesLawyer` and `SolutionVerifier` are class attributes on `DABStepAgent`, swappable for testing **(custom)**.
- **Run the Tau-Bench multi-turn customer-service agent** — `tau-bench` (`TauBenchAgent`): conversation loop up to `MAX_CONVERSATION_TURNS=50`, `UserResponse` structured replies, domain policy/tools context blocks via injected `self.taubench`.
- **Run the LoCoMo long-context memory QA agent** — `locomo` (`LoCoMoAgent`): single `PredictStrategy` call over deterministically assembled retrieval context (recent-sessions window, keyword-overlap retrieval, temporal index), tunable via `recent_sessions_count`/`max_retrieved_sessions`.
- **Run the Terminal Bench 1 agent** — `terminal-bench-1` (`TerminalBench1Agent`): single CodeAct loop, `max_iterations=100`, dynamic `terminal_tools` context over injected `self.terminal`.
- **Run the Terminal Bench 2 agent** — `terminal-bench-2` (`TerminalBench2Agent`): harder-task variant, `max_iterations=150`, mandatory verification step, exposes `self.context` to the LLM via `spec(self, "context", hidden=False)`.
- **Vending-Bench long-horizon simulation agent** — `VendingBenchAgent` (`agents/vendingbench.py`): `max_iterations=200`, loads `/app/simulation.py` `VendingSimulation(seed=42)`, dynamic balance/inventory state context block, writes `/app/result.json`. Class is implemented but NOT yet registered in `AGENT_CLASSES`, so it is not selectable via `--agent-type`.
- **Register a custom agent type** by adding a `"<type>": "module:ClassName"` entry to `AGENT_CLASSES` — the runner resolves it via dotted-path import **(custom)**.

### Container tool suites

- **Run shell commands and file ops against `/testbed`** with `SWEBenchLocalTools` — `execute`, `find_files`, `repo_tree`, `view_file`, `edit_file` (search/replace, refuses test files), `create_file`, `run_python` (heredoc with random delimiter), `run_tests`, `git_diff`, `git_status`, plus grep-based `find_definition`/`find_references`/`find_test_files`; `workdir` override on construction **(custom)**.
- **Run shell commands inside a Terminal Bench container** with `TerminalBenchTools.execute(command, timeout=300)` — combined stdout+stderr with exit code, default workdir `/app` **(custom)**.
- **Search markdown documentation** with the module-level helpers in `markdown_helpers` — `find_sections_matching_regex`, `find_sections_with_content_matching_regex`, `get_markdown_section`, `list_markdown_sections`, `get_markdown_section_sizes` (exposed to DABStep agent-generated code).
- **Match payment fees deterministically** with the DABStep module-level helpers — `fee_matches`, `applies_to_all`, `volume_matches`, `fraud_level_matches`, `capture_delay_matches`, `calc_fee`, `find_lowest_fee`, `round_eur`, `format_numeric_answer` (all callable from agent-generated code).

### Run without Harbor & extension points

- **Iterate locally without a container** with the `util/harbor/run_*_debug.py` scripts (`run_dabstep_debug.py`, `run_terminal_bench_debug.py`, `run_locomo_debug.py`, `run_membench_debug.py`) — run agents in-process via `eval_pipeline` with a common `--tasks N`/`--model` plus benchmark-specific flags (e.g. `--agent`/`--dabstep-model` for DABStep, `--question-types`/`--scenarios` for MemBench, `--task-names`/`--sparse`/`--branch` for Terminal Bench); completion-based scoring only **(opt-in)**.
- **Generate DABStep / Terminal Bench task dirs** with `util/harbor/generate_dabstep_tasks.py` and `generate_terminal_bench_tasks.py` (or `harbor adapter run --adapter <name>`) **(opt-in)**.
- **Build benchmark SIF images** with `util/harbor/build_terminal_bench_sifs.py` (`--skip-built`/`--dry-run`/per-task), `build_terminal_bench_sifs_nosudo.py`, `pull_swebench_sifs.py`, `pull_swebenchpro_sifs.py`, and `build_venv_tarballs.sh` **(opt-in)**.
- **Build an in-process self-improvement eval harness** with the `nemo_oo_agents_benchmarks.evaluation` package — `BenchmarkAdapter`/`BenchmarkEnvironment` ABCs, `SelfImprovementRunner`, `TraceAnalyzer`/`FailurePattern`, `MetricsCalculator`/`ImprovementMetrics`, and the `NemoOOAgentsAdapter` that maps tasks to agent classes via dotted-path import **(custom)**.

---

## Appendix — notable changes since the 2026-04-17 survey

Corrections and renames surfaced while re-verifying against current code. These are the items most likely to mislead anyone writing docs from memory or the old list:

- `@agent` / `@plan` decorators are **removed** — agents are plain `class X(Agent, llm=...)` with bare `...` methods.
- `agentdoc`, `context_blocks`, `unifiedllm`, and `trace_explorer` are now **core submodules** — import as `nemo_oo_agents.<name>` (`import agentdoc` no longer works).
- The `LibraryWriting` skill was renamed to **`SkillWriting`**; `SkillManager` is **removed** (use `SkillRegistry` + `discover_skills_dirs()`).
- The entire **layered-config + secrets + setup/credentials** system is new (`config/`, `layered_config.py`, `secrets.py`, the `install.sh` credential flow, `nemo-oo config`, TUI `settings.yaml`).
- The LLM registry is now **YAML-driven**; `MODELS` is the merged runtime dict populated via config layers, not a Python dict you mutate.
- `enable_tracing()` **no longer takes `trace_dir=`** — pass exporters, e.g. `exporters.jsonl(...)`. (The in-repo example comment is itself stale.)
- Snapshots do **not** restore LLM-defined methods — `AgentSnapshot.from_agent` skips callables. The old "resume incl LLM-defined methods" claim was never accurate.
- Experimental strategies (`ReflexionStrategy`, `CodeActLiteStrategy`, `PurePythonStrategy`) moved to `nemo_oo_agents.experimental`; legacy import paths emit `FutureWarning`.
- "Nexus" is fully renamed to **NeMo Flow** (extra `[nemo-flow]`, dep `nemo-flow`); a native **ATIF v1.7** trajectory exporter is new.
- TUI slash commands are now **skill-based** (`@slash_command`), not hard-coded `Command` classes.
- The evaluation surface is **`eval_pipeline`** (`util/eval_pipeline`); `nemo-oo eval` is a thin passthrough to it.
- Context truncation is now total-budget **eviction** (with an `EVICTED` notice in `ContextWindowStats`), not per-block trimming.
- Shell tooling was consolidated: the registered `nemo.shell` builtin is the new 4-method `ShellTools`; the verbose `ShellToolsLegacy` is effectively dead.

---

*Generated 2026-06-11 from branch `feat/setup-and-credentials` (current `main` + the incoming layered-config system). Source of truth: in-repo code and docs. Inventory totals 665 features across 22 capability areas.*
