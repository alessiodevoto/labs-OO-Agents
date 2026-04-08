# agentdoc Output Review (Streamlined)

**Purpose:** Systematic review of `doc()` and `pformat()` output quality.

**Output Patterns:**
- **Types:** `doc(Type)` and `doc(Type, concise=True)`
- **Instances:** `doc(instance)` and `pformat(instance)`
- **Methods:** `doc(method)`

**Review Criteria:**
- Is the output readable for LLMs?
- Are type hints and descriptions clear?
- Is truncation behavior sensible?
- Are multi-line strings handled well?

---

## 1. Golden Path - Recommended Patterns


**Purpose:** Demonstrate the recommended way to document agents, tools, and models.

**What to review:**
- Pydantic models use `Annotated[type, Field(description="...")]`
- Tools are namespaced classes with typed state
- Agents compose tools and expose methods with typed I/O


### 1.1 Pydantic Input/Output Models

**QueryRequest (simple model)**

<details>
<summary>📄 Source Code</summary>

```python
class QueryRequest(BaseModel):
    """A database query request.

    Encapsulates query parameters with validation and defaults.
    Used as input to DatabaseTool.query() method.
    """

    sql: Annotated[str, Field(description="SQL query to execute")]
    params: Annotated[dict[str, str], Field(default_factory=dict, description="Query parameters")]
    limit: Annotated[int, Field(default=100, ge=1, le=1000, description="Maximum rows to return")]
    timeout: Annotated[float, Field(default=30.0, gt=0, description="Query timeout in seconds")]

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class QueryRequest(BaseModel):
    """
    A database query request.

    Encapsulates query parameters with validation and defaults.
    Used as input to DatabaseTool.query() method.
    """

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
```
</details>

❌ **Needs Work**

**QueryError (error response model)**

<details>
<summary>📄 Source Code</summary>

```python
class QueryError(BaseModel):
    """Error information from a failed database query.

    Contains details about why a query failed, including
    error codes and diagnostic information.
    """

    error_code: Annotated[str, Field(description="Database error code")]
    message: Annotated[str, Field(description="Human-readable error message")]
    query: Annotated[str, Field(description="The query that failed")]
    recoverable: Annotated[
        bool, Field(default=False, description="Whether the error is recoverable")
    ]

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class QueryError(BaseModel):
    """
    Error information from a failed database query.

    Contains details about why a query failed, including
    error codes and diagnostic information.
    """

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
```
</details>

❌ **Needs Work**

**NestedOrder (nested model with transitive types)**

<details>
<summary>📄 Source Code</summary>

```python
class NestedOrder(BaseModel):
    """An order with nested products.

    Represents a customer order containing one or more products.
    Tracks the order total and maintains the list of products
    with their individual details.
    """

    order_id: Annotated[str, Field(description="Order identifier")]
    products: Annotated[list[NestedProduct], Field(description="Products in order")]
    total: Annotated[float, Field(default=0.0, description="Order total")]

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class NestedOrder(BaseModel):
    """
    An order with nested products.

    Represents a customer order containing one or more products.
    Tracks the order total and maintains the list of products
    with their individual details.
    """

    order_id: str  # Order identifier
    products: list[NestedProduct]  # Products in order
    total: float = 0.0  # Order total

## Referenced Types
class NestedProduct(BaseModel):
    """A product with nested category structure."""

    sku: str  # Stock keeping unit
    name: str  # Product name
    price: float = 0.0  # Price in USD
    categories: list[str]  # Categories
    manufacturer: Manufacturer | None = None  # Product manufacturer
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class NestedOrder(BaseModel):
    """An order with nested products."""

    order_id: str  # Order identifier
    products: list[NestedProduct]  # Products in order
    total: float = 0.0  # Order total
```
</details>

❌ **Needs Work**

**NestedProduct (shows Manufacturer in Referenced Types)**

<details>
<summary>📄 Source Code</summary>

```python
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
    manufacturer: Annotated[
        Manufacturer | None, Field(default=None, description="Product manufacturer")
    ]

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class NestedProduct(BaseModel):
    """
    A product with nested category structure.

    Represents a product in the catalog with pricing, categorization,
    and manufacturer information. Products can belong to multiple
    categories and optionally have an associated manufacturer.
    """

    sku: str  # Stock keeping unit
    name: str  # Product name
    price: float = 0.0  # Price in USD
    categories: list[str]  # Categories
    manufacturer: Manufacturer | None = None  # Product manufacturer

## Referenced Types
class Manufacturer(BaseModel):
    """A product manufacturer."""

    name: str  # Manufacturer name
    country: str  # Country of origin
    certified: bool = False  # ISO certified
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class NestedProduct(BaseModel):
    """A product with nested category structure."""

    sku: str  # Stock keeping unit
    name: str  # Product name
    price: float = 0.0  # Price in USD
    categories: list[str]  # Categories
    manufacturer: Manufacturer | None = None  # Product manufacturer
```
</details>

❌ **Needs Work**

**QueryRequest instance**

<details>
<summary>📄 Instance Creation</summary>

```python
request = QueryRequest(sql="SELECT * FROM users", limit=50)
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
QueryRequest(sql='SELECT * FROM users', params={}, limit=50, timeout=30.0)
```
</details>

❌ **Needs Work**

**NestedOrder instance (nested)**

<details>
<summary>📄 Instance Creation</summary>

```python
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
        NestedProduct(sku="SKU-002", name="Gadget", price=49.99),
    ],
    total=79.98,
)
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
NestedOrder(order_id='ORD-123', products=[
    NestedProduct(sku='SKU-001', name='Widget', price=29.99, categories=[list: 1 items], manufacturer=Manufacturer(name='WidgetCorp', country='USA', certified=True)),
    NestedProduct(sku='SKU-002', name='Gadget', price=49.99, categories=[], manufacturer=None),
], total=79.98)
```
</details>

❌ **Needs Work**


### 1.2 Namespaced Tool with State

**DatabaseTool (type)**

<details>
<summary>📄 Source Code</summary>

```python
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
        return QueryResult(rows=[], row_count=0, execution_time=0.01)

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
        return QueryResult(rows=[], row_count=0, execution_time=0.01)

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class DatabaseTool:
    """
    Database operations namespace.

    Provides query, insert, and transaction operations.
    Maintains connection pool and query statistics.

    Example:
        result = self.db.query(QueryRequest(sql="SELECT * FROM users"))
        print(f"Found {result.row_count} users")
    """

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """
        Execute a query with error handling.

        Returns either successful results or detailed error information.
        Unlike query(), this method catches errors and returns them
        as structured QueryError objects instead of raising exceptions.
        """
    def insert(self, table: str, data: dict) -> int:
        """
        Insert a row into a table.

        Automatically escapes values and handles type conversion.

        Args:
            table: Target table name
            data: Row data to insert

        Returns:
            ID of inserted row
        """
    def query(self, request: QueryRequest) -> QueryResult:
        """
        Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """

## Referenced Types
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""
```
</details>

❌ **Needs Work**

**DatabaseTool (instance with state)**

<details>
<summary>📄 Instance Creation</summary>

```python
db_tool = DatabaseTool(connection_string="postgres://localhost/mydb")
db_tool.query_count = 42
db_tool.last_query = "SELECT * FROM users WHERE active = true"

```
</details>

<details>
<summary>doc(instance)</summary>

```python
class DatabaseTool:
    """
    Database operations namespace.

    Provides query, insert, and transaction operations.
    Maintains connection pool and query statistics.

    Example:
        result = self.db.query(QueryRequest(sql="SELECT * FROM users"))
        print(f"Found {result.row_count} users")
    """

    connection_string: str = 'postgres://localhost/mydb'  # Database connection string
    query_count: int = 42  # Total queries executed
    last_query: str | None = 'SELECT * FROM users WHERE active = true'  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """
        Execute a query with error handling.

        Returns either successful results or detailed error information.
        Unlike query(), this method catches errors and returns them
        as structured QueryError objects instead of raising exceptions.
        """
    def insert(self, table: str, data: dict) -> int:
        """
        Insert a row into a table.

        Automatically escapes values and handles type conversion.

        Args:
            table: Target table name
            data: Row data to insert

        Returns:
            ID of inserted row
        """
    def query(self, request: QueryRequest) -> QueryResult:
        """
        Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """

## Referenced Types
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
DatabaseTool(connection_string='postgres://localhost/mydb', query_count=42, last_query='SELECT * FROM users WHERE active = true')
```
</details>

❌ **Needs Work**

**DatabaseTool.query (method)**

<details>
<summary>📄 Source Code</summary>

```python
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """
        self.query_count += 1
        self.last_query = request.sql
        return QueryResult(rows=[], row_count=0, execution_time=0.01)

```
</details>

<details>
<summary>doc(method)</summary>

```python
def DatabaseTool.query(self, request: QueryRequest) -> QueryResult:
    """
    Execute a SQL query and return results.

    Uses parameterized queries to prevent SQL injection.
    Updates query_count and last_query state after execution.
    """

## Referenced Types
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

❌ **Needs Work**

**DatabaseTool.insert (method with Annotated params)**

<details>
<summary>📄 Source Code</summary>

```python
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

```
</details>

<details>
<summary>doc(method)</summary>

```python
def DatabaseTool.insert(self, table: str, data: dict) -> int:
    """
    Insert a row into a table.

    Automatically escapes values and handles type conversion.

    Args:
        table: Target table name
        data: Row data to insert

    Returns:
        ID of inserted row
    """
```
</details>

❌ **Needs Work**

**DatabaseTool.execute_safe (method returning QueryResult | QueryError)**

<details>
<summary>📄 Source Code</summary>

```python
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
        return QueryResult(rows=[], row_count=0, execution_time=0.01)

```
</details>

<details>
<summary>doc(method)</summary>

```python
def DatabaseTool.execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
    """
    Execute a query with error handling.

    Returns either successful results or detailed error information.
    Unlike query(), this method catches errors and returns them
    as structured QueryError objects instead of raising exceptions.
    """

## Referenced Types
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

❌ **Needs Work**


### 1.3 Agent with Namespaced Tools

**DataAgent (agent type)**

<details>
<summary>📄 Source Code</summary>

```python
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

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class DataAgent:
    """
    Data processing agent with database capabilities.

    This agent demonstrates the recommended documentation patterns:
    - Namespaced tools (self.db) with their own state
    - Typed state with Annotated descriptions
    - Methods with Pydantic input/output types

    Example:
        agent = DataAgent()
        result = await agent.fetch_user("user:123")
    """

    db: DatabaseTool = DatabaseTool()
    processed_requests: int = 0  # Total requests processed
    last_error: str | None = None  # Most recent error message
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def fetch_user(self, user_id: str) -> dict | None:
        """
        Fetch a user by ID, using cache when available.

        Checks cache first, falls back to database query.

        Args:
            user_id: User ID to fetch

        Returns:
            User data or None if not found
        """
    async def process_batch(self, requests: list[QueryRequest]) -> list[QueryResult]:
        """
        Process a batch of database queries.

        Executes queries in order, collecting results.

        Args:
            requests: Batch of queries to execute

        Returns:
            Results for each query
        """

## Referenced Types
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class DataAgent:
    """Data processing agent with database capabilities."""

    db: DatabaseTool = DatabaseTool()
    processed_requests: int = 0  # Total requests processed
    last_error: str | None = None  # Most recent error message
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def fetch_user(self, user_id: str) -> dict | None:
        """Fetch a user by ID, using cache when available."""
    async def process_batch(self, requests: list[QueryRequest]) -> list[QueryResult]:
        """Process a batch of database queries."""
```
</details>

