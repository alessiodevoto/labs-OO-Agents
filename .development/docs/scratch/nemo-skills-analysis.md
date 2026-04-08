# NeMo Skills Evaluation Analysis

## What is NeMo Skills?

**Purpose**: End-to-end LLM development pipelines - synthetic data generation, model training, evaluation

**Focus**: Math, coding, science benchmarks (not agentic tool-calling)

## Evaluation System Overview

Located in `3p/Skills/nemo_skills/evaluation/`

### Architecture

**Two-Phase Approach** (like NAT):
1. **Generation Phase**: LLM generates responses → saves to JSONL
2. **Evaluation Phase**: Scorer processes JSONL → adds scores

```python
class BaseEvaluator(ABC):
    async def eval_full(self) -> None:
        """Evaluate full dataset in batch mode."""
        semaphore = asyncio.Semaphore(self.num_parallel_requests)

        async def process_line(line_data):
            async with semaphore:
                updates = await self.eval_single(line_data)  # Score one sample
                merged = dict(line_data)
                merged.update(updates)  # Merge score back
                return merged

        # Read all lines from input file
        with open(self.config.input_file, "rt") as fin:
            tasks = [asyncio.create_task(process_line(json.loads(line))) for line in fin]

        # Write results back to file (in-place modification!)
        temp_file = self.config.input_file + "-tmp"
        with open(temp_file, "wt") as f:
            for task in tqdm.tqdm(tasks):
                line = await task
                f.write(json.dumps(line) + "\n")

        os.replace(temp_file, self.config.input_file)  # Atomic replace
```

**Key Pattern**: Reads JSONL, processes in parallel, writes back to same file.

### Benchmark-Specific Evaluators

**Example: BFCL Evaluator** (`bfcl.py`):

```python
def eval_bfcl(cfg):
    """Wrapper around BFCL's CLI evaluation tool."""

    # Convert Nemo-Skills format → BFCL format
    bfcl_input = _convert_to_bfcl_format(jsonl_file, ...)

    # Call BFCL CLI as subprocess
    cmd = f"OPENAI_API_KEY=dummy bfcl evaluate --model {model} --test-category {cat}"
    subprocess.run(cmd, shell=True, check=True, timeout=300)

    # Read BFCL scores and merge back into original file
    _merge_bfcl_results(jsonl_file, bfcl_input, score_file)
```

**Example: LiveCodeBench Evaluator** (`livecodebench.py`):

```python
def eval_livecodebench(cfg):
    """Wrapper around LiveCodeBench evaluation."""

    # Preprocess: Convert format, add required fields
    samples = _preprocess_and_validate_file(jsonl_file, language)

    # Execute in sandbox (Docker container)
    async with sandbox_context(config.sandbox) as sandbox:
        results = await sandbox.execute_code(code, language, timeout)

    # Postprocess: Merge results back
    _postprocess_results(jsonl_file, samples)
```

### Common Pattern

**All evaluators**:
1. Read JSONL with generations
2. Convert to benchmark-specific format
3. Call external scoring tool (subprocess or API)
4. Merge scores back into JSONL
5. Overwrite original file

**Concurrency**:
- Semaphore-based (like ours!)
- `num_parallel_requests` parameter
- Progress bars with tqdm

---

## What's Good About NeMo Skills

### 1. ✅ In-Place File Modification

```python
# Write to temp file
temp_file = self.config.input_file + "-tmp"
with open(temp_file, "wt") as f:
    for task in tqdm.tqdm(tasks):
        line = await task
        f.write(json.dumps(line) + "\n")

# Atomic replace (crash-safe!)
os.replace(temp_file, self.config.input_file)
```

**Benefits**:
- Atomic writes (no partial files)
- Simple resume strategy (check if fields exist)
- Single source of truth

**We could adopt this** for incremental writes in runner.

### 2. ✅ Evaluator = Scorer Only

**Clean separation**:
- Generation pipeline produces JSONL
- Evaluator only scores, doesn't generate
- Each evaluator is ~100-200 lines

**Example evaluators**:
- `bfcl.py` - 142 lines
- `livecodebench.py` - 300 lines
- `math.py` - 150 lines

