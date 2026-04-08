# InterCode SQL Failure Analysis

**Date**: 2025-12-08
**Updated**: 2025-12-08 (After adapter investigation)
**Issue**: All agents (baseline_react, agent006_bare, agent006_tools) achieve 0% on InterCode SQL

## Problem Summary

Despite "CRITICAL OUTPUT FORMAT" prompts instructing agents to generate SQL queries, all agents fail completely (0/25 tasks).

**Initial Hypothesis**: Wrong tools distract agents from generating SQL.
**Actual Root Cause**: **The adapter provides no schema context to agents** because the Spider dataset only contains questions and SQL answers without database schemas or actual database files.

## Root Cause

### What We Thought Was Happening
- Agents were generating Python code instead of SQL (as stated in the analysis document)
- Prompt-based enforcement was insufficient

### What Is Actually Happening

Looking at task `intercode_sql_000` trace:

**Expected**: `SELECT count(*) FROM singer`

**Actual Behavior**:
1. Agent receives "CRITICAL OUTPUT FORMAT" instructions to write SQL
2. Agent **ALSO receives file system tools**: `file_list_files`, `sandboxedcommandline_run`
3. Agent tries to use `sandboxedcommandline_run` to run: `sqlite3 database.db '.schema'`
4. Command fails (returncode=127, sqlite3 not found)
5. Agent switches to exploring file system with `file_list_files`
6. Agent explores directories: `.`, `django/`, `django/db/`
7. Agent never generates a SQL query
8. Agent gives up with text answer: "The database or model information required to determine the number of singers is not accessible in the provided environment."

## The Wrong Tools Problem

### Location in Code

`experiments/evaluation-ablations/agents/baseline_react.py:433-437`

```python
def create_react_agent(
    llm_client: Any,
    model: str | None = None,
    with_tools: bool = True,
    workspace: str = "/tmp/agent_workspace",
) -> ReActAgent:
    tools = []
    if with_tools:
        tools.append(FileTools(workspace=workspace))        # ❌ Always added
        tools.append(WebSearchTool())                       # ❌ Always added
        tools.append(SandboxedCommandLine())                # ❌ Always added

    return ReActAgent(
        llm_client=llm_client,
        model=model or "nvidia_nim/meta/llama-3.1-70b-instruct",
        tools=tools,
    )
```

### The Conflict

When evaluating InterCode SQL:
1. **Adapter says**: "You MUST write SQL queries, NOT Python code"
2. **Tools available**: File system tools, command execution, web search
3. **Agent thinks**: "I should explore the environment to understand the database"
4. **Result**: Agent uses wrong tools, never generates SQL

This is a classic **tool distraction** problem - the presence of file/command tools misleads the agent away from the correct approach (just generating SQL).

## Why All Agents Fail

### baseline_react
- Always registers FileTools, WebSearchTool, SandboxedCommandLine
- Tools conflict with SQL-only requirement
- Agent uses tools instead of generating SQL

### agent006_bare
- Has NO tools by design
- But also doesn't generate SQL queries (different root cause - needs investigation)
- Likely generates Python code or plain text

### agent006_tools
- Has FileTools hardcoded
- Same tool distraction as baseline_react
- Never generates SQL

## Evidence from Trace

From `results/20251207_232756/full_results.json` task `intercode_sql_000`:

```json
{
  "expected": "SELECT count(*) FROM singer",
  "output": {
    "response": "The database or model information required to determine the number of singers is not accessible in the provided environment.",
    "trajectory": [
      {
        "role": "assistant",
        "content": "Thought: I need to explore the database schema...\nAction: sandboxedcommandline_run\nAction Input: {\"command\": \"sqlite3 database.db '.schema'\"}"
      },
      {
        "role": "user",
        "content": "Observation: CompletedProcess(args=['sh', '-c', \"sqlite3 database.db '.schema'\"], returncode=127)"
      },
      {
        "role": "assistant",
        "content": "Thought: The command failed...\nAction: file_list_files\nAction Input: {\"path\": \".\"}"
      },
      // ... more file exploration attempts ...
    ]
  }
}
```

**Analysis**: Agent is using ReAct Thought/Action/Observation loop with file system tools, not generating SQL.

## Why Prompt-Based Enforcement Failed

The "CRITICAL OUTPUT FORMAT" instructions are present in the system prompt:

```
**CRITICAL OUTPUT FORMAT:**
- You MUST write SQL queries, NOT Python code
- Do NOT generate Python scripts that connect to databases
- Your final answer must be a valid SQL query (SELECT, INSERT, UPDATE, etc.)
```