❌ **Needs Work**

**DataAgent (instance with tool state)**

<details>
<summary>📄 Instance Creation</summary>

```python
agent = DataAgent()
agent.processed_requests = 100
agent.db.query_count = 50

```
</details>

<details>
<summary>doc(instance)</summary>

```python
class DataAgent:
    """
    Data processing agent with database capabilities.

    This agent demonstrates the recommended documentation patterns:
    - Namespaced tools (self.db) with their own state
    - Typed state with Annotated descriptions
    - Methods with Pydantic input/output types

    Example:
        agent = DataAgent()
        result = await agent.fetch_user("user:123")
    """

    db: DatabaseTool = DatabaseTool(connection_string='sqlite:///:memory:', query_count=50, last_query=None)
    processed_requests: int = 100  # Total requests processed
    last_error: str | None = None  # Most recent error message
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def fetch_user(self, user_id: str) -> dict | None:
        """
        Fetch a user by ID, using cache when available.

        Checks cache first, falls back to database query.

        Args:
            user_id: User ID to fetch

        Returns:
            User data or None if not found
        """
    async def process_batch(self, requests: list[QueryRequest]) -> list[QueryResult]:
        """
        Process a batch of database queries.

        Executes queries in order, collecting results.

        Args:
            requests: Batch of queries to execute

        Returns:
            Results for each query
        """

## Referenced Types
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
DataAgent(db=DatabaseTool(connection_string='sqlite:///:memory:', query_count=50, last_query=None), processed_requests=100, last_error=None, events=EventView(), context=ContextManager())
```
</details>

❌ **Needs Work**

**DataAgent.fetch_user (method)**

<details>
<summary>📄 Source Code</summary>

```python
    async def fetch_user(
        self,
        user_id: Annotated[str, "User ID to fetch"],
    ) -> Annotated[dict | None, "User data or None if not found"]:
        """Fetch a user by ID, using cache when available.

        Checks cache first, falls back to database query.
        """
        ...

```
</details>

<details>
<summary>doc(method)</summary>

```python
async def DataAgent.fetch_user(self, user_id: str) -> dict | None:
    """
    Fetch a user by ID, using cache when available.

    Checks cache first, falls back to database query.

    Args:
        user_id: User ID to fetch

    Returns:
        User data or None if not found
    """
```
</details>

❌ **Needs Work**

**DataAgent.process_batch (method with Pydantic I/O)**

<details>
<summary>📄 Source Code</summary>

```python
    async def process_batch(
        self,
        requests: Annotated[list[QueryRequest], "Batch of queries to execute"],
    ) -> Annotated[list[QueryResult], "Results for each query"]:
        """Process a batch of database queries.

        Executes queries in order, collecting results.
        """
        ...

```
</details>

<details>
<summary>doc(method)</summary>

```python
async def DataAgent.process_batch(self, requests: list[QueryRequest]) -> list[QueryResult]:
    """
    Process a batch of database queries.

    Executes queries in order, collecting results.

    Args:
        requests: Batch of queries to execute

    Returns:
        Results for each query
    """

## Referenced Types
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

❌ **Needs Work**


### 1.4 Progressive Disclosure - Drilling Down

**Demonstrates:** Top-level agent → namespace → method navigation

**agent (top level)**

<details>
<summary>doc(instance)</summary>

```python
class DataAgent:
    """
    Data processing agent with database capabilities.

    This agent demonstrates the recommended documentation patterns:
    - Namespaced tools (self.db) with their own state
    - Typed state with Annotated descriptions
    - Methods with Pydantic input/output types

    Example:
        agent = DataAgent()
        result = await agent.fetch_user("user:123")
    """

    db: DatabaseTool = DatabaseTool(connection_string='sqlite:///:memory:', query_count=50, last_query=None)
    processed_requests: int = 100  # Total requests processed
    last_error: str | None = None  # Most recent error message
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def fetch_user(self, user_id: str) -> dict | None:
        """
        Fetch a user by ID, using cache when available.

        Checks cache first, falls back to database query.

        Args:
            user_id: User ID to fetch

        Returns:
            User data or None if not found
        """
    async def process_batch(self, requests: list[QueryRequest]) -> list[QueryResult]:
        """
        Process a batch of database queries.

        Executes queries in order, collecting results.

        Args:
            requests: Batch of queries to execute

        Returns:
            Results for each query
        """

## Referenced Types
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
DataAgent(db=DatabaseTool(connection_string='sqlite:///:memory:', query_count=50, last_query=None), processed_requests=100, last_error=None, events=EventView(), context=ContextManager())
```
</details>

❌ **Needs Work**

**agent.db (namespace)**

<details>
<summary>doc(instance)</summary>

```python
class DatabaseTool:
    """
    Database operations namespace.

    Provides query, insert, and transaction operations.
    Maintains connection pool and query statistics.

    Example:
        result = self.db.query(QueryRequest(sql="SELECT * FROM users"))
        print(f"Found {result.row_count} users")
    """

    connection_string: str = 'sqlite:///:memory:'  # Database connection string
    query_count: int = 50  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """
        Execute a query with error handling.

        Returns either successful results or detailed error information.
        Unlike query(), this method catches errors and returns them
        as structured QueryError objects instead of raising exceptions.
        """
    def insert(self, table: str, data: dict) -> int:
        """
        Insert a row into a table.

        Automatically escapes values and handles type conversion.

        Args:
            table: Target table name
            data: Row data to insert

        Returns:
            ID of inserted row
        """
    def query(self, request: QueryRequest) -> QueryResult:
        """
        Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """

