# DABStep SFT Data Generation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate a NeMo RL SFT training dataset from DABStep trajectories by running opt63 with DeepSeek V3.2, filtering correct answers against `dabstep-full-solutions.jsonl`, and converting OTel traces to NeMo RL OpenAI format JSONL.

**Architecture:** Two-phase pipeline — Phase 1 runs opt63 on all 450 DABStep tasks with OTel tracing enabled, producing per-task trace files. Phase 2 filters tasks where our answer matches ground truth and converts the `acompletion` spans (which contain full LLM message histories) into NeMo RL format by flattening all sub-agent conversations into one chronological timeline per task.

**Tech Stack:** Python 3.13, `experiments/evaluation-ablations/run_ablation.py`, OTel traces (`.006trace.jsonl`), NeMo RL OpenAI format JSONL, DeepSeek V3.2 on NVIDIA NIM (`integrate.api.nvidia.com`).

---

## Context You Need

### Working directory
All work happens in the worktree:
```
/localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation/
```

### Key files to understand first
- `experiments/evaluation-ablations/run_ablation.py` — the evaluation runner (don't modify)
- `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py` — the agent to fix
- `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent_opt63.py` — second copy of opt63 to fix
- `experiments/evaluation-ablations/dabstep-full-solutions.jsonl` — ground truth answers (450 entries, fields: `task_id`, `agent_answer`)

### What `.006trace.jsonl` files look like
Each trace file is JSONL where each line is an OTel span dict with keys:
```
span_id, trace_id, parent_span_id, name, start_time, end_time, attributes, ...
```
The spans we care about are named `acompletion` with `attributes["openinference.span.kind"] == "LLM"`.
These contain:
- `attributes["llm.input_messages.N.message.role"]` → `"system"`, `"user"`, `"assistant"`, `"tool"`
- `attributes["llm.input_messages.N.message.content"]` → message text
- `attributes["llm.input_messages.N.message.tool_calls.N.tool_call.function.name"]` → tool name
- `attributes["llm.input_messages.N.message.tool_calls.N.tool_call.function.arguments"]` → JSON string
- `attributes["llm.input_messages.N.message.tool_calls.N.tool_call.id"]` → call ID
- `attributes["llm.input_messages.N.message.tool_call_id"]` → for tool role messages
- `attributes["llm.output_messages.0.message.role"]` → `"assistant"`
- `attributes["llm.output_messages.0.message.content"]` → text or empty
- `attributes["llm.output_messages.0.message.tool_calls.*"]` → tool calls in output
- `attributes["llm.tools.N.tool.json_schema"]` → JSON string of tool schema
- `start_time` → ISO timestamp string or float (for ordering spans)

### What the eval result JSON looks like
After a run, `nemo_oo_agents_dabstep.006eval.json` contains a list of task results. Each has:
- `task_id` — the DABStep task ID (e.g., `"1"`, `"2"`, ...)
- `answer` or `response` — what the agent returned
- `trace_file` — absolute or relative path to the `.006trace.jsonl` for this task

### NeMo RL target format
```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [
      {"id": "call_1", "type": "function", "function": {"name": "execute_python", "arguments": "{\"code\": \"...\"}"}}
    ]},
    {"role": "tool", "content": "...", "tool_call_id": "call_1"},
    {"role": "assistant", "content": "Final answer."}
  ],
  "tools": [
    {"type": "function", "function": {"name": "execute_python", "description": "...", "parameters": {...}}}
  ]
}
```
**Rule:** Last message MUST be `role: assistant`.

### Answer matching
Use exact-match (case-insensitive, strip whitespace) — same logic as `ExactMatchScorer`.

---

## Task 1: Inspect the eval result JSON format

Before writing any code, look at what the actual eval result JSON contains so the
`build_sft_dataset.py` script can parse it correctly.

**Files:**
- Read: `experiments/evaluation-ablations/run_ablation.py` (look for where results are written)

**Step 1: Find how results are saved**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
grep -n "006eval\|trace_file\|task_id\|results\[" experiments/evaluation-ablations/run_ablation.py | head -40
```

**Step 2: Find an existing eval JSON to inspect**

```bash
find . -name "*.006eval.json" | head -3
# Then inspect the first one:
python3 -c "
import json
with open('<path>') as f:
    data = json.load(f)
print(type(data))
if isinstance(data, list):
    print('keys in first item:', list(data[0].keys()))
    print('first item:', json.dumps(data[0], indent=2)[:500])
elif isinstance(data, dict):
    print('top-level keys:', list(data.keys()))
"
```

**Step 3: Note what fields contain task_id, answer, and trace_file path**

Write findings as a comment at the top of `build_sft_dataset.py` before writing any code.

---

## Task 2: Fix opt63 to use DeepSeek V3.2 as default model

**Files:**
- Modify: `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py`
- Modify: `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent_opt63.py`

**Background:**
Both files have a class definition:
```python
class RSCDABAgentHardOpt63(
    Agent,
    llm=FakeLLMClient(),
):
```
`FakeLLMClient` is a placeholder. We want the class to default to DeepSeek V3.2 on NIM.
But we don't want to break existing code that passes `llm=` at instantiation time.

The `CompletionClient` from `unifiedllm` is the right class. It accepts:
- `model` — litellm model string. For NIM: `"nvidia_nim/deepseek-ai/deepseek-v3.2"`
- `api_key` — the NVIDIA_API_KEY from environment
- `api_base` — `"https://integrate.api.nvidia.com/v1"`
- `temperature` — `0.0` for deterministic eval

**Step 1: Check how CompletionClient is imported in existing agents**

```bash
grep -n "CompletionClient\|from unifiedllm\|import.*Client" \
  experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py | head -10
```

**Step 2: Write a minimal test to verify the import and client creation works**

```bash
cat > /tmp/test_deepseek_client.py << 'EOF'
import os
import sys
sys.path.insert(0, '/localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation')
from dotenv import load_dotenv
load_dotenv('/localhome/local-rcabral/nemo_oo_agents/.env')

from unifiedllm import CompletionClient

client = CompletionClient(
    model="nvidia_nim/deepseek-ai/deepseek-v3.2",
    api_key=os.environ["NVIDIA_API_KEY"],
    api_base="https://integrate.api.nvidia.com/v1",
    temperature=0.0,
    max_tokens=100,
)
print(f"Client created: {client}")
print("PASS: CompletionClient for DeepSeek V3.2 created successfully")
EOF
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation && source .venv/bin/activate && python3 /tmp/test_deepseek_client.py
```

**Step 3: Add a default_llm factory function to opt63 (evaluation-ablations version)**

In `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py`, add after the imports:

```python
import os as _os
from unifiedllm import CompletionClient as _CompletionClient

def _default_llm():
    """Default LLM: DeepSeek V3.2 on NVIDIA NIM (MIT license, allowed for SFT generation)."""
    return _CompletionClient(
        model="nvidia_nim/deepseek-ai/deepseek-v3.2",
        api_key=_os.environ.get("NVIDIA_API_KEY", ""),
        api_base="https://integrate.api.nvidia.com/v1",
        temperature=0.0,
        max_tokens=4096,
    )
```

Then change the class definition:
```python
class RSCDABAgentHardOpt63(
    Agent,
    llm=_default_llm(),
):
```

**Step 4: Apply the same fix to the e2e_optimization copy**

Same change in:
`util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent_opt63.py`

**Step 5: Verify the agent can be imported without error**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
source .venv/bin/activate
python3 -c "
import sys
sys.path.insert(0, 'experiments/evaluation-ablations')
sys.path.insert(0, 'src')
from agents.rsc_dab_agent_hard_opt63 import RSCDABAgentHardOpt63
print('Import OK')
print(f'Default LLM: {RSCDABAgentHardOpt63._llm}')
"
```
Expected: prints `Import OK` and the LLM config.

**Step 6: Commit**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
git add experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py
git add util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent_opt63.py
git commit -m "feat: set DeepSeek V3.2 as default model for opt63 (SFT data generation)"
```

---

## Task 3: Launch the full DABStep run (background)

**Files:**
- Read: `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py` (just to confirm agent class name)
- Output to: `experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/`

**Background:**
This is a long-running job (~2-6 hours for 450 tasks at 10 concurrent). We launch it in the
background with `nohup` and a log file, then continue building the converter.

**Step 1: Confirm the agent class name**

```bash
grep "^class RSC\|^class DAB" \
  experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py
```
Expected: `class RSCDABAgentHardOpt63(`

**Step 2: Create the output directory**

```bash
mkdir -p experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun
```

**Step 3: Launch the full run in background**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
source .venv/bin/activate

nohup python experiments/evaluation-ablations/run_ablation.py \
  --agent-file experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt63.py \
  --benchmark dabstep \
  --provider nvidia \
  --model deepseek-ai/deepseek-v3.2 \
  --concurrent-tasks 10 \
  --output-dir experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun \
  > experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/run.log 2>&1 &

echo "PID: $!"
echo $! > experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/run.pid
```

**Step 4: Verify it started**

```bash
sleep 5
tail -20 experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/run.log
```
Expected: should show tasks starting, model loading, first results.

**Step 5: Monitor progress** (optional, check later)

```bash
# Count completed tasks
ls experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/traces/*.006trace.jsonl 2>/dev/null | wc -l
tail -5 experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/run.log
```

---

## Task 4: Write the SFT dataset builder — span extraction helpers

**Files:**
- Create: `experiments/evaluation-ablations/build_sft_dataset.py`

This task writes just the helper functions for extracting messages from trace spans.
We do this BEFORE the run finishes so it's ready to go.

**Step 1: Create the file with helpers and a unit test**

```python
# experiments/evaluation-ablations/build_sft_dataset.py
"""Build NeMo RL SFT dataset from DABStep opt63 traces.

Usage:
    python build_sft_dataset.py \
        --eval-json results/dabstep/opt63_deepseek_fullrun/nemo_oo_agents_dabstep.006eval.json \
        --solutions dabstep-full-solutions.jsonl \
        --traces-dir results/dabstep/opt63_deepseek_fullrun/traces/ \
        --output dabstep_sft_deepseek_v3.2_20260224.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Span extraction helpers
# ---------------------------------------------------------------------------


def extract_messages_from_span(span: dict) -> list[dict]:
    """Extract messages list from a single acompletion span's attributes.

    Reconstructs the ordered list of messages from flat attribute keys like:
        llm.input_messages.0.message.role
        llm.input_messages.0.message.content
        llm.input_messages.1.message.tool_calls.0.tool_call.function.name
        ...
        llm.output_messages.0.message.role
        llm.output_messages.0.message.content

    Returns a list of message dicts in order: all input messages, then the
    last output message (the assistant response for this turn).
    """
    attrs = span.get("attributes", {})

    # --- Parse input messages ---
    # Group by message index
    input_msgs: dict[int, dict] = {}
    tool_call_by_msg: dict[int, dict[int, dict]] = {}  # msg_idx -> {call_idx -> call_dict}

    for key, value in attrs.items():
        # llm.input_messages.N.message.role
        m = re.match(r"llm\.input_messages\.(\d+)\.message\.(.+)$", key)
        if not m:
            continue
        msg_idx = int(m.group(1))
        field = m.group(2)
        if msg_idx not in input_msgs:
            input_msgs[msg_idx] = {}

        if field == "role":
            input_msgs[msg_idx]["role"] = value
        elif field == "content":
            input_msgs[msg_idx]["content"] = value
        elif field == "tool_call_id":
            input_msgs[msg_idx]["tool_call_id"] = value
        else:
            # tool_calls.N.tool_call.function.name/arguments/id
            tc_m = re.match(r"tool_calls\.(\d+)\.tool_call\.(.+)$", field)
            if tc_m:
                tc_idx = int(tc_m.group(1))
                tc_field = tc_m.group(2)
                if msg_idx not in tool_call_by_msg:
                    tool_call_by_msg[msg_idx] = {}
                if tc_idx not in tool_call_by_msg[msg_idx]:
                    tool_call_by_msg[msg_idx][tc_idx] = {}
                tc = tool_call_by_msg[msg_idx][tc_idx]
                if tc_field == "id":
                    tc["id"] = value
                elif tc_field == "function.name":
                    tc.setdefault("function", {})["name"] = value
                elif tc_field == "function.arguments":
                    tc.setdefault("function", {})["arguments"] = value

    # Attach tool_calls to messages
    for msg_idx, tc_map in tool_call_by_msg.items():
        if msg_idx in input_msgs:
            tcs = [tc_map[i] for i in sorted(tc_map.keys())]
            for tc in tcs:
                tc["type"] = "function"
            input_msgs[msg_idx]["tool_calls"] = tcs

    # Build sorted input messages list
    sorted_input = [input_msgs[i] for i in sorted(input_msgs.keys())]

    # --- Parse output message ---
    output_msg: dict = {}
    output_tool_calls: dict[int, dict] = {}

    for key, value in attrs.items():
        m = re.match(r"llm\.output_messages\.0\.message\.(.+)$", key)
        if not m:
            continue
        field = m.group(1)
        if field == "role":
            output_msg["role"] = value
        elif field == "content":
            output_msg["content"] = value
        else:
            tc_m = re.match(r"tool_calls\.(\d+)\.tool_call\.(.+)$", field)
            if tc_m:
                tc_idx = int(tc_m.group(1))
                tc_field = tc_m.group(2)
                if tc_idx not in output_tool_calls:
                    output_tool_calls[tc_idx] = {}
                tc = output_tool_calls[tc_idx]
                if tc_field == "id":
                    tc["id"] = value
                elif tc_field == "function.name":
                    tc.setdefault("function", {})["name"] = value
                elif tc_field == "function.arguments":
                    tc.setdefault("function", {})["arguments"] = value

    if output_tool_calls:
        tcs = [output_tool_calls[i] for i in sorted(output_tool_calls.keys())]
        for tc in tcs:
            tc["type"] = "function"
        output_msg["tool_calls"] = tcs

    # Combine: return input messages + output message
    messages = sorted_input.copy()
    if output_msg:
        messages.append(output_msg)
    return messages


def extract_tools_from_span(span: dict) -> list[dict]:
    """Extract tools list from span attributes (llm.tools.N.tool.json_schema)."""
    attrs = span.get("attributes", {})
    tools_by_idx: dict[int, dict] = {}
    for key, value in attrs.items():
        m = re.match(r"llm\.tools\.(\d+)\.tool\.json_schema$", key)
        if m:
            idx = int(m.group(1))
            try:
                tools_by_idx[idx] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return [tools_by_idx[i] for i in sorted(tools_by_idx.keys())]


def span_start_time(span: dict) -> float:
    """Return start_time as float (seconds since epoch) for sorting."""
    st = span.get("start_time", 0)
    if isinstance(st, (int, float)):
        return float(st)
    if isinstance(st, str):
        # ISO format: "2026-02-24T12:00:00.000000Z"
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            return 0.0
    return 0.0


def build_sft_record_from_trace(trace_file: Path) -> dict | None:
    """Build a single NeMo RL SFT record from a .006trace.jsonl file.

    Strategy:
    1. Load all acompletion (LLM kind) spans
    2. Sort by start_time
    3. For the first span, take ALL input messages (establishes system + initial turns)
    4. For subsequent spans, take only the NEW messages (skip what was already in previous span)
       This avoids duplicating the growing conversation history
    5. Always append the output message from each span
    6. Collect tools from the first span that has them

    Returns None if no acompletion spans found.
    """
    spans = []
    with open(trace_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Filter to acompletion LLM spans only
    acomp_spans = [
        s for s in spans
        if s.get("name") == "acompletion"
        and s.get("attributes", {}).get("openinference.span.kind") == "LLM"
    ]

    if not acomp_spans:
        return None

    # Sort by start_time
    acomp_spans.sort(key=span_start_time)

    # Build merged conversation
    all_messages: list[dict] = []
    tools: list[dict] = []
    prev_input_count = 0

    for span in acomp_spans:
        span_messages = extract_messages_from_span(span)
        if not span_messages:
            continue

        # Count input messages in this span (all except the last, which is the output)
        # Actually: span_messages = all input messages + output message
        # The input messages are everything except the last appended output
        # But extract_messages_from_span returns input_msgs + output_msg
        # So len(span_messages) - 1 = number of input messages

        n_input = len(span_messages) - 1  # last is output
        if n_input < 0:
            continue

        if not all_messages:
            # First span: take everything
            all_messages = span_messages
        else:
            # Subsequent spans: the input messages of this span include the growing
            # conversation history. We only want the NEW messages since last time.
            # The conversation grows by appending: (tool result, new assistant turn...)
            # So new input messages start at index prev_input_count
            new_input_msgs = span_messages[prev_input_count:n_input]
            output_msg = span_messages[-1] if span_messages else None
            all_messages.extend(new_input_msgs)
            if output_msg:
                all_messages.append(output_msg)

        prev_input_count = n_input + 1  # +1 because output becomes next input

        # Collect tools from first span that has them
        if not tools:
            tools = extract_tools_from_span(span)

    if not all_messages:
        return None

    # Ensure last message is assistant
    if all_messages[-1].get("role") != "assistant":
        # Find last assistant message and truncate there
        for i in range(len(all_messages) - 1, -1, -1):
            if all_messages[i].get("role") == "assistant":
                all_messages = all_messages[: i + 1]
                break
        else:
            return None  # No assistant messages at all

    record: dict[str, Any] = {"messages": all_messages}
    if tools:
        record["tools"] = tools
    return record


# ---------------------------------------------------------------------------
# Answer matching
# ---------------------------------------------------------------------------


def answers_match(agent_answer: str, correct_answer: str) -> bool:
    """Exact match after stripping whitespace and lowercasing."""
    return agent_answer.strip().lower() == correct_answer.strip().lower()
```

**Step 2: Add a quick unit test at the bottom of the file**

```python
# ---------------------------------------------------------------------------
# Self-test (run with: python build_sft_dataset.py --selftest)
# ---------------------------------------------------------------------------

def _run_selftest():
    """Quick smoke test of span extraction with synthetic data."""
    fake_span = {
        "name": "acompletion",
        "start_time": 1000.0,
        "attributes": {
            "openinference.span.kind": "LLM",
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": "You are helpful.",
            "llm.input_messages.1.message.role": "user",
            "llm.input_messages.1.message.content": "What is 2+2?",
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.tool_calls.0.tool_call.id": "call_abc",
            "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "execute_python",
            "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"code": "print(2+2)"}',
            "llm.tools.0.tool.json_schema": '{"type":"function","function":{"name":"execute_python","description":"run code"}}',
        },
    }

    msgs = extract_messages_from_span(fake_span)
    assert len(msgs) == 3, f"Expected 3 msgs, got {len(msgs)}"
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["tool_calls"][0]["id"] == "call_abc"
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "execute_python"

    tools = extract_tools_from_span(fake_span)
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "execute_python"

    assert answers_match("  73.15  ", "73.15")
    assert answers_match("NL", "nl")
    assert not answers_match("73.15", "73.16")

    print("PASS: all self-tests passed")
```

**Step 3: Run the self-test**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
source .venv/bin/activate
python experiments/evaluation-ablations/build_sft_dataset.py --selftest
```
Expected: `PASS: all self-tests passed`

---

## Task 5: Write the main() function of build_sft_dataset.py

**Files:**
- Modify: `experiments/evaluation-ablations/build_sft_dataset.py`

Add the `main()` function and CLI argument handling. At this point the full run may
or may not be done — the script should work on whatever tasks are available.

**Step 1: Inspect the actual eval JSON from a previous run to confirm the format**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
python3 -c "
import json
# Look at an existing eval result
import glob
files = glob.glob('experiments/evaluation-ablations/results/**/*.006eval.json', recursive=True)
print('Found:', files[:3])
if files:
    with open(files[0]) as f:
        data = json.load(f)
    print('Type:', type(data))
    if isinstance(data, list) and data:
        print('First item keys:', list(data[0].keys()))
        print('First item:', json.dumps(data[0], indent=2)[:400])
    elif isinstance(data, dict):
        print('Dict keys:', list(data.keys())[:10])
"
```

**Step 2: Write main() based on the actual format**

Add to `build_sft_dataset.py`:

```python
def load_solutions(solutions_file: Path) -> dict[str, str]:
    """Load ground-truth answers. Returns {task_id: correct_answer}."""
    solutions = {}
    with open(solutions_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            task_id = str(entry["task_id"])
            solutions[task_id] = str(entry["agent_answer"])
    return solutions


def load_eval_results(eval_json: Path) -> list[dict]:
    """Load eval results JSON. Returns list of task result dicts."""
    with open(eval_json) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    # If it's a dict with a results key
    if isinstance(data, dict):
        for key in ["results", "tasks", "samples"]:
            if key in data:
                return data[key]
        # Flatten dict of {task_id: result}
        return [{"task_id": k, **v} for k, v in data.items()]
    return []


def find_trace_file(task_result: dict, traces_dir: Path) -> Path | None:
    """Find the trace file for a task result."""
    # Try explicit trace_file field first
    if "trace_file" in task_result:
        p = Path(task_result["trace_file"])
        if p.exists():
            return p
        # Try relative to traces_dir
        p2 = traces_dir / p.name
        if p2.exists():
            return p2

    # Fall back: search traces_dir for a file containing the task_id
    task_id = str(task_result.get("task_id", ""))
    if not task_id or not traces_dir.exists():
        return None

    for f in traces_dir.glob("*.006trace.jsonl"):
        if task_id in f.name:
            return f
    return None


def get_agent_answer(task_result: dict) -> str:
    """Extract agent answer from task result dict."""
    for key in ["answer", "response", "result", "output"]:
        if key in task_result and task_result[key] is not None:
            return str(task_result[key]).strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description="Build NeMo RL SFT dataset from DABStep traces")
    parser.add_argument("--eval-json", required=False, help="Path to .006eval.json from full run")
    parser.add_argument("--solutions", default="dabstep-full-solutions.jsonl",
                        help="Ground truth solutions JSONL")
    parser.add_argument("--traces-dir", required=False, help="Directory containing .006trace.jsonl files")
    parser.add_argument("--output", required=False, help="Output JSONL path")
    parser.add_argument("--selftest", action="store_true", help="Run self-tests and exit")
    args = parser.parse_args()

    if args.selftest:
        _run_selftest()
        return

    if not args.eval_json or not args.output:
        parser.error("--eval-json and --output are required (unless --selftest)")

    # Resolve paths relative to this script's directory
    script_dir = Path(__file__).parent
    eval_json = Path(args.eval_json)
    if not eval_json.is_absolute():
        eval_json = script_dir / eval_json
    solutions_file = Path(args.solutions)
    if not solutions_file.is_absolute():
        solutions_file = script_dir / solutions_file
    traces_dir = Path(args.traces_dir) if args.traces_dir else eval_json.parent / "traces"
    output_file = Path(args.output)
    if not output_file.is_absolute():
        output_file = script_dir / output_file

    # Load inputs
    print(f"Loading solutions from: {solutions_file}")
    solutions = load_solutions(solutions_file)
    print(f"  {len(solutions)} ground-truth answers loaded")

    print(f"Loading eval results from: {eval_json}")
    eval_results = load_eval_results(eval_json)
    print(f"  {len(eval_results)} task results loaded")

    # Filter and convert
    n_correct = 0
    n_converted = 0
    n_no_trace = 0
    n_no_spans = 0

    with open(output_file, "w") as out:
        for task_result in eval_results:
            task_id = str(task_result.get("task_id", ""))
            agent_answer = get_agent_answer(task_result)

            if task_id not in solutions:
                continue
            correct_answer = solutions[task_id]

            if not answers_match(agent_answer, correct_answer):
                continue
            n_correct += 1

            # Find trace file
            trace_file = find_trace_file(task_result, traces_dir)
            if not trace_file:
                print(f"  WARNING: no trace file for task {task_id}", file=sys.stderr)
                n_no_trace += 1
                continue

            # Build SFT record
            record = build_sft_record_from_trace(trace_file)
            if record is None:
                print(f"  WARNING: no acompletion spans in trace for task {task_id}", file=sys.stderr)
                n_no_spans += 1
                continue

            record["task_id"] = task_id
            record["agent_answer"] = agent_answer
            out.write(json.dumps(record) + "\n")
            n_converted += 1

    print(f"\nResults:")
    print(f"  Total tasks evaluated: {len(eval_results)}")
    print(f"  Correct answers:       {n_correct}")
    print(f"  SFT records written:   {n_converted}")
    print(f"  Missing trace files:   {n_no_trace}")
    print(f"  No acompletion spans:  {n_no_spans}")
    print(f"\nOutput: {output_file}")


if __name__ == "__main__":
    main()
```

**Step 3: Run self-test again to make sure nothing broke**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
source .venv/bin/activate
python experiments/evaluation-ablations/build_sft_dataset.py --selftest
```

**Step 4: Commit**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
git add experiments/evaluation-ablations/build_sft_dataset.py
git commit -m "feat: add build_sft_dataset.py for NeMo RL SFT data generation

Converts correct DABStep opt63 traces to NeMo RL OpenAI format JSONL.
Flattens all sub-agent conversations (RulesLawyer, SolutionVerifier)
into a single chronological timeline per task."
```

---

## Task 6: Dry-run the converter on existing traces

Before the full run finishes, validate the converter works end-to-end using
traces from a PREVIOUS run (not the new full run).

**Step 1: Find a previous run with trace files**

```bash
find /localhome/local-rcabral/nemo_oo_agents/util/e2e_optimization/src/e2e_optimization/examples/dabstep/results \
  -name "*.006trace.jsonl" | head -5
```

**Step 2: Run the converter on a single trace file directly**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
source .venv/bin/activate
python3 -c "
import json
from pathlib import Path
import sys
sys.path.insert(0, 'experiments/evaluation-ablations')
from build_sft_dataset import build_sft_record_from_trace

# Use one of the traces from a previous opt63 run
trace = Path('<path_to_trace_from_step_1>')
record = build_sft_record_from_trace(trace)
if record is None:
    print('ERROR: got None')
else:
    print(f'Messages: {len(record[\"messages\"])}')
    print(f'Tools: {len(record.get(\"tools\", []))}')
    print(f'Roles: {[m[\"role\"] for m in record[\"messages\"]]}')
    print(f'Last message role: {record[\"messages\"][-1][\"role\"]}')
    assert record['messages'][-1]['role'] == 'assistant', 'Last message must be assistant!'
    print('PASS: NeMo RL format valid')
"
```

Expected:
- 20-80 messages
- Mix of system/user/assistant/tool roles
- Last message is `assistant`

**Step 3: If there are issues, fix them in `build_sft_record_from_trace`**

Common issues to watch for:
- Tool call arguments stored as JSON string vs dict — NeMo RL expects string
- Missing `type: "function"` on tool calls
- System prompts repeated across sub-agents (dedup: skip system prompts after first span)

---

## Task 7: Wait for full run, then generate the SFT dataset

**Step 1: Check if full run is done**

```bash
# Check run status
cat experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/run.pid \
  | xargs ps -p 2>/dev/null || echo "Process not running (may be done)"

# Check results
tail -20 experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/run.log
ls experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/traces/ | wc -l
```

**Step 2: Once eval JSON is available, run the converter**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
source .venv/bin/activate

TODAY=$(date +%Y%m%d)
python experiments/evaluation-ablations/build_sft_dataset.py \
  --eval-json experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/nemo_oo_agents_dabstep.006eval.json \
  --solutions experiments/evaluation-ablations/dabstep-full-solutions.jsonl \
  --traces-dir experiments/evaluation-ablations/results/dabstep/opt63_deepseek_fullrun/traces/ \
  --output experiments/evaluation-ablations/dabstep_sft_deepseek_v3.2_${TODAY}.jsonl
```

Expected output (rough):
```
Loading solutions from: .../dabstep-full-solutions.jsonl
  450 ground-truth answers loaded
Loading eval results from: .../nemo_oo_agents_dabstep.006eval.json
  450 task results loaded

Results:
  Total tasks evaluated: 450
  Correct answers:       200-300
  SFT records written:   180-280
  Missing trace files:   0-20
  No acompletion spans:  0-5

Output: .../dabstep_sft_deepseek_v3.2_YYYYMMDD.jsonl
```

**Step 3: Validate the output JSONL**

```bash
python3 -c "
import json
from pathlib import Path

output = Path('experiments/evaluation-ablations/dabstep_sft_deepseek_v3.2_$(date +%Y%m%d).jsonl')
records = []
with open(output) as f:
    for line in f:
        records.append(json.loads(line))

print(f'Total records: {len(records)}')
print()

# Check structure
errors = []
for i, r in enumerate(records):
    msgs = r.get('messages', [])
    if not msgs:
        errors.append(f'Record {i}: no messages')
        continue
    if msgs[-1]['role'] != 'assistant':
        errors.append(f'Record {i}: last msg is {msgs[-1][\"role\"]} not assistant')
    roles = [m['role'] for m in msgs]
    if roles[0] != 'system':
        errors.append(f'Record {i}: first msg is {roles[0]} not system')

if errors:
    print('ERRORS:')
    for e in errors[:10]:
        print(f'  {e}')
else:
    print('PASS: all records valid')

# Stats
msg_counts = [len(r['messages']) for r in records]
print(f'Message counts: min={min(msg_counts)}, max={max(msg_counts)}, avg={sum(msg_counts)/len(msg_counts):.1f}')
print(f'Tools present: {sum(1 for r in records if \"tools\" in r)} / {len(records)}')
"
```

**Step 4: Commit the final script (if any changes from dry-run)**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
git add experiments/evaluation-ablations/build_sft_dataset.py
git commit -m "fix: finalize build_sft_dataset.py after dry-run validation" || echo "nothing to commit"
```

---

## Task 8: Update documentation and create PR

**Files:**
- Create/update: `experiments/evaluation-ablations/README.md` (add SFT section)
- Commit all changes

**Step 1: Add SFT section to evaluation-ablations README**

If README doesn't exist:
```bash
cat >> experiments/evaluation-ablations/README.md << 'EOF'

## SFT Data Generation

Generate NeMo RL SFT training data from correct DABStep trajectories:

```bash
# 1. Run opt63 on full dataset (uses DeepSeek V3.2 by default)
source ../../.venv/bin/activate
python run_ablation.py \
  --agent-file agents/rsc_dab_agent_hard_opt63.py \
  --benchmark dabstep \
  --provider nvidia \
  --model deepseek-ai/deepseek-v3.2 \
  --concurrent-tasks 10 \
  --output-dir results/dabstep/opt63_deepseek_fullrun

# 2. Build SFT dataset from correct traces
python build_sft_dataset.py \
  --eval-json results/dabstep/opt63_deepseek_fullrun/nemo_oo_agents_dabstep.006eval.json \
  --solutions dabstep-full-solutions.jsonl \
  --output dabstep_sft_deepseek_v3.2_YYYYMMDD.jsonl
```

Output format: NeMo RL OpenAI format JSONL (see docs/training-data-dual-track-plan.md).
Use `use_preserving_dataset: true` in NeMo RL training config.
EOF
```

**Step 2: Final commit and push**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/.worktrees/sft-data-generation
git add -A
git status
git commit -m "docs: add SFT generation instructions to evaluation-ablations README" || echo "nothing to commit"
git push -u origin feat/sft-data-generation
```

**Step 3: Create MR**

```bash
glab mr create \
  --title "feat: DABStep SFT data generation pipeline (opt63 + DeepSeek V3.2)" \
  --description "$(cat <<'EOF'
## Summary
- Moves bash MCP instruction to user-level ~/.claude/CLAUDE.md (persists across worktrees)
- Adds .worktrees/ to .gitignore
- Fixes opt63 to use DeepSeek V3.2 (MIT, 77.2% SWE-bench) as default model for SFT generation
- Adds build_sft_dataset.py: filters correct traces and converts to NeMo RL OpenAI format
- See docs/plans/2026-02-24-sft-data-generation-design.md for full design

## Test plan
- [x] build_sft_dataset.py --selftest passes
- [x] Dry-run on existing traces validates output format
- [ ] Full run on 450 tasks completes
- [ ] SFT JSONL validates: all records end with assistant, first is system
EOF
)" \
  --source-branch feat/sft-data-generation \
  --target-branch main
```

---

## Quick Reference

| What | Command |
|------|---------|
| Check full run progress | `tail -f results/dabstep/opt63_deepseek_fullrun/run.log` |
| Count completed traces | `ls results/dabstep/opt63_deepseek_fullrun/traces/ \| wc -l` |
| Run self-test | `python build_sft_dataset.py --selftest` |
| Build SFT dataset | `python build_sft_dataset.py --eval-json ... --solutions ... --output ...` |
| Validate output | `python -c "import json; [json.loads(l) for l in open('output.jsonl')]"` |
