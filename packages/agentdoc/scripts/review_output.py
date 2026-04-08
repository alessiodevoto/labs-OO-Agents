#!/usr/bin/env python
"""Streamlined review of pprint() and doc() output for nemo_oo_agents types.

This script generates markdown output showing how agentdoc handles the most
important types and instances in nemo_oo_agents. Systematically shows doc(),
doc(concise=True), and pformat() for consistent review.

Output Format:
    For each test, the markdown includes:
    - 📄 Source Code: Actual Python class/method definition (via inspect.getsource)
    - doc(Type): Full agentdoc rendering
    - doc(Type, concise=True): Abbreviated rendering
    - pformat(instance): Instance state rendering

    This side-by-side comparison makes it easy to verify that agentdoc
    correctly extracts type hints, docstrings, defaults, and constraints.

Usage:
    python packages/agentdoc/scripts/review_output.py > docs/scratch/agentdoc-output-review.md

Review Workflow:
    1. All tests start with is_approved=False (default) showing ❌ **Needs Work**
    2. Review each output in the generated markdown file
    3. Compare source code with rendered output to verify correctness
    4. When a test's output is validated, add is_approved=True to that test:
       Example: show_type("QueryRequest", QueryRequest, is_approved=True)
    5. Regenerate the markdown to see ✅ **Approved** for validated tests
    6. Continue until all tests show ✅ **Approved**

Track Progress:
    grep -c "❌" docs/scratch/agentdoc-output-review-v2.md   # Count remaining
    grep -c "✅" docs/scratch/agentdoc-output-review-v2.md   # Count approved
"""

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated

from nemo_oo_agents import Agent
from nemo_oo_agents.events import ExecutionResult, Task
from nemo_oo_agents.runtime import TruncationConfig
from context_blocks import DynamicContext
from pydantic import BaseModel, Field
from unifiedllm import FakeLLMClient

from agentdoc import doc, pformat

# =============================================================================
# TEST FIXTURES
# =============================================================================


# --- Pydantic Models (Annotated syntax - Golden Path) ---
class QueryRequest(BaseModel):
    """A database query request.

    Encapsulates query parameters with validation and defaults.
    Used as input to DatabaseTool.query() method.
    """

    sql: Annotated[str, Field(description="SQL query to execute")]
    params: Annotated[dict[str, str], Field(default_factory=dict, description="Query parameters")]
    limit: Annotated[int, Field(default=100, ge=1, le=1000, description="Maximum rows to return")]
    timeout: Annotated[float, Field(default=30.0, gt=0, description="Query timeout in seconds")]


class QueryResult(BaseModel):
    """Result of a database query.

    Contains the query results along with metadata about execution.
    """

    rows: Annotated[list[dict], Field(description="Query result rows")]
    row_count: Annotated[int, Field(description="Number of rows returned")]
    execution_time: Annotated[float, Field(description="Query execution time in seconds")]
    truncated: Annotated[bool, Field(default=False, description="True if results were truncated")]


class QueryError(BaseModel):
    """Error information from a failed database query.

    Contains details about why a query failed, including
    error codes and diagnostic information.
    """

    error_code: Annotated[str, Field(description="Database error code")]
    message: Annotated[str, Field(description="Human-readable error message")]
    query: Annotated[str, Field(description="The query that failed")]
    recoverable: Annotated[bool, Field(default=False, description="Whether the error is recoverable")]


class Manufacturer(BaseModel):
    """A product manufacturer.

    Represents a company that produces products, including
    their certification status and country of origin.
    Used for supply chain tracking and compliance verification.
    """

    name: Annotated[str, Field(description="Manufacturer name")]
    country: Annotated[str, Field(description="Country of origin")]
    certified: Annotated[bool, Field(default=False, description="ISO certified")]


class NestedProduct(BaseModel):
    """A product with nested category structure.

    Represents a product in the catalog with pricing, categorization,
    and manufacturer information. Products can belong to multiple
    categories and optionally have an associated manufacturer.
    """

    sku: Annotated[str, Field(description="Stock keeping unit")]
    name: Annotated[str, Field(description="Product name")]
    price: Annotated[float, Field(default=0.0, description="Price in USD")]
    categories: Annotated[list[str], Field(default_factory=list, description="Categories")]
    manufacturer: Annotated[Manufacturer | None, Field(default=None, description="Product manufacturer")]