## Referenced Types
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
DatabaseTool(connection_string='sqlite:///:memory:', query_count=50, last_query=None)
```
</details>

❌ **Needs Work**

**agent.db.query (method)**

<details>
<summary>📄 Source Code</summary>

```python
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """
        self.query_count += 1
        self.last_query = request.sql
        return QueryResult(rows=[], row_count=0, execution_time=0.01)

```
</details>

<details>
<summary>doc(method)</summary>

```python
def DatabaseTool.query(self, request: QueryRequest) -> QueryResult:
    """
    Execute a SQL query and return results.

    Uses parameterized queries to prevent SQL injection.
    Updates query_count and last_query state after execution.
    """

## Referenced Types
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

❌ **Needs Work**


## 2. Agent Classes


**Purpose:** Test Agent-specific rendering (child agents, tools, state).


### 2.1 Agent with Child Agent

**CoordinatorAgent (type)**

<details>
<summary>📄 Source Code</summary>

```python
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

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class CoordinatorAgent:
    """Coordinates work across multiple workers."""

    WorkerAgent: type[WorkerAgent] = WorkerAgent
    db: DatabaseTool = DatabaseTool()
    active_workers: list[WorkerAgent] = []
    task_queue: list[str] = []
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def distribute_tasks(self, tasks: list[str]) -> list[str]:
        """
        Distribute tasks to workers and collect results.

        Args:
            tasks: Tasks to distribute

        Returns:
            Task results from workers
        """

## Referenced Types
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""
class WorkerAgent:
    """A worker agent that performs tasks."""

    worker_id: worker_id
    tasks_completed: int = 0
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class CoordinatorAgent:
    """Coordinates work across multiple workers."""

    WorkerAgent: type[WorkerAgent] = WorkerAgent
    db: DatabaseTool = DatabaseTool()
    active_workers: list[WorkerAgent] = []
    task_queue: list[str] = []
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def distribute_tasks(self, tasks: list[str]) -> list[str]:
        """Distribute tasks to workers and collect results."""
```
</details>

❌ **Needs Work**

**CoordinatorAgent with type_depth=100**

<details>
<summary>doc(Type, type_depth=100) - Full transitive references</summary>

```python
class CoordinatorAgent:
    """Coordinates work across multiple workers."""

    WorkerAgent: type[WorkerAgent] = WorkerAgent
    db: DatabaseTool = DatabaseTool()
    active_workers: list[WorkerAgent] = []
    task_queue: list[str] = []
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def distribute_tasks(self, tasks: list[str]) -> list[str]:
        """
        Distribute tasks to workers and collect results.

        Args:
            tasks: Tasks to distribute

        Returns:
            Task results from workers
        """

## Referenced Types
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""

## Referenced Types
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
class WorkerAgent:
    """A worker agent that performs tasks."""

    worker_id: worker_id
    tasks_completed: int = 0
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
```
</details>

**CoordinatorAgent (instance)**

<details>
<summary>doc(instance)</summary>

```python
class CoordinatorAgent:
    """Coordinates work across multiple workers."""

    WorkerAgent: type[WorkerAgent] = class WorkerAgent:
    """A worker agent that performs tasks."""

    worker_id: worker_id
    tasks_completed: int = 0
    events: EventView = EventView()
    ... +1

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
    db: DatabaseTool = DatabaseTool(connection_string='sqlite:///:memory:', query_count=0, last_query=None)
    active_workers: list[WorkerAgent] = []
    task_queue: list[str] = ['task1', 'task2', 'task3']
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def distribute_tasks(self, tasks: list[str]) -> list[str]:
        """
        Distribute tasks to workers and collect results.

        Args:
            tasks: Tasks to distribute

        Returns:
            Task results from workers
        """

## Referenced Types
class DatabaseTool:
    """Database operations namespace."""

    connection_string: str  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """Execute a query with error handling."""
    def insert(self, table: str, data: dict) -> int:
        """Insert a row into a table."""
    def query(self, request: QueryRequest) -> QueryResult:
        """Execute a SQL query and return results."""
class WorkerAgent:
    """A worker agent that performs tasks."""

    worker_id: worker_id
    tasks_completed: int = 0
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
CoordinatorAgent(WorkerAgent=WorkerAgent, db=DatabaseTool(connection_string='sqlite:///:memory:', query_count=0, last_query=None), active_workers=[], task_queue=['task1', 'task2', 'task3'], events=EventView(), context=ContextManager())
```
</details>

❌ **Needs Work**


### 2.2 Child Agent as Attribute

**WorkerAgent (child agent class)**

<details>
<summary>📄 Source Code</summary>

```python
class WorkerAgent(Agent, llm=FakeLLMClient()):
    """A worker agent that performs tasks."""

    def __init__(self, worker_id: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.worker_id = worker_id
        self.tasks_completed = 0

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
        ...

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class WorkerAgent:
    """A worker agent that performs tasks."""

    worker_id: worker_id
    tasks_completed: int = 0
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class WorkerAgent:
    """A worker agent that performs tasks."""

    worker_id: worker_id
    tasks_completed: int = 0
    events: EventView = EventView()
    context: ContextManager = ContextManager()

    async def do_task(self, task: str) -> str:
        """Perform a task and return result."""
```
</details>

❌ **Needs Work**


### 2.3 Tool Instance as Attribute

**coordinator.db (tool)**

<details>
<summary>doc(instance)</summary>

```python
class DatabaseTool:
    """
    Database operations namespace.

    Provides query, insert, and transaction operations.
    Maintains connection pool and query statistics.

    Example:
        result = self.db.query(QueryRequest(sql="SELECT * FROM users"))
        print(f"Found {result.row_count} users")
    """

    connection_string: str = 'sqlite:///:memory:'  # Database connection string
    query_count: int = 0  # Total queries executed
    last_query: str | None = None  # Most recent query

    def execute_safe(self, request: QueryRequest) -> QueryResult | QueryError:
        """
        Execute a query with error handling.

        Returns either successful results or detailed error information.
        Unlike query(), this method catches errors and returns them
        as structured QueryError objects instead of raising exceptions.
        """
    def insert(self, table: str, data: dict) -> int:
        """
        Insert a row into a table.

        Automatically escapes values and handles type conversion.

        Args:
            table: Target table name
            data: Row data to insert

        Returns:
            ID of inserted row
        """
    def query(self, request: QueryRequest) -> QueryResult:
        """
        Execute a SQL query and return results.

        Uses parameterized queries to prevent SQL injection.
        Updates query_count and last_query state after execution.
        """

## Referenced Types
class QueryError(BaseModel):
    """Error information from a failed database query."""

    error_code: str  # Database error code
    message: str  # Human-readable error message
    query: str  # The query that failed
    recoverable: bool = False  # Whether the error is recoverable
class QueryRequest(BaseModel):
    """A database query request."""

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]  # Query result rows
    row_count: int  # Number of rows returned
    execution_time: float  # Query execution time in seconds
    truncated: bool = False  # True if results were truncated
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
DatabaseTool(connection_string='sqlite:///:memory:', query_count=0, last_query=None)
```
</details>

❌ **Needs Work**

**coordinator.distribute_tasks (method)**

<details>
<summary>📄 Source Code</summary>

```python
    async def distribute_tasks(
        self, tasks: Annotated[list[str], "Tasks to distribute"]
    ) -> Annotated[list[str], "Task results from workers"]:
        """Distribute tasks to workers and collect results."""
        ...

```
</details>

<details>
<summary>doc(method)</summary>

```python
async def CoordinatorAgent.distribute_tasks(self, tasks: list[str]) -> list[str]:
    """
    Distribute tasks to workers and collect results.

    Args:
        tasks: Tasks to distribute

    Returns:
        Task results from workers
    """
```
</details>

❌ **Needs Work**


## 3. Runtime Objects


**Purpose:** Test agent006-specific runtime components.


### 3.1 Events

**agent.events (Events)**

<details>
<summary>doc(instance)</summary>

```python
class EventView:
    """
    Query past events. Like a database, not an array.

    This is the agent-facing view of the event manager. It provides
    read-only access through a minimal interface:

    - query(): Query events by type, call, text search, or regex
    - get(): Safe access by tag or uuid (returns None if not found)
    - [key]: Access by tag or uuid (raises KeyError if not found)
    - key in events: Check if tag/uuid exists

    Identifiers:
        - **tag**: A short positional label assigned on insert ("1", "2", ...,
          or "2..40" for summaries). Tags are stable — once assigned they never
          change, even after summarization collapses earlier events.
        - **uuid**: A globally unique UUID string generated when the event is
          created. Useful when you need to reference an event across systems
          or when the tag is unknown.

    Examples:
        # Query events
        events.query(limit=50)                      # Recent 50
        events.query(type="task")                   # All task events
        events.query(type="python_output")          # All execution outputs
        events.query(call_id="abc123")               # Events for call
        events.query(query="error")                 # Text search
        events.query(query="error.*db", regex=True) # Regex search
        events.query(type="task", call_id="abc")    # Combined (ANDed)

        # Access by tag
        events.get("5")                              # By tag, None if missing
        events["5"]                                  # By tag, KeyError if missing
        events[["2", "3", "4"]]                      # Multiple tags

        # Access summary child tags
        summary = events["1..22"]
        child_events = events[summary.children_tags]

        # Check existence
        "5" in events                                # True/False

        # Events have their tag built-in
        for event in events.query(type="task"):
            print(f"Task at {event.tag}: {event.prompt}")
    """

    def get(self, key: str | list[str]) -> EventBase | list[EventBase] | None:
        """
        Get event(s) by tag or uuid.

        Safe access that returns None if not found (for single key)
        or filters out missing events (for list of keys).

        Args:
            key: Single tag/uuid or list of tags/uuids.
                - "5" → single event by tag
                - "abc123-..." → single event by uuid
                - "1..22" → Summary by range tag
                - ["2", "3", "4"] → list of events
                - summary.children_tags → list of child tags

        Returns:
            - Single key: Event | None
            - List of keys: list[Event] (missing events filtered out)

        Examples:
            events.get("5")                    # By tag, None if missing
            events.get("abc123-uuid...")        # By uuid, None if missing
            events.get("1..22")                # Summary event
            events.get(["2", "3", "4"])        # Multiple events
            events.get(summary.children_tags)  # Child events of a summary
        """
    def query(self, type: str | None = None, call_id: str | None = None, query: str | None = None, regex: bool = False, limit: int | None = None) -> list[EventBase]:
        """
        Query events with AND semantics.

        Multiple filters are ANDed together. Results are returned in
        chronological order, with limit taking the most recent.

        Args:
            type: Event type filter (e.g., "task", "python_output", "tool_call")
            call_id: Call ID filter (matches metadata.call_id)
            query: Text search (case-insensitive substring, or regex if regex=True)
            regex: If True, treat query as regex pattern
            limit: Maximum results (most recent first when limit < total)

        Returns:
            List of matching events.

        Examples:
            events.query(limit=50)                      # Recent 50
            events.query(type="task")                   # All task events
            events.query(type="python_output")          # Execution outputs
            events.query(call_id="abc123")              # Events for call
            events.query(query="error")                 # Text search
            events.query(query="error.*db", regex=True) # Regex search
            events.query(type="task", call_id="abc")    # Combined (ANDed)
        """

## Referenced Types
class EventBase(BaseModel):
    """Base class for all events."""

    event_type: str = 'event'  # Event type discriminator
    id: str  # Unique UUID for this event
    metadata: dict[str, Any]  # Arbitrary metadata (call_id, source, etc.)
    status: EventStatus = <EventStatus.ACTIVE: 'active'>  # Active or archived
    tag: str | None = None  # Positional tag assigned by EventManager ('1', '2', '2..40')
    timestamp: datetime  # Creation time
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
EventView()
```
</details>

❌ **Needs Work**


### 3.2 ContextManager

**agent.context (ContextManager)**

<details>
<summary>doc(instance)</summary>

```python
class ContextManager:
    """
    Dict-like API for managing context blocks.

    Stores context blocks as key -> value mappings. Values are either static
    values (any type) or DynamicContext markers (for expressions re-evaluated each turn).

    Single source of truth:
    - Static blocks: value lives in _blocks only. __getitem__ reads from _blocks.
    - DynamicContext blocks: DynamicContext marker in _blocks, resolved value in _dynamic_cache.
      Cache is populated by _update_resolved() after each _prepare_context() run,
      and invalidated on set_dynamic() or __setitem__().

    Framework blocks (system_prompt, self, etc.) are managed separately
    by _prepare_context() and cannot be set here.
    """

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a block value, returning default if not found.

        Like dict.get() — returns default instead of raising KeyError.
        """
    def keys(self):
        """Return block keys."""
    def pop(self, key: str, args: Any) -> Any:
        """
        Remove and return a block value.

        Like dict.pop() — returns default if provided, raises KeyError otherwise.
        """
    def set_dynamic(self, key: str, expr: str):
        """
        Set a dynamic context block that re-evaluates each turn.

        Args:
            key: Block key (unique identifier).
            expr: Python expression to evaluate each turn.

        Raises:
            ProtectedBlockError: If key is protected.
        """
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
ContextManager()
```
</details>

❌ **Needs Work**


## 4. Event Types


**Purpose:** Test event class and instance rendering.


### 4.1 Event Class

**Task (type)**

<details>
<summary>📄 Source Code</summary>

```python
class Task(EventBase):
    """Task prompt event - added at start of generation."""

    event_type: Literal["task"] = Field(default="task", repr=False)
    _role: ClassVar[Role] = Role.USER

    prompt: Annotated[str, Field(description="Task prompt describing what to do")]

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class Task(BaseModel):
    """Task prompt event - added at start of generation."""

    event_type: Literal[task] = 'task'
    id: str  # Unique UUID for this event
    metadata: dict[str, Any]  # Arbitrary metadata (call_id, source, etc.)
    status: EventStatus = <EventStatus.ACTIVE: 'active'>  # Active or archived
    tag: str | None = None  # Positional tag assigned by EventManager ('1', '2', '2..40')
    timestamp: datetime  # Creation time
    prompt: str  # Task prompt describing what to do
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class Task(BaseModel):
    """Task prompt event - added at start of generation."""

    event_type: Literal[task] = 'task'
    id: str  # Unique UUID for this event
    metadata: dict[str, Any]  # Arbitrary metadata (call_id, source, etc.)
    status: EventStatus = <EventStatus.ACTIVE: 'active'>  # Active or archived
    tag: str | None = None  # Positional tag assigned by EventManager ('1', '2', '2..40')
    timestamp: datetime  # Creation time
    prompt: str  # Task prompt describing what to do
```
</details>

❌ **Needs Work**


### 4.2 Event Instances

**Task instance**

<details>
<summary>pformat(instance)</summary>

```python
Task(prompt='Process the data')
```
</details>

❌ **Needs Work**

**ExecutionResult instance**

<details>
<summary>pformat(instance)</summary>

```python
ExecutionResult(stdout='Hello World\\n', stderr='', error=None, signal=None, defined_methods={}, returned_value=<object object at 0x7e9e00380e20>, explicit_return=False, captured_locals={}, wrapper_line_offset=0)
```
</details>

❌ **Needs Work**


## 5. Dataclasses


**Purpose:** Test standard library dataclass rendering.


### 5.1 Simple Dataclass

**Point (simple)**

<details>
<summary>📄 Source Code</summary>

```python
@dataclass
class Point:
    """A 2D point with coordinates."""

    x: float
    y: float

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
@dataclass
class Point:
    """A 2D point with coordinates."""

    x: float
    y: float
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
@dataclass
class Point:
    """A 2D point with coordinates."""

    x: float
    y: float