**Simple, focused, reusable.**

### 3. ✅ External Tool Integration

**Pragmatic approach**: Call official scorers via subprocess

```python
# Don't reimplement scoring - use official tools
subprocess.run(f"bfcl evaluate --model {model} ...", shell=True)
```

**Benefits**:
- Use official scorers (no drift)
- Less maintenance
- Correctness by construction

**We already do this** for some benchmarks (BFCL).

### 4. ✅ Sandbox Support

**Docker-based code execution**:
```python
async with sandbox_context(config.sandbox) as sandbox:
    result = await sandbox.execute_code(code, language="python", timeout=6)
```

**For**: LiveCodeBench, code execution, formal proofs

**We could use this** for secure code execution.

---

## What Doesn't Fit Our Needs

### 1. ❌ Two-Phase Only (Generation → Evaluation)

**NeMo Skills assumes**:
```
Step 1: Generate responses (LLM inference)
        ↓ (save to JSONL)
Step 2: Evaluate responses (scorer)
```

**Our agent execution** needs tighter integration:
- **BFCL**: Agent generates, we score immediately (feedback loop possible)
- **InterCode**: Multi-turn with environment (need to interleave execution and scoring)
- **TAU-Bench**: Complex state management (not just generation)

**Their model doesn't fit agentic benchmarks.**

### 2. ❌ No Agent Execution

**NeMo Skills focuses on**:
- LLM inference (prompt → response)
- Batch generation at scale
- Slurm job distribution

**Not designed for**:
- Agent method invocation
- Tool calling with state
- Multi-turn interactions
- Environment simulation

**We need agent execution, not just LLM calls.**

### 3. ❌ File-Centric (Not Object-Oriented)

**Pattern**: Everything reads/writes JSONL files

```python
def eval_bfcl(cfg):
    with open(cfg.input_file, "rt") as fin:
        for line in fin:
            sample = json.loads(line)
            # Process...

    with open(cfg.input_file, "wt") as fout:
        fout.write(json.dumps(updated_sample) + "\n")
```

**Problems**:
- Hard to compose (files as interfaces)
- No in-memory task objects
- Forces serialization/deserialization

**Our architecture** uses:
```python
tasks = [EvaluationTask(...) for ...]
results = await runner.run_evaluation(tasks)
```

**Much more flexible** - Python objects, not files.

### 4. ❌ CLI-Based (Not Programmatic API)

**Usage**:
```bash
# Command-line only
ns eval bfcl --input-file results.jsonl --model gpt-4
```

**No Python API** for programmatic use:
```python
# Can't do this:
evaluator = BFCLEvaluator(config)
results = await evaluator.run(tasks)
```

**We need programmatic API** for:
- eval_pipeline Python API
- Integration tests
- Flexible composition

---

## What We Should Adopt

### ✅ 1. Atomic File Writes

**Adopt this pattern**:

```python
class JsonlWriter(ResultWriter):
    def finalize(self, metadata: dict):
        """Write all results atomically."""

        # Write to temp file
        temp_file = self.output_file.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            for result in self.results:
                f.write(json.dumps(result.to_dict()) + '\n')

        # Atomic replace
        temp_file.replace(self.output_file)
```

**Benefits**:
- Crash-safe writes
- No partial files
- Simple resume (check if file exists)

### ✅ 2. Subprocess-Based Scorers

**When appropriate, use official scorers**:

```python
class BFCLAdapter(ExecutionAdapter):
    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        # Agent generates response
        agent = self.agent_factory()
        response = await agent.solve(...)

        # Write to temp file in BFCL format
        bfcl_file = self._convert_to_bfcl(task, response)

        # Call official BFCL scorer
        subprocess.run(f"bfcl evaluate --input {bfcl_file} ...", shell=True, check=True)

        # Read score back
        score = self._read_bfcl_score(bfcl_file)

        return EvaluationResult(
            task_id=task.task_id,
            success=score["is_correct"],
            output=response,
            metadata={"score": score},
        )
```

**Benefits**:
- Use official scorers
- Stay current with benchmark updates
- Less code to maintain

### ✅ 3. Progress Bars with Context