class NestedOrder(BaseModel):
    """An order with nested products.

    Represents a customer order containing one or more products.
    Tracks the order total and maintains the list of products
    with their individual details.
    """

    order_id: Annotated[str, Field(description="Order identifier")]
    products: Annotated[list[NestedProduct], Field(description="Products in order")]
    total: Annotated[float, Field(default=0.0, description="Order total")]


# --- Namespaced Tools ---
class DatabaseTool:
    """Database operations namespace.

    Provides query, insert, and transaction operations.
    Maintains connection pool and query statistics.

    Example:
        result = self.db.query(QueryRequest(sql="SELECT * FROM users"))
        print(f"Found {result.row_count} users")
    """

    def __init__(self, connection_string: str = "sqlite:///:memory:"):
        self.connection_string: Annotated[str, "Database connection string"] = connection_string
        self.query_count: Annotated[int, "Total queries executed"] = 0
        self.last_query: Annotated[str | None, "Most recent query"] = None

    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """
        self.query_count += 1
        self.last_query = request.sql
        return QueryResult(rows=[], row_count=0, execution_time=0.01, truncated=False)

    def insert(
        self,
        table: Annotated[str, "Target table name"],
        data: Annotated[dict, "Row data to insert"],
    ) -> Annotated[int, "ID of inserted row"]:
        """Insert a row into a table.

        Automatically escapes values and handles type conversion.
        """
        self.query_count += 1
        return 1

    def execute_safe(
        self,
        request: QueryRequest,
    ) -> QueryResult | QueryError:
        """Execute a query with error handling.

        Returns either successful results or detailed error information.
        Unlike query(), this method catches errors and returns them
        as structured QueryError objects instead of raising exceptions.
        """
        self.query_count += 1
        return QueryResult(rows=[], row_count=0, execution_time=0.01, truncated=False)


# --- Golden Path Agent ---
class DataAgent(Agent, llm=FakeLLMClient()):
    """Data processing agent with database capabilities.

    This agent demonstrates the recommended documentation patterns:
    - Namespaced tools (self.db) with their own state
    - Typed state with Annotated descriptions
    - Methods with Pydantic input/output types

    Example:
        agent = DataAgent()
        result = await agent.fetch_user("user:123")
    """

    db: DatabaseTool = DatabaseTool()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.processed_requests: Annotated[int, "Total requests processed"] = 0
        self.last_error: Annotated[str | None, "Most recent error message"] = None

    async def fetch_user(
        self,
        user_id: Annotated[str, "User ID to fetch"],
    ) -> Annotated[dict | None, "User data or None if not found"]:
        """Fetch a user by ID, using cache when available.

        Checks cache first, falls back to database query.
        """
        ...

    async def process_batch(
        self,
        requests: Annotated[list[QueryRequest], "Batch of queries to execute"],
    ) -> Annotated[list[QueryResult], "Results for each query"]:
        """Process a batch of database queries.

        Executes queries in order, collecting results.
        """
        ...


# --- Child Agent ---
class WorkerAgent(Agent, llm=FakeLLMClient()):
    """A worker agent that performs tasks."""

    def __init__(self, worker_id: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.worker_id = worker_id
        self.tasks_completed = 0

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
        ...


class CoordinatorAgent(Agent, llm=FakeLLMClient()):
    """Coordinates work across multiple workers."""

    WorkerAgent = WorkerAgent  # Child agent class
    db = DatabaseTool()  # Tool instance

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_workers: list[WorkerAgent] = []
        self.task_queue: list[str] = []

    async def distribute_tasks(
        self, tasks: Annotated[list[str], "Tasks to distribute"]
    ) -> Annotated[list[str], "Task results from workers"]:
        """Distribute tasks to workers and collect results."""
        ...


# --- Dataclasses ---
@dataclass
class Point:
    """A 2D point with coordinates."""

    x: float
    y: float


@dataclass
class ComplexSession:
    """A complex dataclass with multiple field types."""

    session_id: str
    agent_name: str
    depth: int = 0
    turns: list[dict] = field(default_factory=list)
    children: list["ComplexSession"] = field(default_factory=list)
    status: str = "OK"


# --- Plain Python Class ---
class Calculator:
    """A simple calculator tool."""

    def __init__(self):
        self.history: list[str] = []
        self.last_result: float = 0

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        self.last_result = result
        return result

    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
        result = a * b
        self.history.append(f"{a} * {b} = {result}")
        self.last_result = result
        return result


# --- Enum ---
class TaskStatus(Enum):
    """Status of a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# --- NamedTuple ---