```
</details>

❌ **Needs Work**

**Point instance**

<details>
<summary>pformat(instance)</summary>

```python
Point(x=10.5, y=20.3)
```
</details>

❌ **Needs Work**


### 5.2 Complex Dataclass

**ComplexSession (with lists and recursion)**

<details>
<summary>📄 Source Code</summary>

```python
@dataclass
class ComplexSession:
    """A complex dataclass with multiple field types."""

    session_id: str
    agent_name: str
    depth: int = 0
    turns: list[dict] = field(default_factory=list)
    children: list["ComplexSession"] = field(default_factory=list)
    status: str = "OK"

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
@dataclass
class ComplexSession:
    """A complex dataclass with multiple field types."""

    session_id: str
    agent_name: str
    depth: int = 0
    turns: list[dict] = []
    children: list[ComplexSession] = []
    status: str = 'OK'
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
@dataclass
class ComplexSession:
    """A complex dataclass with multiple field types."""

    session_id: str
    agent_name: str
    depth: int = 0
    turns: list[dict] = []
    children: list[ComplexSession] = []
    status: str = 'OK'
```
</details>

❌ **Needs Work**

**ComplexSession instance**

<details>
<summary>📄 Instance Creation</summary>

```python
session = ComplexSession(
    session_id="abc123",
    agent_name="TestAgent",
    depth=0,
    turns=[{"type": "llm", "content": "thinking..."}, {"type": "exec", "output": "42"}],
)
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
ComplexSession(session_id='abc123', agent_name='TestAgent', depth=0, turns=[{'type': 'llm', 'content': 'thinking...'}, {'type': 'exec', 'output': '42'}], children=[], status='OK')
```
</details>

❌ **Needs Work**


### 5.3 List of Dataclasses

**list[ComplexSession]**

<details>
<summary>pformat(instance)</summary>

```python
[
    ComplexSession(session_id='s1', agent_name='Agent1', depth=0, ... +3),
    ComplexSession(session_id='s2', agent_name='Agent2', depth=1, ... +3),
    ComplexSession(session_id='s3', agent_name='Agent3', depth=2, ... +3),
]
```
</details>

❌ **Needs Work**

**list[ComplexSession] (truncated)**

<details>
<summary>pformat(obj, max_length=2)</summary>

```python
[
    ComplexSession(session_id='s1', agent_name='Agent1', ... +4),
    ComplexSession(session_id='s2', agent_name='Agent2', ... +4),
    ... +1
]
```
</details>

❌ **Needs Work**


## 6. Plain Python Classes


**Purpose:** Test non-decorated Python classes.


### 6.1 Plain Class

**Calculator (type)**

<details>
<summary>📄 Source Code</summary>

```python
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

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class Calculator:
    """A simple calculator tool."""

    history: list[str] = []
    last_result: float = 0

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class Calculator:
    """A simple calculator tool."""

    history: list[str] = []
    last_result: float = 0

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
```
</details>

❌ **Needs Work**

**Calculator instance**

<details>
<summary>📄 Instance Creation</summary>

```python
calc = Calculator()
calc.add(10, 20)
calc.multiply(5, 6)

```
</details>

<details>
<summary>doc(instance)</summary>

```python
class Calculator:
    """A simple calculator tool."""

    history: list[str] = ['10 + 20 = 30', '5 * 6 = 30']
    last_result: float = 30

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
Calculator(history=['10 + 20 = 30', '5 * 6 = 30'], last_result=30)
```
</details>

❌ **Needs Work**


## 7. Other Common Types


**Purpose:** Test TypedDict, NamedTuple, Enum rendering.


### 7.1 TypedDict

**ConfigDict (TypedDict)**

<details>
<summary>📄 Source Code</summary>

```python
class ConfigDict(TypedDict, total=False):
    """Configuration with optional fields."""

    debug: bool
    log_level: str
    max_retries: int

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class ConfigDict(TypedDict):
    """Configuration with optional fields."""

    debug: bool  # optional
    log_level: str  # optional
    max_retries: int  # optional
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class ConfigDict(TypedDict):
    """Configuration with optional fields."""

    debug: bool  # optional
    log_level: str  # optional
    max_retries: int  # optional
```
</details>

❌ **Needs Work**

**ConfigDict instance**

<details>
<summary>pformat(instance)</summary>

```python
{'debug': True, 'log_level': 'INFO', 'max_retries': 3}
```
</details>

❌ **Needs Work**


### 7.2 NamedTuple

**Coordinate (NamedTuple)**

<details>
<summary>📄 Source Code</summary>

```python
class Coordinate(NamedTuple):
    """A geographic coordinate."""

    latitude: float
    longitude: float
    altitude: float = 0.0

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class Coordinate(NamedTuple):
    """A geographic coordinate."""

    latitude: float
    longitude: float
    altitude: float = 0.0
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class Coordinate(NamedTuple):
    """A geographic coordinate."""

    latitude: float
    longitude: float
    altitude: float = 0.0