**Adopt their pattern**:

```python
for task in tqdm.tqdm(tasks, total=len(tasks), desc=f"Evaluating {benchmark_name}"):
    result = await task
    writer.write_result(result)
```

**Better than** our current approach:
```python
pbar = tqdm(total=len(tasks), desc="Running evaluation")

def on_complete(task_id, result):
    pbar.update(1)
```

**Their pattern is simpler.**

### ❌ Don't Adopt

- Two-phase generation/evaluation model (doesn't fit agents)
- File-centric architecture (less flexible)
- CLI-only interface (need programmatic API)
- Evaluators as separate phase (need integrated execution)

---

## Comparison Matrix

| Aspect | NeMo Skills | Our Architecture | Winner |
|--------|-------------|------------------|--------|
| **Execution model** | Generation → Evaluation | Flexible (adapter decides) | **Ours** |
| **Agent support** | ❌ LLM inference only | ✅ Direct agent methods | **Ours** |
| **File handling** | ✅ Atomic writes | ⚠️ Need to add | **NeMo** |
| **Concurrency** | ✅ Semaphore + gather | ✅ Same (Layer 0) | Tie |
| **Progress bars** | ✅ tqdm with context | ⚠️ Callback-based | **NeMo** |
| **Benchmark scorers** | ✅ Subprocess wrappers | ⚠️ Python integration | **NeMo** |
| **Programmatic API** | ❌ CLI only | ✅ Python API | **Ours** |
| **Object-oriented** | ❌ File-centric | ✅ Task/Result objects | **Ours** |
| **Clean architecture** | ⚠️ Monolithic | ✅ 5 layers | **Ours** |
| **Multi-turn support** | ❌ Single-turn only | ✅ Adapter handles it | **Ours** |

---

## Recommendation

### ❌ Don't Use NeMo Skills Evaluation

**Reasons**:
1. **Two-phase model** - Doesn't fit agent execution patterns
2. **No agent support** - Built for LLM inference, not agentic workflows
3. **File-centric** - Less flexible than object-oriented approach
4. **CLI-only** - Need programmatic API

### ✅ Adopt These Patterns

1. **Atomic file writes** - `temp_file.replace(output_file)`
2. **Subprocess scorers** - Call official benchmark tools when appropriate
3. **Better progress bars** - `tqdm` with iteration context
4. **Sandbox support** - For secure code execution (LiveCodeBench, etc.)

### 🎯 Updated Implementation Plan

Add to our Layer 2 (Runner):

```python
class JsonlWriter(ResultWriter):
    def __init__(self, output_file: Path):
        self.output_file = output_file
        self.results: list[EvaluationResult] = []

    def write_result(self, result: EvaluationResult):
        """Buffer results (don't write immediately)."""
        self.results.append(result)

    def finalize(self, metadata: dict):
        """Write all results atomically."""
        temp_file = self.output_file.with_suffix('.tmp')

        with open(temp_file, 'w') as f:
            for result in self.results:
                f.write(json.dumps(result.to_dict()) + '\n')

        # Atomic replace (crash-safe!)
        temp_file.replace(self.output_file)
```

Add to benchmark adapters (when appropriate):

```python
class BFCLAdapter(ExecutionAdapter):
    def _call_official_scorer(self, task_file: Path) -> dict:
        """Call BFCL CLI for official scoring."""
        subprocess.run(
            f"bfcl evaluate --input {task_file} --model {self.model}",
            shell=True,
            check=True,
            timeout=300,
        )
        return self._read_bfcl_results(task_file)
```

---

## Conclusion

**NeMo Skills is excellent for LLM inference pipelines** (generation at scale, Slurm distribution, model training).

**But we're building agent evaluation** with different needs:
- Direct agent method invocation
- Multi-turn interactions
- Environment simulation
- Programmatic API
- Flexible execution patterns

**We should**:
- ✅ Keep our clean 5-layer architecture
- ✅ Adopt atomic file writes from NeMo
- ✅ Use subprocess for official scorers (when appropriate)
- ✅ Improve progress bars (tqdm iteration)

**This gives us**: Clean architecture + Better file handling + Official scoring tools.