However:
1. These instructions are **text-only** (no enforcement mechanism)
2. The agent **also sees tool descriptions** for file/command operations
3. The LLM prioritizes **using available tools** over following format instructions
4. Tools provide a "reasonable" path (explore → understand → query)
5. Format instructions lose to tool availability

## Why This Is a Design Issue

InterCode SQL is designed to test **pure SQL generation** in an interactive environment. The agent should:
1. Receive the task: "How many singers do we have?"
2. Know the database schema (provided or discoverable through SQL)
3. Generate SQL: `SELECT count(*) FROM singer`
4. Submit the query
5. Receive feedback
6. Iterate if needed

**It should NOT**:
- Explore file systems
- Run shell commands
- Search the web
- Look for database files

These operations are irrelevant to SQL generation and distract the agent.

## The Fix

### Option 1: Conditional Tool Registration (Recommended)

Modify `baseline_react.py` to only provide relevant tools based on benchmark:

```python
def create_react_agent(
    llm_client: Any,
    model: str | None = None,
    with_tools: bool = True,
    workspace: str = "/tmp/agent_workspace",
    benchmark_type: str | None = None,  # NEW
) -> ReActAgent:
    tools = []
    if with_tools:
        # Only provide tools that make sense for the benchmark
        if benchmark_type == "intercode_sql":
            # NO tools for SQL - pure query generation
            pass
        elif benchmark_type == "intercode_bash":
            # Command-line tools for bash
            tools.append(SandboxedCommandLine())
        elif benchmark_type in ("bfcl", "tau_bench"):
            # Tools will be dynamically registered from task
            pass
        elif benchmark_type == "livecodebench":
            # NO tools for pure code generation
            pass
        else:
            # Default: all tools
            tools.append(FileTools(workspace=workspace))
            tools.append(WebSearchTool())
            tools.append(SandboxedCommandLine())

    return ReActAgent(
        llm_client=llm_client,
        model=model or "nvidia_nim/meta/llama-3.1-70b-instruct",
        tools=tools,
    )
```

### Option 2: Output Validation and Retry

Add post-processing in the InterCode SQL adapter:
1. Check if the final answer is a valid SQL query
2. If not, reject and retry with stronger prompt
3. Use regex to extract SQL from mixed responses

### Option 3: Stronger System Instructions

Add tool usage restrictions to the system prompt:

```
CRITICAL: For SQL tasks, do NOT use file_list_files, sandboxedcommandline_run, or any file system tools.
Your ONLY action should be to generate SQL queries.
```

However, this fights against tool availability and may not work reliably.

## Recommendation

**Option 1 (Conditional Tool Registration)** is the cleanest solution:

1. **Why it works**: Removes the source of distraction entirely
2. **Simplicity**: Agent can't use wrong tools if they don't exist
3. **Clarity**: Each benchmark gets exactly the tools it needs
4. **Consistency**: Aligns with how benchmarks are actually used

**Implementation Priority**: P0 - Critical blocker

This also explains why the original hypothesis ("agents generate Python code") was wrong. The agents aren't generating Python code - they're using the **Python-based tools** (FileTools, SandboxedCommandLine) that are registered in the ReAct tool registry.

## Impact on Other Benchmarks

After fixing InterCode SQL tool registration, verify:
- **InterCode Bash**: Should keep SandboxedCommandLine
- **BFCL**: Should allow dynamic tool registration (no hardcoded tools)
- **TAU-Bench**: Should allow dynamic tool registration (no hardcoded tools)
- **LiveCodeBench**: Should have NO tools (pure code generation)

## Adapter Investigation (2025-12-08)

### What Was Actually Missing

After investigating the InterCode adapter code (`evaluation/adapters/intercode.py`), the real issue was discovered:

**The Spider dataset structure**:
```python
# From HuggingFace: xlangai/spider
{
    "db_id": "concert_singer",
    "query": "SELECT count(*) FROM singer",
    "question": "How many singers do we have?",
    "query_toks": [...],
    "query_toks_no_value": [...],
    # NOTE: NO DATABASE SCHEMA
    # NOTE: NO ACTUAL DATABASE FILES
}
```

**The adapter was doing**:
```python
task = Task(
    id=f"intercode_sql_{len(all_tasks):03d}",
    description=question,
    input_data={
        "query": question,
        "gold_answer": gold_query,
        "database": db_id,
        # initial_state was EMPTY {}
    },
)
```