```
</details>

❌ **Needs Work**

**Coordinate instance**

<details>
<summary>pformat(instance)</summary>

```python
Coordinate(latitude=40.7128, longitude=-74.006, altitude=10.0)
```
</details>

❌ **Needs Work**


### 7.3 Enum

**TaskStatus (Enum)**

<details>
<summary>📄 Source Code</summary>

```python
class TaskStatus(Enum):
    """Status of a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class TaskStatus(Enum):
    """Status of a task."""

    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class TaskStatus(Enum):
    """Status of a task."""

    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
```
</details>

❌ **Needs Work**

**TaskStatus.RUNNING (member)**

<details>
<summary>pformat(instance)</summary>

```python
TaskStatus.RUNNING
```
</details>

❌ **Needs Work**


## 8. Configuration Types


**Purpose:** Test agent006 configuration objects.


### 8.1 TruncationConfig

**TruncationConfig (type)**

<details>
<summary>📄 Source Code</summary>

```python
class TruncationConfig(BaseModel):
    """Configuration for output truncation.

    Configures truncation at multiple levels:
    1. Per-block limit: individual context blocks and events (20KB default)
    2. Context limit: total character budget for all system/context blocks
    3. Event limit: total character budget for all events
    4. Stdout/stderr per execute_python() call (50KB/20KB)
    5. Smart printing defaults for pprint() (50 elements, 500 chars, depth 4)

    Can be set at class, instance, or method level via Agent configuration:
        class MyAgent(Agent, truncation=TruncationConfig(...))
        agent = MyAgent(truncation=TruncationConfig(...))
        @strategy(..., truncation=TruncationConfig(...))

    Attributes:
        block_limit: Max chars per individual block (context or event).
        context_limit: Max total chars for all system/context blocks combined.
            When exceeded, blocks are re-truncated to fit the budget.
        event_limit: Max total chars for all events combined.
            When exceeded, oldest events are dropped first.
        stdout_limit: Max chars per execute_python() stdout.
        stderr_limit: Max chars per execute_python() stderr.
        max_length: Default max container elements for framework pprint() calls.
        max_string: Default max string chars for framework pprint() calls.
        max_depth: Default max nesting depth for framework pprint() calls.
    """

    model_config = ConfigDict(
        frozen=True,
        # Exclude _explicitly_set from repr and equality comparison
        # Use exclude for serialization control (not shown in dict/json)
    )

    # Per-block limit (applies to both context blocks and events)
    block_limit: Annotated[int, Field(description="Max chars per block")] = 20_000

    # Total section limits (None = no limit)
    context_limit: Annotated[
        int | None, Field(default=None, description="Total context budget")
    ] = None
    event_limit: Annotated[int | None, Field(default=None, description="Total event budget")] = None

    # Execution output limits
    stdout_limit: Annotated[int, Field(description="Max stdout chars per execute_python")] = 50_000
    stderr_limit: Annotated[int, Field(description="Max stderr chars per execute_python")] = 20_000

    # Default pprint limits (used by prefill inspection AND return value formatting)
    max_length: Annotated[int | None, Field(default=50, description="Max container elements")] = 50
    max_string: Annotated[int | None, Field(default=500, description="Max string chars")] = 500
    max_depth: Annotated[int | None, Field(default=4, description="Max nesting depth")] = 4

    # Track which fields were explicitly passed to __init__
    # This is NOT a Field — it's metadata for merge_with() logic
    # Pydantic doesn't serialize it by default (no Field descriptor)
    _explicitly_set: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _track_explicitly_set(self) -> "TruncationConfig":
        """Track which fields were explicitly passed to __init__.

        Uses model_fields_set to detect what was explicitly provided vs defaulted.
        """
        # Create a new instance with _explicitly_set populated
        # This is safe because we're in mode="after" — all fields are validated
        object.__setattr__(self, "_explicitly_set", frozenset(self.model_fields_set))
        return self

    def merge_with(self, other: "TruncationConfig | None") -> "TruncationConfig":
        """Merge with another config (other takes precedence for explicitly-set values).

        Only fields that were explicitly passed to the other config's __init__
        will override. This means TruncationConfig(block_limit=20_000) correctly
        overrides, unlike the previous approach which compared against default values.

        Args:
            other: Config to merge with (overrides self).

        Returns:
            New merged TruncationConfig.
        """
        if other is None:
            return self

        # Build kwargs for merged config
        merged_kwargs = {}
        for field_name in TruncationConfig.model_fields.keys():
            if field_name.startswith("_"):
                continue
            if field_name in other._explicitly_set:
                merged_kwargs[field_name] = getattr(other, field_name)
            else:
                merged_kwargs[field_name] = getattr(self, field_name)

        return TruncationConfig(**merged_kwargs)

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class TruncationConfig(BaseModel):
    """
    Configuration for output truncation.

    Configures truncation at multiple levels:
    1. Per-block limit: individual context blocks and events (20KB default)
    2. Context limit: total character budget for all system/context blocks
    3. Event limit: total character budget for all events
    4. Stdout/stderr per execute_python() call (50KB/20KB)
    5. Smart printing defaults for pprint() (50 elements, 500 chars, depth 4)

    Can be set at class, instance, or method level via Agent configuration:
        class MyAgent(Agent, truncation=TruncationConfig(...))
        agent = MyAgent(truncation=TruncationConfig(...))
        @strategy(..., truncation=TruncationConfig(...))

    Attributes:
        block_limit: Max chars per individual block (context or event).
        context_limit: Max total chars for all system/context blocks combined.
            When exceeded, blocks are re-truncated to fit the budget.
        event_limit: Max total chars for all events combined.
            When exceeded, oldest events are dropped first.
        stdout_limit: Max chars per execute_python() stdout.
        stderr_limit: Max chars per execute_python() stderr.
        max_length: Default max container elements for framework pprint() calls.
        max_string: Default max string chars for framework pprint() calls.
        max_depth: Default max nesting depth for framework pprint() calls.
    """

    block_limit: int = 20000  # Max chars per block
    context_limit: int | None = None  # Total context budget
    event_limit: int | None = None  # Total event budget
    stdout_limit: int = 50000  # Max stdout chars per execute_python
    stderr_limit: int = 20000  # Max stderr chars per execute_python
    max_length: int | None = 50  # Max container elements
    max_string: int | None = 500  # Max string chars
    max_depth: int | None = 4  # Max nesting depth

    def merge_with(self, other: TruncationConfig | None) -> TruncationConfig:
        """
        Merge with another config (other takes precedence for explicitly-set values).

        Only fields that were explicitly passed to the other config's __init__
        will override. This means TruncationConfig(block_limit=20_000) correctly
        overrides, unlike the previous approach which compared against default values.

        Args:
            other: Config to merge with (overrides self).

        Returns:
            New merged TruncationConfig.
        """
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class TruncationConfig(BaseModel):
    """Configuration for output truncation."""

    block_limit: int = 20000  # Max chars per block
    context_limit: int | None = None  # Total context budget
    event_limit: int | None = None  # Total event budget
    stdout_limit: int = 50000  # Max stdout chars per execute_python
    stderr_limit: int = 20000  # Max stderr chars per execute_python
    max_length: int | None = 50  # Max container elements
    max_string: int | None = 500  # Max string chars
    max_depth: int | None = 4  # Max nesting depth

    def merge_with(self, other: TruncationConfig | None) -> TruncationConfig:
        """Merge with another config (other takes precedence for explicitly-set values)."""
```
</details>

❌ **Needs Work**

**TruncationConfig instance**

<details>
<summary>pformat(instance)</summary>

```python
TruncationConfig(block_limit=20000, context_limit=None, event_limit=None, stdout_limit=10000, stderr_limit=5000, max_length=50, max_string=500, max_depth=4)
```
</details>

❌ **Needs Work**


### 8.2 DynamicContext (context block marker)

**DynamicContext (type)**

<details>
<summary>📄 Source Code</summary>

```python
class DynamicContext(BaseModel):
    """Marks a context block for dynamic evaluation each turn.

    Wraps a Python expression string that will be evaluated by the runtime
    at each LLM turn. The expression is validated at creation time.

    Usage:
        self.context.set_dynamic("status", "self.format_status()")
        self.context.set_dynamic("progress", "self.todo.show_active()")

    The expression must be valid Python (compilable as an eval expression).
    """

    model_config = ConfigDict(frozen=True)

    expr: Annotated[str, Field(description="Python expression to evaluate each turn")]

    def __init__(self, expr: str, **kwargs: Any):
        """Create a DynamicContext block marker.

        Args:
            expr: Python expression to evaluate each turn.

        Raises:
            BlockSyntaxError: If expr is not valid Python syntax.
        """
        try:
            compile(expr, "<block_expr>", "eval")
        except SyntaxError as e:
            raise BlockSyntaxError(key="<dynamic>", expr=expr, original_error=e) from e
        super().__init__(expr=expr, **kwargs)

    def __repr__(self) -> str:
        return f"DynamicContext({self.expr!r})"

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class DynamicContext(BaseModel):
    """
    Marks a context block for dynamic evaluation each turn.

    Wraps a Python expression string that will be evaluated by the runtime
    at each LLM turn. The expression is validated at creation time.

    Usage:
        self.context.set_dynamic("status", "self.format_status()")
        self.context.set_dynamic("progress", "self.todo.show_active()")

    The expression must be valid Python (compilable as an eval expression).
    """

    expr: str  # Python expression to evaluate each turn
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class DynamicContext(BaseModel):
    """Marks a context block for dynamic evaluation each turn."""

    expr: str  # Python expression to evaluate each turn
```
</details>

❌ **Needs Work**

**DynamicContext instance**

<details>
<summary>pformat(instance)</summary>

```python
DynamicContext(expr='self.get_status()')
```
</details>

❌ **Needs Work**


## 9. Functions


**Purpose:** Test function signature rendering.


### 9.1 Sync Function

**example_function (sync)**

<details>
<summary>📄 Source Code</summary>

```python
    def example_function(data: list[dict], limit: int = 10) -> list[dict]:
        """Process data with an optional limit.

        Args:
            data: Input data to process
            limit: Maximum items to return

        Returns:
            Processed and limited data
        """
        return data[:limit]

```
</details>

<details>
<summary>doc(method)</summary>

```python
def example_function(data: list[dict], limit: int = 10) -> list[dict]:
    """
    Process data with an optional limit.

    Args:
        data: Input data to process
        limit: Maximum items to return

    Returns:
        Processed and limited data
    """
```
</details>

❌ **Needs Work**


### 9.2 Async Function

**async_fetch (async)**

<details>
<summary>📄 Source Code</summary>

```python
    async def async_fetch(url: str, timeout: float = 30.0) -> dict:
        """Fetch data from a URL asynchronously.

        Args:
            url: The URL to fetch
            timeout: Request timeout in seconds

        Returns:
            Response data as dictionary
        """
        return {}

```
</details>

<details>
<summary>doc(method)</summary>

```python
async def async_fetch(url: str, timeout: float = 30.0) -> dict:
    """
    Fetch data from a URL asynchronously.

    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Response data as dictionary
    """
```
</details>

❌ **Needs Work**


## 10. Complex String Fields


**Purpose:** Test multi-line string rendering (CRITICAL for doc() output storage).

**What to review:**
- Multi-line strings use triple quotes
- Special characters handled correctly
- Truncation behavior is sensible


### 10.1 Class with String Fields

**DocumentationCache (type)**

<details>
<summary>📄 Source Code</summary>

```python
class DocumentationCache:
    """Stores cached documentation strings.

    Tests how agentdoc handles complex multi-line string values.
    """

    def __init__(self):
        self.user_doc: str = ""
        self.calculator_doc: str = ""
        self.simple_message: str = "Hello, world!"

```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class DocumentationCache:
    """
    Stores cached documentation strings.

    Tests how agentdoc handles complex multi-line string values.
    """

    user_doc: str = ''
    calculator_doc: str = ''
    simple_message: str = 'Hello, world!'
```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class DocumentationCache:
    """Stores cached documentation strings."""

    user_doc: str = ''
    calculator_doc: str = ''
    simple_message: str = 'Hello, world!'
```
</details>

❌ **Needs Work**


### 10.2 Instance with Cached Documentation

**DocumentationCache (default truncation)**

<details>
<summary>pformat(instance)</summary>

```python
DocumentationCache(user_doc='''class QueryRequest(BaseModel):
    """
    A database query request.

    Encapsulates query parameters with validation and defaults.
    Used as '''+248, calculator_doc='''class Calculator:
    """A simple calculator tool."""

    history: list[str] = []
    last_result: float = 0

    def add(self, a: float, b: float) -'''+128, simple_message='Hello, world!')
```
</details>

❌ **Needs Work**

**DocumentationCache (full content)**

<details>
<summary>pformat(obj, max_string=500)</summary>

```python
DocumentationCache(user_doc='''class QueryRequest(BaseModel):
    """
    A database query request.

    Encapsulates query parameters with validation and defaults.
    Used as input to DatabaseTool.query() method.
    """

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]''', calculator_doc='''class Calculator:
    """A simple calculator tool."""

    history: list[str] = []
    last_result: float = 0

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""''', simple_message='Hello, world!')
```
</details>

❌ **Needs Work**


### 10.3 Doc Output as String

**Full doc string:** 398 characters

**doc(QueryRequest) as string (default)**

<details>
<summary>pformat(instance)</summary>

```python
'''class QueryRequest(BaseModel):
    """
    A database query request.

    Encapsulates query parameters with validation and defaults.
    Used as input to DatabaseTool.query() method.
    """

    sql: str  # SQL query to execute
    params: dict[str, str]  # Query parameters
    limit: int = 100  # Maximum rows to return [≥1, ≤1000]
    timeout: float = 30.0  # Query timeout in seconds [>0]'''
```
</details>

❌ **Needs Work**

**doc(QueryRequest) as string (truncated)**

<details>
<summary>pformat(obj, max_string=100)</summary>

```python
'''class QueryRequest(BaseModel):
    """
    A database query request.

    Encapsulates query par'''+298
```
</details>

❌ **Needs Work**


## 11. Truncation Behavior


**Purpose:** Test truncation parameters (CRITICAL for token management).

**What to review:**
- max_string: String length limit
- max_length: Collection length limit
- max_depth: Nesting depth limit
- Ellipsis placement is clear


### 11.1 Long Strings

**long_string (default)**

<details>
<summary>pformat(instance)</summary>

```python
'This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. This is a very long string that should be truncated. '
```
</details>

❌ **Needs Work**

**long_string (max_string=50)**

<details>
<summary>pformat(obj, max_string=50)</summary>

```python
'This is a very long string that should be truncate'+2600
```
</details>

❌ **Needs Work**


### 11.2 Large Lists

**range(100) (default)**

<details>
<summary>pformat(instance)</summary>

```python
[
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    51,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
    59,
    60,
    61,
    62,
    63,
    64,
    65,
    66,
    67,
    68,
    69,
    70,
    71,
    72,
    73,
    74,
    75,
    76,
    77,
    78,
    79,
    80,
    81,
    82,
    83,
    84,
    85,
    86,
    87,
    88,
    89,
    90,
    91,
    92,
    93,
    94,
    95,
    96,
    97,
    98,
    99,
]
```
</details>

❌ **Needs Work**

**range(100) (max_length=5)**

<details>
<summary>pformat(obj, max_length=5)</summary>

```python
[0, 1, 2, 3, 4, ... +95]
```
</details>

❌ **Needs Work**


### 11.3 Large Dicts

**large_dict (max_length=5)**

<details>
<summary>pformat(obj, max_length=5)</summary>

```python
{'key_0': 'value_0', 'key_1': 'value_1', 'key_2': 'value_2', 'key_3': 'value_3', 'key_4': 'value_4', ... +45}
```
</details>

❌ **Needs Work**


### 11.4 Deeply Nested Structures

**nested (default)**

<details>
<summary>pformat(instance)</summary>

```python
{'level1': {'level2': {'level3': {'level4': {'level5': 'deep value'}}}}}
```
</details>

❌ **Needs Work**

**nested (max_depth=2)**

<details>
<summary>pformat(obj, max_depth=2)</summary>

```python
{'level1': {'level2': {dict: 1 items}}}
```
</details>

❌ **Needs Work**

**nested (max_depth=3)**

<details>
<summary>pformat(obj, max_depth=3)</summary>

```python
{'level1': {'level2': {'level3': {dict: 1 items}}}}
```
</details>

❌ **Needs Work**


### 11.5 Combined Truncation

**complex_data (multiple params)**

<details>
<summary>pformat(obj, max_length=3, max_string=50, max_depth=2)</summary>

```python
{
    'users': [
        QueryRequest(sql='SELECT 0', params={}, limit=100, timeout=30.0),
        QueryRequest(sql='SELECT 1', params={}, limit=100, timeout=30.0),
        QueryRequest(sql='SELECT 2', params={}, limit=100, timeout=30.0),
        ... +17
    ],
    'config': {'nested': {dict: 1 items}},
    'items': [0, 1, 2, ... +997],
}
```
</details>

❌ **Needs Work**


### 11.6 Instance with Large Nested Dict

**JsonHolder (default)**

<details>
<summary>📄 Instance Creation</summary>

```python
json_holder = JsonHolder()
json_holder.populate(depth=4, breadth=10)