from typing import NamedTuple  # noqa: E402


class Coordinate(NamedTuple):
    """A geographic coordinate."""

    latitude: float
    longitude: float
    altitude: float = 0.0


# --- TypedDict ---
from typing import TypedDict  # noqa: E402


class ConfigDict(TypedDict, total=False):
    """Configuration with optional fields."""

    debug: bool
    log_level: str
    max_retries: int


# --- String Storage Classes ---
class DocumentationCache:
    """Stores cached documentation strings.

    Tests how agentdoc handles complex multi-line string values.
    """

    def __init__(self):
        self.user_doc: str = ""
        self.calculator_doc: str = ""
        self.simple_message: str = "Hello, world!"


# --- Large Nested Data ---
class JsonHolder:
    """A class with a large nested dict."""

    def __init__(self):
        self.data: dict = {}
        self.metadata: dict = {"created": "2026-01-28", "version": "1.0"}

    def populate(self, depth: int = 4, breadth: int = 10) -> None:
        """Populate with nested data."""

        def build_level(current_depth: int) -> dict:
            if current_depth <= 0:
                return {"value": f"leaf_{current_depth}"}
            return {f"key_{i}": build_level(current_depth - 1) if i < 2 else f"value_{i}" for i in range(breadth)}

        self.data = build_level(depth)


# =============================================================================
# OUTPUT HELPERS
# =============================================================================


def section(title: str):
    """Print a markdown section header."""
    print(f"\n## {title}\n")


def subsection(title: str):
    """Print a markdown subsection header."""
    print(f"\n### {title}\n")


def approved(is_approved: bool):
    """Print approval status with emoji."""
    if is_approved:
        print("✅ **Approved**\n")
    else:
        print("❌ **Needs Work**\n")


def show_source(obj):
    """Show source code for a type or function."""
    try:
        source = inspect.getsource(obj)
        print("<details>")
        print("<summary>📄 Source Code</summary>\n")
        print("```python")
        print(source)
        print("```")
        print("</details>\n")
    except (TypeError, OSError):
        # Built-in types or types without source
        print("<details>")
        print("<summary>📄 Source Code</summary>\n")
        print("```python")
        print(f"# {obj} is a built-in type or has no accessible source")
        print("```")
        print("</details>\n")


def show_type(name: str, obj: type, is_approved: bool = False):
    """Standardized output for TYPE: doc() and doc(concise=True)."""
    print(f"**{name}**\n")

    # Show source code first
    show_source(obj)

    print("<details>")
    print("<summary>doc(Type) - Full</summary>\n")
    print("```python")
    print(doc(obj))
    print("```")
    print("</details>\n")
    print("<details>")
    print("<summary>doc(Type, concise=True)</summary>\n")
    print("```python")
    print(doc(obj, concise=True))
    print("```")
    print("</details>\n")
    approved(is_approved)


def show_instance(name: str, obj, show_doc: bool = True, is_approved: bool = False, creation_code: str | None = None):
    """Standardized output for INSTANCE: doc(), pformat(), and optionally doc(concise=True)."""
    print(f"**{name}**\n")

    # Optionally show how the instance was created
    if creation_code:
        print("<details>")
        print("<summary>📄 Instance Creation</summary>\n")
        print("```python")
        print(creation_code)
        print("```")
        print("</details>\n")

    if show_doc:
        print("<details>")
        print("<summary>doc(instance)</summary>\n")
        print("```python")
        print(doc(obj))
        print("```")
        print("</details>\n")

    print("<details>")
    print("<summary>pformat(instance)</summary>\n")
    print("```python")
    print(pformat(obj))
    print("```")
    print("</details>\n")

    approved(is_approved)