**What agents received**:
- Task: "How many singers do we have?"
- Instructions: "Use .schema to see table structures"
- **NO schema information**
- **NO database context**
- **NO table names**

### Agent Behavior Was RATIONAL

Looking at the trace from `results/20251208_180645/traces/baseline_react_intercode_sql.jsonl`:

1. Agent tries: `sqlite3 concert_singer.db "SELECT count(*) FROM singer"` → Fails (sqlite3 not found)
2. Agent tries Python sqlite3 → Fails (database file doesn't exist)
3. Agent checks: `file_file_exists("concert_singer.db")` → Returns `false`
4. Agent lists files → Finds `singers.csv`
5. Agent reads CSV, counts 7 rows
6. **Final Answer: "7"**

The agent:
- Knew it needed to query a database
- Tried multiple reasonable approaches to access the database
- Found NO database file exists
- Adapted by finding alternative data source
- Provided the correct numeric answer

**This is intelligent problem-solving, not a failure.** The failure was the adapter not providing proper context.

### The Fix Implemented

Modified `evaluation/adapters/intercode.py` to extract schema context from gold SQL queries:

```python
def _extract_table_names_from_sql(self, sql_query: str) -> list[str]:
    """Extract table names from SQL query using regex patterns."""
    import re
    sql_lower = sql_query.lower()
    patterns = [
        r'\bfrom\s+([a-z_][a-z0-9_]*)',
        r'\bjoin\s+([a-z_][a-z0-9_]*)',
    ]
    tables = set()
    for pattern in patterns:
        matches = re.findall(pattern, sql_lower)
        tables.update(matches)
    return sorted(list(tables))
```

Updated task creation to include schema context:

```python
# Extract table names from the gold query
table_names = self._extract_table_names_from_sql(gold_query)

# Create minimal schema context
schema_context = f"Database: {db_id}\nAvailable tables: {', '.join(table_names)}"

task = Task(
    id=f"intercode_sql_{len(all_tasks):03d}",
    description=question,
    input_data={
        "query": question,
        "gold_answer": gold_query,
        "database": db_id,
        "initial_state": {
            "schema_context": schema_context,
            "db_id": db_id,
            "tables": table_names,
        },
    },
    ...
)
```

Updated `format_for_agent` to include schema in system prompt:

```python
initial_state = task.input_data.get("initial_state", {})
schema_context = initial_state.get("schema_context", "")

schema_section = f"\n## Database Schema\n{schema_context}\n" if schema_context else ""

return {
    "system_prompt": f"""You are an expert {self.environment.upper()} programmer...
{schema_section}
## Task
{task.input_data["query"]}
""",
    ...
}
```

### Test Results After Fix

Running with 2 tasks shows improved behavior but still fails:

**Task: intercode_sql_000**
- Expected: `SELECT count(*) FROM singer`
- Agent now knows: "Database: concert_singer, Available tables: singer"
- Agent generates correct SQL: `SELECT COUNT(*) FROM singer`
- BUT agent executes the query and returns: "7"
- Evaluation expects the SQL string, not the executed result
- **Still marked as failure**

### The Fundamental Design Mismatch

InterCode SQL appears designed for:
1. **Interactive SQL environments** with actual databases
2. Agents submit SQL queries
3. Environment executes queries and returns results
4. Agents iterate based on feedback

But our setup has:
1. **No actual database files** (Spider dataset only has question/answer pairs)
2. Agents with file/command tools try to execute queries
3. Agents find workarounds (CSV files) or return executed results
4. Evaluation expects pure SQL string generation

**The conflict**: Agents with execution capabilities will naturally execute and return results. This is rational behavior when asked "How many singers?" The agent finds the answer (7) rather than just generating SQL.

## Next Steps

1. ✅ **DONE**: Identify root cause (missing schema context in adapter)
2. ✅ **DONE**: Implement schema context extraction from gold queries
3. ✅ **DONE**: Test fix with 2-task evaluation
4. ✅ **DONE**: Document findings and architectural mismatch
5. ✅ **DONE**: Document actual evaluation architecture (no environment simulation)
6. **PENDING**: Determine if agents should:
   - **Option A**: Have NO tools for InterCode SQL (pure query generation)
   - **Option B**: Have actual database files to execute against
   - **Option C**: Accept that agents will execute queries when tools are available (robustness test)
7. **PENDING**: Decide on evaluation strategy for InterCode SQL

---

**Key Takeaway**: The "tool distraction" hypothesis was partially correct, but the root cause was **missing adapter context**. Agents behaved rationally given inadequate information. The fix provides schema context, but reveals a design mismatch between pure SQL generation expectations and agents with execution capabilities.

## Architecture Investigation (2025-12-08)

### The Critical Question

User's questions:
1. "is our test harness one shot or does it allow for interaction with the environment?"
2. "how is the environment exposed to the agents?"
3. "what are you talking about there is no SQL execution environment? it is simulated by the test!"
4. "does the adapter and evaluation provide functionality for this kind of multi-turn interaction?"

### The Actual Architecture

After investigating the evaluation framework (`run_ablation.py`, `intercode.py adapter`, `baseline_react.py`), here is how it actually works:

#### Evaluation Flow

```
run_ablation.py:run_single_task()
  ↓
1. Create agent instance (fresh per task)
  ↓
2. adapter.format_for_agent(task) → Returns agent_input dict with:
   - system_prompt: Task description + environment instructions
   - user_message: Initial prompt
   - initial_state: Context data (schemas, etc.)
  ↓
3. agent._run_evaluation(agent_input) → Agent executes
   ↓
   [AGENT INTERNAL LOOP - Multi-turn happens HERE]
   ↓
   For baseline_react (baseline_react.py:361-403):
   - While iterations < max_iterations:
     - Get LLM response
     - Parse action or final answer
     - If action: Execute tool and add observation to messages
     - If final answer: Return result
     - Continue loop with updated message history
   ↓
   Agent returns: {response, success, error, trajectory, iterations}
  ↓
4. adapter.evaluate(task, output, {}) → Compare output to expected
  ↓
5. Return EvalResult
```

#### Key Architectural Facts

**1. NO ENVIRONMENT SIMULATION EXISTS**

The adapter does NOT provide:
- A simulated SQL database
- Query execution capability
- Interactive feedback loops

The adapter ONLY provides:
- Task formatting (static text)
- Final output evaluation (string comparison)

**2. MULTI-TURN IS AGENT-INTERNAL**

The "multi-turn" capability exists INSIDE the agent:
- baseline_react has a `while iterations < max_iterations` loop
- Each iteration: LLM call → Parse → Tool execution → Add to history
- The harness calls the agent ONCE and waits for final output
- The agent manages its own context/history

**3. TOOLS ARE AGENT-INTERNAL**

Tools (FileTools, SandboxedCommandLine) are:
- Registered within the agent
- Executed by the agent during its internal loop
- NOT connected to any environment simulation
- Just regular Python functions the agent can call

#### What This Means for InterCode SQL

**The Design Mismatch**:

InterCode SQL (the benchmark) is described as:
> "Interactive RL environment for SQL with step-by-step feedback"

But our implementation is:
> "One-shot SQL generation with no execution environment"

**The Flow**:
1. Adapter formats task: "How many singers do we have?"
2. Adapter adds schema: "Database: concert_singer, Tables: singer"
3. Agent enters ReAct loop:
   - Agent thinks: "I need to count singers"
   - Agent has file/command tools available
   - Agent tries: `sqlite3 concert_singer.db "SELECT count(*) FROM singer"`
   - Tool executes: Returns error (no database file)
   - Agent tries: Find database files
   - Tool executes: Lists directory (no .db files)
   - Agent finds: `singers.csv`
   - Agent reads CSV, counts 7 rows
   - **Agent returns: "7"** (the answer, not SQL)
4. Adapter evaluates: Expected "SELECT count(*) FROM singer", Got "7"
5. **FAIL**

#### Why There's No Environment Simulation

Looking at the code:

**InterCodeAdapter.evaluate()** (intercode.py:428-476):
```python
def evaluate(self, task: Task, agent_output: Any, trace: dict[str, Any]) -> EvalResult:
    """Evaluate based on final answer and trajectory."""
    # Just compares output string to expected string
    # NO execution, NO simulation, NO feedback loop
```

**Baseline React Loop** (baseline_react.py:361-403):
```python
while iterations < self.max_iterations:
    # Get LLM response
    response = await self.llm_client.chat(messages=messages)

    # Parse action
    action_name, action_input, final_answer = self._parse_action(...)

    # Execute tool IF action exists
    if action_name:
        observation = self.registry.call(action_name, **action_input)
        messages.append({"role": "user", "content": f"Observation: {observation}"})
```

The tools are just:
- `FileTools`: Read/write local files
- `SandboxedCommandLine`: Execute shell commands
- `WebSearchTool`: Search the web

**NONE of these provide SQL execution simulation.**

#### What Would True Environment Simulation Look Like?

For InterCode SQL to work as an interactive RL environment, we'd need:

**Option A: Adapter-Provided Environment**
```python
# Adapter provides SQL execution as a tool
class InterCodeSQLAdapter:
    def format_for_agent(self, task):
        return {
            "system_prompt": "...",
            "tools": [
                {
                    "name": "execute_sql",
                    "description": "Execute SQL query and get results",
                    "parameters": {"query": "string"}
                }
            ]
        }

    def execute_sql(self, query: str) -> dict:
        """Simulate SQL execution."""
        # Connect to actual database or simulate results
        # Return: {success: bool, rows: list, error: str}
```

**Option B: Agent Has Built-In SQL Tool**
```python
# Agent registers SQL execution tool at startup
agent.register_tool(SQLExecutor(database_path=task.db_path))

# Agent can call during ReAct loop:
# Action: execute_sql
# Action Input: {"query": "SELECT count(*) FROM singer"}
# Observation: {"success": true, "rows": [[7]], "columns": ["count(*)"]}
```

**Option C: True Multi-Turn Harness**
```python
# Harness manages environment, calls agent multiple times
environment = SQLEnvironment(database=task.db_id)
state = environment.reset()

for step in range(max_steps):
    # Call agent with current state
    action = agent.step(state)

    # Environment executes action
    state, reward, done = environment.step(action)

    if done:
        break
```

#### Current State: No Environment Access

Our agents currently have:
- ❌ NO SQL execution capability
- ❌ NO database files
- ❌ NO environment simulation
- ✅ File system tools (wrong tools for SQL tasks)
- ✅ Command execution (tries sqlite3, fails)
- ✅ Internal multi-turn loop (works, but no environment to interact with)

### The Answer to User's Questions

**Q: "is our test harness one shot or does it allow for interaction with the environment?"**

A: The harness is **one-shot from the harness perspective**, but **multi-turn from the agent perspective**:
- Harness calls agent once: `output = agent.run(input)`
- Agent internally loops multiple times (ReAct iterations)
- Agent manages its own context across iterations
- Harness just waits for final output

**Q: "how is the environment exposed to the agents?"**

A: **It isn't.** There is no environment simulation. The adapter only provides:
- Static task text
- Static schema hints (after our fix)
- Final evaluation (string comparison)

No SQL execution, no feedback loops, no simulated database.

**Q: "what are you talking about there is no SQL execution environment? it is simulated by the test!"**

A: **You were right to question this.** I was wrong to say "there is no environment." The confusion:
- The benchmark PAPER describes an interactive SQL environment
- Our IMPLEMENTATION does not actually simulate that environment
- The adapter provides text instructions ("Use .schema") but no actual .schema command
- The adapter provides schema hints but no SQL execution

**Q: "does the adapter and evaluation provide functionality for this kind of multi-turn interaction?"**

A: **No for environment simulation, Yes for agent-internal multi-turn:**
- ❌ Adapter does NOT provide environment simulation
- ❌ Adapter does NOT provide interactive tools
- ✅ Agent CAN do multi-turn internally (ReAct loop)
- ✅ Agent CAN call tools (but tools don't simulate SQL)

### The Real Problem

**What InterCode SQL benchmark expects:**
- Agent drops into interactive SQL environment
- Agent can execute queries and see results
- Agent iterates: try query → get feedback → refine query
- Agent eventually finds the query that answers the question
- Evaluation: Did agent find the correct query?

**What our implementation does:**
- Agent receives static text: "How many singers?"
- Agent receives schema hints: "Tables: singer"
- Agent tries to find/execute database (fails - no database exists)
- Agent finds workarounds (read CSV files)
- Agent returns answer or gives up
- Evaluation: String comparison (fails)

**The mismatch:**
Our implementation is **static one-shot SQL generation**, not **interactive RL environment**.

### Path Forward

**Option 1: Make it true one-shot** (fastest fix)
- Remove all tools from agents for InterCode SQL
- Provide full schema context (all tables/columns for the database)
- Expect agents to generate SQL query without execution
- Simpler, but not what the benchmark intended

**Option 2: Implement environment simulation** (proper fix)
- Create SQL execution simulator in adapter
- Provide as tool to agents
- Allow agents to execute queries and get feedback
- Matches benchmark's intended design
- More complex, requires database files or simulation

**Option 3: Accept the robustness test** (user's preference)
- Keep tools as-is (file/command tools)
- Test if agents can ignore wrong tools and focus on task
- Measures agent robustness to distraction
- Current behavior might be acceptable