```
</details>

<details>
<summary>pformat(instance)</summary>

```python
JsonHolder(data={
    'key_0': {
        'key_0': {
            'key_0': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_1': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_2': 'value_2',
            'key_3': 'value_3',
            'key_4': 'value_4',
            ... +5
        },
        'key_1': {
            'key_0': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_1': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_2': 'value_2',
            'key_3': 'value_3',
            'key_4': 'value_4',
            ... +5
        },
        'key_2': 'value_2',
        'key_3': 'value_3',
        'key_4': 'value_4',
        ... +5
    },
    'key_1': {
        'key_0': {
            'key_0': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_1': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_2': 'value_2',
            'key_3': 'value_3',
            'key_4': 'value_4',
            ... +5
        },
        'key_1': {
            'key_0': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_1': {
                'key_0': {'value': 'leaf_0'},
                'key_1': {'value': 'leaf_0'},
                'key_2': 'value_2',
                'key_3': 'value_3',
                'key_4': 'value_4',
                ... +5
            },
            'key_2': 'value_2',
            'key_3': 'value_3',
            'key_4': 'value_4',
            ... +5
        },
        'key_2': 'value_2',
        'key_3': 'value_3',
        'key_4': 'value_4',
        ... +5
    },
    'key_2': 'value_2',
    'key_3': 'value_3',
    'key_4': 'value_4',
    ... +5
}, metadata={'created': '2026-01-28', 'version': '1.0'})
```
</details>

❌ **Needs Work**

**JsonHolder (max_depth=2)**

<details>
<summary>pformat(obj, max_depth=2)</summary>

```python
JsonHolder(data={
    'key_0': {dict: 10 items},
    'key_1': {dict: 10 items},
    'key_2': 'value_2',
    'key_3': 'value_3',
    'key_4': 'value_4',
    ... +5
}, metadata={'created': '2026-01-28', 'version': '1.0'})
```
</details>

❌ **Needs Work**

**JsonHolder (max_depth=3, max_length=3)**

<details>
<summary>pformat(obj, max_depth=3, max_length=3)</summary>

```python
JsonHolder(data={
    'key_0': {
        'key_0': {dict: 10 items},
        'key_1': {dict: 10 items},
        'key_2': 'value_2',
        'key_3': 'value_3',
        'key_4': 'value_4',
        ... +5
    },
    'key_1': {
        'key_0': {dict: 10 items},
        'key_1': {dict: 10 items},
        'key_2': 'value_2',
        'key_3': 'value_3',
        'key_4': 'value_4',
        ... +5
    },
    'key_2': 'value_2',
    'key_3': 'value_3',
    'key_4': 'value_4',
    ... +5
}, metadata={'created': '2026-01-28', 'version': '1.0'})
```
</details>

❌ **Needs Work**


## 12. Edge Cases


**Purpose:** Test robustness with unusual inputs.


### 12.1 None and Empty Collections

**None**

<details>
<summary>pformat(instance)</summary>

```python
None
```
</details>

❌ **Needs Work**

**Empty list**

<details>
<summary>pformat(instance)</summary>

```python
[]
```
</details>

❌ **Needs Work**

**Empty dict**

<details>
<summary>pformat(instance)</summary>

```python
{}
```
</details>

❌ **Needs Work**

**Empty string**

<details>
<summary>pformat(instance)</summary>

```python
''
```
</details>

❌ **Needs Work**


### 12.2 Lambda

**lambda function**

<details>
<summary>📄 Source Code</summary>

```python
    fn = lambda x: x * 2  # noqa: E731

```
</details>

<details>
<summary>doc(method)</summary>

```python
def <lambda>(x):
    ...
```
</details>

❌ **Needs Work**


### 12.3 Built-in Types

**str (built-in)**

<details>
<summary>📄 Source Code</summary>

```python
# <class 'str'> is a built-in type or has no accessible source
```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class str:
    """
    str(object='') -> str
    str(bytes_or_buffer[, encoding[, errors]]) -> str

    Create a new string object from the given object. If encoding or
    errors is specified, then the object must expose a data buffer
    that will be decoded using the given encoding and error handler.
    Otherwise, returns the result of object.__str__() (if defined)
    or repr(object).
    encoding defaults to 'utf-8'.
    errors defaults to 'strict'.
    """

```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class str:
    """str(object='') -> str"""

```
</details>

❌ **Needs Work**

**list (built-in)**

<details>
<summary>📄 Source Code</summary>

```python
# <class 'list'> is a built-in type or has no accessible source
```
</details>

<details>
<summary>doc(Type) - Full</summary>

```python
class list:
    """
    Built-in mutable sequence.

    If no argument is given, the constructor creates a new empty list.
    The argument must be an iterable if specified.
    """

```
</details>

<details>
<summary>doc(Type, concise=True)</summary>

```python
class list:
    """Built-in mutable sequence."""

```
</details>

❌ **Needs Work**


### 12.4 Modules

**json module**

<details>
<summary>doc(instance)</summary>

```python
# json

"""
JSON (JavaScript Object Notation) <https://json.org> is a subset of
JavaScript syntax (ECMA-262 3rd edition) used as a lightweight data
interchange format.

:mod:`json` exposes an API familiar to users of the standard library
:mod:`marshal` and :mod:`pickle` modules.  It is derived from a
version of the externally maintained simplejson library.

Encoding basic Python object hierarchies::

    >>> import json
    >>> json.dumps(['foo', {'bar': ('baz', None, 1.0, 2)}])
    '["foo", {"bar": ["baz", null, 1.0, 2]}]'
    >>> print(json.dumps("\"foo\bar"))
    "\"foo\bar"
    >>> print(json.dumps('\u1234'))
    "\u1234"
    >>> print(json.dumps('\\'))
    "\\"
    >>> print(json.dumps({"c": 0, "b": 0, "a": 0}, sort_keys=True))
    {"a": 0, "b": 0, "c": 0}
    >>> from io import StringIO
    >>> io = StringIO()
    >>> json.dump(['streaming API'], io)
    >>> io.getvalue()
    '["streaming API"]'

Compact encoding::

    >>> import json
    >>> mydict = {'4': 5, '6': 7}
    >>> json.dumps([1,2,3,mydict], separators=(',', ':'))
    '[1,2,3,{"4":5,"6":7}]'

Pretty printing::

    >>> import json
    >>> print(json.dumps({'4': 5, '6': 7}, sort_keys=True, indent=4))
    {
        "4": 5,
        "6": 7
    }

Decoding JSON::

    >>> import json
    >>> obj = ['foo', {'bar': ['baz', None, 1.0, 2]}]
    >>> json.loads('["foo", {"bar":["baz", null, 1.0, 2]}]') == obj
    True
    >>> json.loads('"\\"foo\\bar"') == '"foo\x08ar'
    True
    >>> from io import StringIO
    >>> io = StringIO('["streaming API"]')
    >>> json.load(io)[0] == 'streaming API'
    True

Specializing JSON object decoding::

    >>> import json
    >>> def as_complex(dct):
    ...     if '__complex__' in dct:
    ...         return complex(dct['real'], dct['imag'])
    ...     return dct
    ...
    >>> json.loads('{"__complex__": true, "real": 1, "imag": 2}',
    ...     object_hook=as_complex)
    (1+2j)
    >>> from decimal import Decimal
    >>> json.loads('1.1', parse_float=Decimal) == Decimal('1.1')
    True

Specializing JSON object encoding::

    >>> import json
    >>> def encode_complex(obj):
    ...     if isinstance(obj, complex):
    ...         return [obj.real, obj.imag]
    ...     raise TypeError(f'Object of type {obj.__class__.__name__} '
    ...                     f'is not JSON serializable')
    ...
    >>> json.dumps(2 + 1j, default=encode_complex)
    '[2.0, 1.0]'
    >>> json.JSONEncoder(default=encode_complex).encode(2 + 1j)
    '[2.0, 1.0]'
    >>> ''.join(json.JSONEncoder(default=encode_complex).iterencode(2 + 1j))
    '[2.0, 1.0]'


Using json.tool from the shell to validate and pretty-print::

    $ echo '{"json":"obj"}' | python -m json.tool
    {
        "json": "obj"
    }
    $ echo '{ 1.2:3.4}' | python -m json.tool
    Expecting property name enclosed in double quotes: line 1 column 3 (char 2)
"""

def detect_encoding(b):
    ...

def dump(obj, fp, skipkeys = False, ensure_ascii = True, check_circular = True, allow_nan = True, cls = None, indent = None, separators = None, default = None, sort_keys = False, kw):
    """
    Serialize ``obj`` as a JSON formatted stream to ``fp`` (a
    ``.write()``-supporting file-like object).

    If ``skipkeys`` is true then ``dict`` keys that are not basic types
    (``str``, ``int``, ``float``, ``bool``, ``None``) will be skipped
    instead of raising a ``TypeError``.

    If ``ensure_ascii`` is false, then the strings written to ``fp`` can
    contain non-ASCII characters if they appear in strings contained in
    ``obj``. Otherwise, all such characters are escaped in JSON strings.

    If ``check_circular`` is false, then the circular reference check
    for container types will be skipped and a circular reference will
    result in an ``RecursionError`` (or worse).

    If ``allow_nan`` is false, then it will be a ``ValueError`` to
    serialize out of range ``float`` values (``nan``, ``inf``, ``-inf``)
    in strict compliance of the JSON specification, instead of using the
    JavaScript equivalents (``NaN``, ``Infinity``, ``-Infinity``).

    If ``indent`` is a non-negative integer, then JSON array elements and
    object members will be pretty-printed with that indent level. An indent
    level of 0 will only insert newlines. ``None`` is the most compact
    representation.

    If specified, ``separators`` should be an ``(item_separator, key_separator)``
    tuple.  The default is ``(', ', ': ')`` if *indent* is ``None`` and
    ``(',', ': ')`` otherwise.  To get the most compact JSON representation,
    you should specify ``(',', ':')`` to eliminate whitespace.

    ``default(obj)`` is a function that should return a serializable version
    of obj or raise TypeError. The default simply raises TypeError.

    If *sort_keys* is true (default: ``False``), then the output of
    dictionaries will be sorted by key.

    To use a custom ``JSONEncoder`` subclass (e.g. one that overrides the
    ``.default()`` method to serialize additional types), specify it with
    the ``cls`` kwarg; otherwise ``JSONEncoder`` is used.
    """