def show_method(name: str, method, is_approved: bool = False):
    """Show method documentation."""
    print(f"**{name}**\n")

    # Show source code first
    show_source(method)

    print("<details>")
    print("<summary>doc(method)</summary>\n")
    print("```python")
    print(doc(method))
    print("```")
    print("</details>\n")
    approved(is_approved)


def show_truncation(name: str, obj, is_approved: bool = False, **kwargs):
    """Show truncation with parameters."""
    print(f"**{name}**\n")
    params_str = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print("<details>")
    print(f"<summary>pformat(obj, {params_str})</summary>\n")
    print("```python")
    print(pformat(obj, **kwargs))
    print("```")
    print("</details>\n")
    approved(is_approved)


# =============================================================================
# MAIN REVIEW OUTPUT
# =============================================================================


def main():
    print("# agentdoc Output Review (Streamlined)")
    print()
    print("**Purpose:** Systematic review of `doc()` and `pformat()` output quality.")
    print()
    print("**Output Patterns:**")
    print("- **Types:** `doc(Type)` and `doc(Type, concise=True)`")
    print("- **Instances:** `doc(instance)` and `pformat(instance)`")
    print("- **Methods:** `doc(method)`")
    print()
    print("**Review Criteria:**")
    print("- Is the output readable for LLMs?")
    print("- Are type hints and descriptions clear?")
    print("- Is truncation behavior sensible?")
    print("- Are multi-line strings handled well?")
    print()
    print("---")

    # =========================================================================
    section("1. Golden Path - Recommended Patterns")
    # =========================================================================

    print("""
**Purpose:** Demonstrate the recommended way to document agents, tools, and models.

**What to review:**
- Pydantic models use `Annotated[type, Field(description="...")]`
- Tools are namespaced classes with typed state
- Agents compose tools and expose methods with typed I/O
""")

    subsection("1.1 Pydantic Input/Output Models")
    show_type("QueryRequest (simple model)", QueryRequest)
    show_type("QueryError (error response model)", QueryError)
    show_type("NestedOrder (nested model with transitive types)", NestedOrder)
    show_type("NestedProduct (shows Manufacturer in Referenced Types)", NestedProduct)

    # Create instance
    request = QueryRequest(sql="SELECT * FROM users", params={}, limit=50, timeout=30.0)
    show_instance(
        "QueryRequest instance",
        request,
        show_doc=False,
        creation_code='request = QueryRequest(sql="SELECT * FROM users", limit=50)',
    )

    order = NestedOrder(
        order_id="ORD-123",
        products=[
            NestedProduct(
                sku="SKU-001",
                name="Widget",
                price=29.99,
                categories=["tools"],
                manufacturer=Manufacturer(name="WidgetCorp", country="USA", certified=True),
            ),
            NestedProduct(sku="SKU-002", name="Gadget", price=49.99, categories=[], manufacturer=None),
        ],
        total=79.98,
    )
    show_instance(
        "NestedOrder instance (nested)",
        order,
        show_doc=False,
        creation_code="""order = NestedOrder(
    order_id="ORD-123",
    products=[
        NestedProduct(
            sku="SKU-001",
            name="Widget",
            price=29.99,
            categories=["tools"],
            manufacturer=Manufacturer(name="WidgetCorp", country="USA", certified=True),
        ),
        NestedProduct(sku="SKU-002", name="Gadget", price=49.99),
    ],
    total=79.98,
)""",
    )

    subsection("1.2 Namespaced Tool with State")
    show_type("DatabaseTool (type)", DatabaseTool)

    db_tool = DatabaseTool(connection_string="postgres://localhost/mydb")
    db_tool.query_count = 42
    db_tool.last_query = "SELECT * FROM users WHERE active = true"
    show_instance(
        "DatabaseTool (instance with state)",
        db_tool,
        creation_code="""db_tool = DatabaseTool(connection_string="postgres://localhost/mydb")
db_tool.query_count = 42
db_tool.last_query = "SELECT * FROM users WHERE active = true"
""",
    )

    show_method("DatabaseTool.query (method)", db_tool.query)
    show_method("DatabaseTool.insert (method with Annotated params)", db_tool.insert)
    show_method(
        "DatabaseTool.execute_safe (method returning QueryResult | QueryError)",
        db_tool.execute_safe,
    )

    subsection("1.3 Agent with Namespaced Tools")
    show_type("DataAgent (agent type)", DataAgent)

    agent = DataAgent()
    agent.processed_requests = 100
    agent.db.query_count = 50
    show_instance(
        "DataAgent (instance with tool state)",
        agent,
        creation_code="""agent = DataAgent()
agent.processed_requests = 100
agent.db.query_count = 50
""",
    )

    show_method("DataAgent.fetch_user (method)", agent.fetch_user)
    show_method("DataAgent.process_batch (method with Pydantic I/O)", agent.process_batch)

    subsection("1.4 Progressive Disclosure - Drilling Down")
    print("**Demonstrates:** Top-level agent → namespace → method navigation\n")
    show_instance("agent (top level)", agent, show_doc=True)
    show_instance("agent.db (namespace)", agent.db, show_doc=True)
    show_method("agent.db.query (method)", agent.db.query)

    # =========================================================================
    section("2. Agent Classes")
    # =========================================================================

    print("""
**Purpose:** Test Agent-specific rendering (child agents, tools, state).
""")

    subsection("2.1 Agent with Child Agent")
    show_type("CoordinatorAgent (type)", CoordinatorAgent)

    # Additional example: inline_depth=100 to show all transitive referenced types
    print("**CoordinatorAgent with inline_depth=100**\n")
    print("<details>")
    print("<summary>doc(Type, inline_depth=100) - Full transitive references</summary>\n")
    print("```python")
    print(doc(CoordinatorAgent, inline_depth=100))
    print("```")
    print("</details>\n")

    coordinator = CoordinatorAgent()
    coordinator.task_queue = ["task1", "task2", "task3"]
    show_instance("CoordinatorAgent (instance)", coordinator)

    subsection("2.2 Child Agent as Attribute")
    show_type("WorkerAgent (child agent class)", coordinator.WorkerAgent)

    subsection("2.3 Tool Instance as Attribute")
    show_instance("coordinator.db (tool)", coordinator.db)

    show_method("coordinator.distribute_tasks (method)", coordinator.distribute_tasks)

    # =========================================================================
    section("3. Runtime Objects")
    # =========================================================================

    print("""
**Purpose:** Test nemo_oo_agents-specific runtime components.
""")

    subsection("3.1 Events")
    show_instance("agent.events (Events)", agent.events)

    subsection("3.2 ContextManager")
    show_instance("agent.context_manager (ContextManager)", agent.context_manager)

    # =========================================================================
    section("4. Event Types")
    # =========================================================================

    print("""
**Purpose:** Test event class and instance rendering.
""")

    subsection("4.1 Event Class")
    show_type("Task (type)", Task)

    subsection("4.2 Event Instances")
    task_event = Task(prompt="Process the data")
    show_instance("Task instance", task_event, show_doc=False)

    exec_result = ExecutionResult(stdout="Hello World\\n", stderr="", error=None)
    show_instance("ExecutionResult instance", exec_result, show_doc=False)

    # =========================================================================
    section("5. Dataclasses")
    # =========================================================================

    print("""
**Purpose:** Test standard library dataclass rendering.
""")

    subsection("5.1 Simple Dataclass")
    show_type("Point (simple)", Point)

    point = Point(x=10.5, y=20.3)
    show_instance("Point instance", point, show_doc=False)

    subsection("5.2 Complex Dataclass")
    show_type("ComplexSession (with lists and recursion)", ComplexSession)

    session = ComplexSession(
        session_id="abc123",
        agent_name="TestAgent",
        depth=0,
        turns=[{"type": "llm", "content": "thinking..."}, {"type": "exec", "output": "42"}],
    )
    show_instance(
        "ComplexSession instance",
        session,
        show_doc=False,
        creation_code="""session = ComplexSession(
    session_id="abc123",
    agent_name="TestAgent",
    depth=0,
    turns=[{"type": "llm", "content": "thinking..."}, {"type": "exec", "output": "42"}],
)""",
    )

    subsection("5.3 List of Dataclasses")
    sessions = [
        ComplexSession(session_id="s1", agent_name="Agent1", depth=0),
        ComplexSession(session_id="s2", agent_name="Agent2", depth=1),
        ComplexSession(session_id="s3", agent_name="Agent3", depth=2),
    ]
    show_instance("list[ComplexSession]", sessions, show_doc=False)
    show_truncation("list[ComplexSession] (truncated)", sessions, max_length=2)

    # =========================================================================
    section("6. Plain Python Classes")
    # =========================================================================

    print("""
**Purpose:** Test non-decorated Python classes.
""")

    subsection("6.1 Plain Class")
    show_type("Calculator (type)", Calculator)

    calc = Calculator()
    calc.add(10, 20)
    calc.multiply(5, 6)
    show_instance(
        "Calculator instance",
        calc,
        creation_code="""calc = Calculator()
calc.add(10, 20)
calc.multiply(5, 6)
""",
    )

    # =========================================================================
    section("7. Other Common Types")
    # =========================================================================

    print("""
**Purpose:** Test TypedDict, NamedTuple, Enum rendering.
""")

    subsection("7.1 TypedDict")
    show_type("ConfigDict (TypedDict)", ConfigDict)

    config: ConfigDict = {"debug": True, "log_level": "INFO", "max_retries": 3}
    show_instance("ConfigDict instance", config, show_doc=False)

    subsection("7.2 NamedTuple")
    show_type("Coordinate (NamedTuple)", Coordinate)

    coord = Coordinate(latitude=40.7128, longitude=-74.0060, altitude=10.0)
    show_instance("Coordinate instance", coord, show_doc=False)

    subsection("7.3 Enum")
    show_type("TaskStatus (Enum)", TaskStatus)
    show_instance("TaskStatus.RUNNING (member)", TaskStatus.RUNNING, show_doc=False)

    # =========================================================================
    section("8. Configuration Types")
    # =========================================================================

    print("""
**Purpose:** Test nemo_oo_agents configuration objects.
""")

    subsection("8.1 TruncationConfig")
    show_type("TruncationConfig (type)", TruncationConfig)

    truncation_config = TruncationConfig(max_stdout_chars=10000, max_stderr_chars=5000)
    show_instance("TruncationConfig instance", truncation_config, show_doc=False)

    subsection("8.2 DynamicContext (context block marker)")
    show_type("DynamicContext (type)", DynamicContext)

    dynamic = DynamicContext("self.get_status()")
    show_instance("DynamicContext instance", dynamic, show_doc=False)

    # =========================================================================
    section("9. Functions")
    # =========================================================================

    print("""
**Purpose:** Test function signature rendering.
""")

    subsection("9.1 Sync Function")

    def example_function(data: list[dict], limit: int = 10) -> list[dict]:
        """Process data with an optional limit.

        Args:
            data: Input data to process
            limit: Maximum items to return

        Returns:
            Processed and limited data
        """
        return data[:limit]

    show_method("example_function (sync)", example_function)

    subsection("9.2 Async Function")

    async def async_fetch(url: str, timeout: float = 30.0) -> dict:
        """Fetch data from a URL asynchronously.

        Args:
            url: The URL to fetch
            timeout: Request timeout in seconds

        Returns:
            Response data as dictionary
        """
        return {}

    show_method("async_fetch (async)", async_fetch)

    # =========================================================================
    section("10. Complex String Fields")
    # =========================================================================

    print("""
**Purpose:** Test multi-line string rendering (CRITICAL for doc() output storage).

**What to review:**
- Multi-line strings use triple quotes
- Special characters handled correctly
- Truncation behavior is sensible
""")

    subsection("10.1 Class with String Fields")
    show_type("DocumentationCache (type)", DocumentationCache)

    subsection("10.2 Instance with Cached Documentation")
    doc_cache = DocumentationCache()
    doc_cache.user_doc = doc(QueryRequest)
    doc_cache.calculator_doc = doc(Calculator)
    show_instance("DocumentationCache (default truncation)", doc_cache, show_doc=False)
    show_truncation("DocumentationCache (full content)", doc_cache, max_string=500)

    subsection("10.3 Doc Output as String")
    doc_string = doc(QueryRequest)
    print(f"**Full doc string:** {len(doc_string)} characters\n")
    show_instance("doc(QueryRequest) as string (default)", doc_string, show_doc=False)
    show_truncation("doc(QueryRequest) as string (truncated)", doc_string, max_string=100)

    # =========================================================================
    section("11. Truncation Behavior")
    # =========================================================================

    print("""
**Purpose:** Test truncation parameters (CRITICAL for token management).

**What to review:**
- max_string: String length limit
- max_length: Collection length limit
- max_depth: Nesting depth limit
- Ellipsis placement is clear
""")

    subsection("11.1 Long Strings")
    long_string = "This is a very long string that should be truncated. " * 50
    show_instance("long_string (default)", long_string, show_doc=False)
    show_truncation("long_string (max_string=50)", long_string, max_string=50)

    subsection("11.2 Large Lists")
    large_list = list(range(100))
    show_instance("range(100) (default)", large_list, show_doc=False)
    show_truncation("range(100) (max_length=5)", large_list, max_length=5)

    subsection("11.3 Large Dicts")
    large_dict = {f"key_{i}": f"value_{i}" for i in range(50)}
    show_truncation("large_dict (max_length=5)", large_dict, max_length=5)

    subsection("11.4 Deeply Nested Structures")
    deep = {"level1": {"level2": {"level3": {"level4": {"level5": "deep value"}}}}}
    show_instance("nested (default)", deep, show_doc=False)
    show_truncation("nested (max_depth=2)", deep, max_depth=2)
    show_truncation("nested (max_depth=3)", deep, max_depth=3)

    subsection("11.5 Combined Truncation")
    complex_data = {
        "users": [QueryRequest(sql=f"SELECT {i}", params={}, limit=100, timeout=30.0) for i in range(20)],
        "config": {"nested": {"deep": {"value": "x" * 1000}}},
        "items": list(range(1000)),
    }
    show_truncation(
        "complex_data (multiple params)",
        complex_data,
        max_length=3,
        max_string=50,
        max_depth=2,
    )

    subsection("11.6 Instance with Large Nested Dict")
    json_holder = JsonHolder()
    json_holder.populate(depth=4, breadth=10)
    show_instance(
        "JsonHolder (default)",
        json_holder,
        show_doc=False,
        creation_code="""json_holder = JsonHolder()
json_holder.populate(depth=4, breadth=10)
""",
    )
    show_truncation("JsonHolder (max_depth=2)", json_holder, max_depth=2)
    show_truncation("JsonHolder (max_depth=3, max_length=3)", json_holder, max_depth=3, max_length=3)

    # =========================================================================
    section("12. Edge Cases")
    # =========================================================================

    print("""
**Purpose:** Test robustness with unusual inputs.
""")

    subsection("12.1 None and Empty Collections")
    show_instance("None", None, show_doc=False)
    show_instance("Empty list", [], show_doc=False)
    show_instance("Empty dict", {}, show_doc=False)
    show_instance("Empty string", "", show_doc=False)

    subsection("12.2 Lambda")
    fn = lambda x: x * 2  # noqa: E731
    show_method("lambda function", fn)

    subsection("12.3 Built-in Types")
    show_type("str (built-in)", str)
    show_type("list (built-in)", list)

    subsection("12.4 Modules")
    import json

    show_instance("json module", json, show_doc=True)

    import agentdoc

    show_instance("agentdoc module", agentdoc, show_doc=True)

    print("\n---\n")
    print("*End of agentdoc output review*")


if __name__ == "__main__":
    main()