def dumps(obj, skipkeys = False, ensure_ascii = True, check_circular = True, allow_nan = True, cls = None, indent = None, separators = None, default = None, sort_keys = False, kw):
    """
    Serialize ``obj`` to a JSON formatted ``str``.

    If ``skipkeys`` is true then ``dict`` keys that are not basic types
    (``str``, ``int``, ``float``, ``bool``, ``None``) will be skipped
    instead of raising a ``TypeError``.

    If ``ensure_ascii`` is false, then the return value can contain non-ASCII
    characters if they appear in strings contained in ``obj``. Otherwise, all
    such characters are escaped in JSON strings.

    If ``check_circular`` is false, then the circular reference check
    for container types will be skipped and a circular reference will
    result in an ``RecursionError`` (or worse).

    If ``allow_nan`` is false, then it will be a ``ValueError`` to
    serialize out of range ``float`` values (``nan``, ``inf``, ``-inf``) in
    strict compliance of the JSON specification, instead of using the
    JavaScript equivalents (``NaN``, ``Infinity``, ``-Infinity``).

    If ``indent`` is a non-negative integer, then JSON array elements and
    object members will be pretty-printed with that indent level. An indent
    level of 0 will only insert newlines. ``None`` is the most compact
    representation.

    If specified, ``separators`` should be an ``(item_separator, key_separator)``
    tuple.  The default is ``(', ', ': ')`` if *indent* is ``None`` and
    ``(',', ': ')`` otherwise.  To get the most compact JSON representation,
    you should specify ``(',', ':')`` to eliminate whitespace.

    ``default(obj)`` is a function that should return a serializable version
    of obj or raise TypeError. The default simply raises TypeError.

    If *sort_keys* is true (default: ``False``), then the output of
    dictionaries will be sorted by key.

    To use a custom ``JSONEncoder`` subclass (e.g. one that overrides the
    ``.default()`` method to serialize additional types), specify it with
    the ``cls`` kwarg; otherwise ``JSONEncoder`` is used.
    """

def load(fp, cls = None, object_hook = None, parse_float = None, parse_int = None, parse_constant = None, object_pairs_hook = None, kw):
    """
    Deserialize ``fp`` (a ``.read()``-supporting file-like object containing
    a JSON document) to a Python object.

    ``object_hook`` is an optional function that will be called with the
    result of any object literal decode (a ``dict``). The return value of
    ``object_hook`` will be used instead of the ``dict``. This feature
    can be used to implement custom decoders (e.g. JSON-RPC class hinting).

    ``object_pairs_hook`` is an optional function that will be called with the
    result of any object literal decoded with an ordered list of pairs.  The
    return value of ``object_pairs_hook`` will be used instead of the ``dict``.
    This feature can be used to implement custom decoders.  If ``object_hook``
    is also defined, the ``object_pairs_hook`` takes priority.

    To use a custom ``JSONDecoder`` subclass, specify it with the ``cls``
    kwarg; otherwise ``JSONDecoder`` is used.
    """

def loads(s, cls = None, object_hook = None, parse_float = None, parse_int = None, parse_constant = None, object_pairs_hook = None, kw):
    """
    Deserialize ``s`` (a ``str``, ``bytes`` or ``bytearray`` instance
    containing a JSON document) to a Python object.

    ``object_hook`` is an optional function that will be called with the
    result of any object literal decode (a ``dict``). The return value of
    ``object_hook`` will be used instead of the ``dict``. This feature
    can be used to implement custom decoders (e.g. JSON-RPC class hinting).

    ``object_pairs_hook`` is an optional function that will be called with the
    result of any object literal decoded with an ordered list of pairs.  The
    return value of ``object_pairs_hook`` will be used instead of the ``dict``.
    This feature can be used to implement custom decoders.  If ``object_hook``
    is also defined, the ``object_pairs_hook`` takes priority.

    ``parse_float``, if specified, will be called with the string
    of every JSON float to be decoded. By default this is equivalent to
    float(num_str). This can be used to use another datatype or parser
    for JSON floats (e.g. decimal.Decimal).

    ``parse_int``, if specified, will be called with the string
    of every JSON int to be decoded. By default this is equivalent to
    int(num_str). This can be used to use another datatype or parser
    for JSON integers (e.g. float).

    ``parse_constant``, if specified, will be called with one of the
    following strings: -Infinity, Infinity, NaN.
    This can be used to raise an exception if invalid JSON numbers
    are encountered.

    To use a custom ``JSONDecoder`` subclass, specify it with the ``cls``
    kwarg; otherwise ``JSONDecoder`` is used.
    """
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
# json

"""
JSON (JavaScript Object Notation) <https://json.org> is a subset of
JavaScript syntax (ECMA-262 3rd edition) used as a lightweight data
interchange format.

:mod:`json` exposes an API familiar to users of the standard library
:mod:`marshal` and :mod:`pickle` modules.  It is derived from a
version of the externally maintained simplejson library.

Encoding basic Python object hierarchies::

    >>> import json
    >>> json.dumps(['foo', {'bar': ('baz', None, 1.0, 2)}])
    '["foo", {"bar": ["baz", null, 1.0, 2]}]'
    >>> print(json.dumps("\"foo\bar"))
    "\"foo\bar"
    >>> print(json.dumps('\u1234'))
    "\u1234"
    >>> print(json.dumps('\\'))
    "\\"
    >>> print(json.dumps({"c": 0, "b": 0, "a": 0}, sort_keys=True))
    {"a": 0, "b": 0, "c": 0}
    >>> from io import StringIO
    >>> io = StringIO()
    >>> json.dump(['streaming API'], io)
    >>> io.getvalue()
    '["streaming API"]'

Compact encoding::

    >>> import json
    >>> mydict = {'4': 5, '6': 7}
    >>> json.dumps([1,2,3,mydict], separators=(',', ':'))
    '[1,2,3,{"4":5,"6":7}]'

Pretty printing::

    >>> import json
    >>> print(json.dumps({'4': 5, '6': 7}, sort_keys=True, indent=4))
    {
        "4": 5,
        "6": 7
    }

Decoding JSON::

    >>> import json
    >>> obj = ['foo', {'bar': ['baz', None, 1.0, 2]}]
    >>> json.loads('["foo", {"bar":["baz", null, 1.0, 2]}]') == obj
    True
    >>> json.loads('"\\"foo\\bar"') == '"foo\x08ar'
    True
    >>> from io import StringIO
    >>> io = StringIO('["streaming API"]')
    >>> json.load(io)[0] == 'streaming API'
    True

Specializing JSON object decoding::

    >>> import json
    >>> def as_complex(dct):
    ...     if '__complex__' in dct:
    ...         return complex(dct['real'], dct['imag'])
    ...     return dct
    ...
    >>> json.loads('{"__complex__": true, "real": 1, "imag": 2}',
    ...     object_hook=as_complex)
    (1+2j)
    >>> from decimal import Decimal
    >>> json.loads('1.1', parse_float=Decimal) == Decimal('1.1')
    True

Specializing JSON object encoding::

    >>> import json
    >>> def encode_complex(obj):
    ...     if isinstance(obj, complex):
    ...         return [obj.real, obj.imag]
    ...     raise TypeError(f'Object of type {obj.__class__.__name__} '
    ...                     f'is not JSON serializable')
    ...
    >>> json.dumps(2 + 1j, default=encode_complex)
    '[2.0, 1.0]'
    >>> json.JSONEncoder(default=encode_complex).encode(2 + 1j)
    '[2.0, 1.0]'
    >>> ''.join(json.JSONEncoder(default=encode_complex).iterencode(2 + 1j))
    '[2.0, 1.0]'


Using json.tool from the shell to validate and pretty-print::

    $ echo '{"json":"obj"}' | python -m json.tool
    {
        "json": "obj"
    }
    $ echo '{ 1.2:3.4}' | python -m json.tool
    Expecting property name enclosed in double quotes: line 1 column 3 (char 2)
"""

def detect_encoding(b):
    ...

def dump(obj, fp, skipkeys = False, ensure_ascii = True, check_circular = True, allow_nan = True, cls = None, indent = None, separators = None, default = None, sort_keys = False, kw):
    """
    Serialize ``obj`` as a JSON formatted stream to ``fp`` (a
    ``.write()``-supporting file-like object).

    If ``skipkeys`` is true then ``dict`` keys that are not basic types
    (``str``, ``int``, ``float``, ``bool``, ``None``) will be skipped
    instead of raising a ``TypeError``.

    If ``ensure_ascii`` is false, then the strings written to ``fp`` can
    contain non-ASCII characters if they appear in strings contained in
    ``obj``. Otherwise, all such characters are escaped in JSON strings.

    If ``check_circular`` is false, then the circular reference check
    for container types will be skipped and a circular reference will
    result in an ``RecursionError`` (or worse).

    If ``allow_nan`` is false, then it will be a ``ValueError`` to
    serialize out of range ``float`` values (``nan``, ``inf``, ``-inf``)
    in strict compliance of the JSON specification, instead of using the
    JavaScript equivalents (``NaN``, ``Infinity``, ``-Infinity``).

    If ``indent`` is a non-negative integer, then JSON array elements and
    object members will be pretty-printed with that indent level. An indent
    level of 0 will only insert newlines. ``None`` is the most compact
    representation.

    If specified, ``separators`` should be an ``(item_separator, key_separator)``
    tuple.  The default is ``(', ', ': ')`` if *indent* is ``None`` and
    ``(',', ': ')`` otherwise.  To get the most compact JSON representation,
    you should specify ``(',', ':')`` to eliminate whitespace.

    ``default(obj)`` is a function that should return a serializable version
    of obj or raise TypeError. The default simply raises TypeError.

    If *sort_keys* is true (default: ``False``), then the output of
    dictionaries will be sorted by key.

    To use a custom ``JSONEncoder`` subclass (e.g. one that overrides the
    ``.default()`` method to serialize additional types), specify it with
    the ``cls`` kwarg; otherwise ``JSONEncoder`` is used.
    """

def dumps(obj, skipkeys = False, ensure_ascii = True, check_circular = True, allow_nan = True, cls = None, indent = None, separators = None, default = None, sort_keys = False, kw):
    """
    Serialize ``obj`` to a JSON formatted ``str``.

    If ``skipkeys`` is true then ``dict`` keys that are not basic types
    (``str``, ``int``, ``float``, ``bool``, ``None``) will be skipped
    instead of raising a ``TypeError``.

    If ``ensure_ascii`` is false, then the return value can contain non-ASCII
    characters if they appear in strings contained in ``obj``. Otherwise, all
    such characters are escaped in JSON strings.

    If ``check_circular`` is false, then the circular reference check
    for container types will be skipped and a circular reference will
    result in an ``RecursionError`` (or worse).

    If ``allow_nan`` is false, then it will be a ``ValueError`` to
    serialize out of range ``float`` values (``nan``, ``inf``, ``-inf``) in
    strict compliance of the JSON specification, instead of using the
    JavaScript equivalents (``NaN``, ``Infinity``, ``-Infinity``).

    If ``indent`` is a non-negative integer, then JSON array elements and
    object members will be pretty-printed with that indent level. An indent
    level of 0 will only insert newlines. ``None`` is the most compact
    representation.

    If specified, ``separators`` should be an ``(item_separator, key_separator)``
    tuple.  The default is ``(', ', ': ')`` if *indent* is ``None`` and
    ``(',', ': ')`` otherwise.  To get the most compact JSON representation,
    you should specify ``(',', ':')`` to eliminate whitespace.

    ``default(obj)`` is a function that should return a serializable version
    of obj or raise TypeError. The default simply raises TypeError.

    If *sort_keys* is true (default: ``False``), then the output of
    dictionaries will be sorted by key.

    To use a custom ``JSONEncoder`` subclass (e.g. one that overrides the
    ``.default()`` method to serialize additional types), specify it with
    the ``cls`` kwarg; otherwise ``JSONEncoder`` is used.
    """

def load(fp, cls = None, object_hook = None, parse_float = None, parse_int = None, parse_constant = None, object_pairs_hook = None, kw):
    """
    Deserialize ``fp`` (a ``.read()``-supporting file-like object containing
    a JSON document) to a Python object.

    ``object_hook`` is an optional function that will be called with the
    result of any object literal decode (a ``dict``). The return value of
    ``object_hook`` will be used instead of the ``dict``. This feature
    can be used to implement custom decoders (e.g. JSON-RPC class hinting).

    ``object_pairs_hook`` is an optional function that will be called with the
    result of any object literal decoded with an ordered list of pairs.  The
    return value of ``object_pairs_hook`` will be used instead of the ``dict``.
    This feature can be used to implement custom decoders.  If ``object_hook``
    is also defined, the ``object_pairs_hook`` takes priority.

    To use a custom ``JSONDecoder`` subclass, specify it with the ``cls``
    kwarg; otherwise ``JSONDecoder`` is used.
    """

def loads(s, cls = None, object_hook = None, parse_float = None, parse_int = None, parse_constant = None, object_pairs_hook = None, kw):
    """
    Deserialize ``s`` (a ``str``, ``bytes`` or ``bytearray`` instance
    containing a JSON document) to a Python object.

    ``object_hook`` is an optional function that will be called with the
    result of any object literal decode (a ``dict``). The return value of
    ``object_hook`` will be used instead of the ``dict``. This feature
    can be used to implement custom decoders (e.g. JSON-RPC class hinting).

    ``object_pairs_hook`` is an optional function that will be called with the
    result of any object literal decoded with an ordered list of pairs.  The
    return value of ``object_pairs_hook`` will be used instead of the ``dict``.
    This feature can be used to implement custom decoders.  If ``object_hook``
    is also defined, the ``object_pairs_hook`` takes priority.

    ``parse_float``, if specified, will be called with the string
    of every JSON float to be decoded. By default this is equivalent to
    float(num_str). This can be used to use another datatype or parser
    for JSON floats (e.g. decimal.Decimal).

    ``parse_int``, if specified, will be called with the string
    of every JSON int to be decoded. By default this is equivalent to
    int(num_str). This can be used to use another datatype or parser
    for JSON integers (e.g. float).

    ``parse_constant``, if specified, will be called with one of the
    following strings: -Infinity, Infinity, NaN.
    This can be used to raise an exception if invalid JSON numbers
    are encountered.

    To use a custom ``JSONDecoder`` subclass, specify it with the ``cls``
    kwarg; otherwise ``JSONDecoder`` is used.
    """
```
</details>

❌ **Needs Work**

**agentdoc module**

<details>
<summary>doc(instance)</summary>

```python
# agentdoc

"""
agentdoc - Python introspection for LLM agents.

Token-efficient, structured documentation of Python objects at runtime.

## Quick Start

```python
from agentdoc import doc, pprint

# Document a class or function
print(doc(MyClass))
print(doc(my_function))

# Document multiple types together (deduplicated)
print(doc(Order, Invoice, Product))

# Control referenced type depth
print(doc(Order, type_depth=2))  # Transitive references
print(doc(Order, type_depth=0))  # No references

# Pretty-print values with truncation
pprint(data, max_length=10, max_string=100)
```

## Core API

- `doc(*objs, concise=False, type_depth=None)` - Generate documentation for one or more objects
- `pformat(obj, ...)` - Format object as string with truncation
- `pprint(obj, ...)` - Print formatted object

## Info Types

For programmatic introspection, use the Info dataclasses:

- `TypeInfo` - Structured representation of a class
- `CallableInfo` - Structured representation of a function/method
- `ModuleInfo` - Structured representation of a module
- `FieldInfo` - Structured representation of a field/attribute

Extract with:
- `extract_info(obj)` - Returns appropriate Info type
- `extract_type_info(cls)` - Returns TypeInfo
- `extract_callable_info(func)` - Returns CallableInfo
- `extract_module_info(mod)` - Returns ModuleInfo

## Customization Protocols

Classes can customize their representation via:
- `__type_info__(cls) -> TypeInfo` - Override type extraction
- `__instance_values__(self) -> dict` - Override instance value extraction
- `__callable_info__() -> CallableInfo` - Override callable extraction
"""

def pformat(obj, console: Any = None, indent_guides: bool = True, max_length: int | None = None, max_string: int | None = None, max_depth: int | None = None, expand_all: bool = False, concise: bool = False, instance_mode: str = 'repr') -> str:
    """
    Format object as string with smart truncation.

    This function is API-compatible with rich.pretty.pformat() for easy migration.
    The ``console`` and ``indent_guides`` parameters are accepted for compatibility
    but have no effect (agentdoc formats plain text without Rich console features).

    Args:
        obj: Object to format.
        console: Ignored. Accepted for Rich API compatibility.
        indent_guides: Ignored. Accepted for Rich API compatibility.
        max_length: Max elements per container (None=unlimited).
        max_string: Max string chars (None=unlimited).
        max_depth: Max nesting depth (None=unlimited).
        expand_all: If True, always expand containers to multiple lines.
        concise: If True, show first-line docstrings only (agentdoc-specific).
        instance_mode: How to format instances - "repr" for repr-style, "type" for type structure.

    Returns:
        Formatted string.
    """

def pprint(obj, console: Any = None, indent_guides: bool = True, max_length: int | None = None, max_string: int | None = None, max_depth: int | None = None, expand_all: bool = False, concise: bool = False):
    """
    Pretty print an object with smart truncation.

    This function is API-compatible with rich.pretty.pprint() for easy migration.
    The ``console`` and ``indent_guides`` parameters are accepted for compatibility
    but have no effect (agentdoc always prints to stdout without guide lines).

    Works on all Python objects:
    - Types (classes): Shows Python class syntax with fields and methods
    - Functions/methods: Shows signature and docstring
    - Modules: Shows docstring and public functions
    - Values: Shows truncated value representation (Rich-style)
    - Instances: Shows repr-style with current values (use doc() for type structure)

    Args:
        obj: Object to print.
        console: Ignored. Accepted for Rich API compatibility.
        indent_guides: Ignored. Accepted for Rich API compatibility.
        max_length: Max elements per container (None=unlimited).
        max_string: Max string chars (None=unlimited).
        max_depth: Max nesting depth (None=unlimited).
        expand_all: If True, always expand containers to multiple lines.
        concise: If True, show first-line docstrings only (agentdoc-specific).
    """
```
</details>

<details>
<summary>pformat(instance)</summary>

```python
# agentdoc

"""
agentdoc - Python introspection for LLM agents.

Token-efficient, structured documentation of Python objects at runtime.

## Quick Start

```python
from agentdoc import doc, pprint

# Document a class or function
print(doc(MyClass))
print(doc(my_function))

# Document multiple types together (deduplicated)
print(doc(Order, Invoice, Product))

# Control referenced type depth
print(doc(Order, type_depth=2))  # Transitive references
print(doc(Order, type_depth=0))  # No references

# Pretty-print values with truncation
pprint(data, max_length=10, max_string=100)
```

## Core API

- `doc(*objs, concise=False, type_depth=None)` - Generate documentation for one or more objects
- `pformat(obj, ...)` - Format object as string with truncation
- `pprint(obj, ...)` - Print formatted object

## Info Types

For programmatic introspection, use the Info dataclasses:

- `TypeInfo` - Structured representation of a class
- `CallableInfo` - Structured representation of a function/method
- `ModuleInfo` - Structured representation of a module
- `FieldInfo` - Structured representation of a field/attribute

Extract with:
- `extract_info(obj)` - Returns appropriate Info type
- `extract_type_info(cls)` - Returns TypeInfo
- `extract_callable_info(func)` - Returns CallableInfo
- `extract_module_info(mod)` - Returns ModuleInfo

## Customization Protocols

Classes can customize their representation via:
- `__type_info__(cls) -> TypeInfo` - Override type extraction
- `__instance_values__(self) -> dict` - Override instance value extraction
- `__callable_info__() -> CallableInfo` - Override callable extraction
"""

def pformat(obj, console: Any = None, indent_guides: bool = True, max_length: int | None = None, max_string: int | None = None, max_depth: int | None = None, expand_all: bool = False, concise: bool = False, instance_mode: str = 'repr') -> str:
    """
    Format object as string with smart truncation.

    This function is API-compatible with rich.pretty.pformat() for easy migration.
    The ``console`` and ``indent_guides`` parameters are accepted for compatibility
    but have no effect (agentdoc formats plain text without Rich console features).

    Args:
        obj: Object to format.
        console: Ignored. Accepted for Rich API compatibility.
        indent_guides: Ignored. Accepted for Rich API compatibility.
        max_length: Max elements per container (None=unlimited).
        max_string: Max string chars (None=unlimited).
        max_depth: Max nesting depth (None=unlimited).
        expand_all: If True, always expand containers to multiple lines.
        concise: If True, show first-line docstrings only (agentdoc-specific).
        instance_mode: How to format instances - "repr" for repr-style, "type" for type structure.

    Returns:
        Formatted string.
    """

def pprint(obj, console: Any = None, indent_guides: bool = True, max_length: int | None = None, max_string: int | None = None, max_depth: int | None = None, expand_all: bool = False, concise: bool = False):
    """
    Pretty print an object with smart truncation.

    This function is API-compatible with rich.pretty.pprint() for easy migration.
    The ``console`` and ``indent_guides`` parameters are accepted for compatibility
    but have no effect (agentdoc always prints to stdout without guide lines).

    Works on all Python objects:
    - Types (classes): Shows Python class syntax with fields and methods
    - Functions/methods: Shows signature and docstring
    - Modules: Shows docstring and public functions
    - Values: Shows truncated value representation (Rich-style)
    - Instances: Shows repr-style with current values (use doc() for type structure)

    Args:
        obj: Object to print.
        console: Ignored. Accepted for Rich API compatibility.
        indent_guides: Ignored. Accepted for Rich API compatibility.
        max_length: Max elements per container (None=unlimited).
        max_string: Max string chars (None=unlimited).
        max_depth: Max nesting depth (None=unlimited).
        expand_all: If True, always expand containers to multiple lines.
        concise: If True, show first-line docstrings only (agentdoc-specific).
    """
```
</details>

❌ **Needs Work**


---

*End of agentdoc output review*
