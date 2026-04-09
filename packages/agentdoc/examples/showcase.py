# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""agentdoc showcase — every feature on one page.

Run with:
    uv run python packages/agentdoc/examples/showcase.py

Each section is separated by a header so you can read the output top-to-bottom
and understand what each part of the API produces.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path, PurePosixPath
from typing import Annotated

import agentdoc
from agentdoc import doc, hidden, pformat, spec
from agentdoc.ext import FieldInfo, TypeInfo
from agentdoc.introspect import methods, variables
from agentdoc.visibility import is_hidden_field

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def section(title: str) -> None:
    width = 72
    print()
    print("─" * width)
    print(f"  {title}")
    print("─" * width)


def show_output(label: str, value: str) -> None:
    print(f"\n[{label}]")
    print(value)


# ══════════════════════════════════════════════════════════════════════════════
# 0. doc(agentdoc) — the library documents itself
# ══════════════════════════════════════════════════════════════════════════════

section("0. doc(agentdoc) — the library documents itself")

show_output("doc(agentdoc, concise=True)", doc(agentdoc, concise=True))
print("  → complete first-level API reference in declaration order")

# ══════════════════════════════════════════════════════════════════════════════
# 1. doc() on a plain class
# ══════════════════════════════════════════════════════════════════════════════

section("1. doc() on a plain class")


class DatabaseConfig:
    """Connection settings for the database.

    Passed to the agent at construction time; do not modify at runtime.
    """

    host: str = "localhost"
    port: int = 5432
    database: str = "mydb"
    max_connections: int = 10

    def ping(self) -> bool:
        """Check if the database is reachable."""
        return True

    def execute(self, query: str) -> list[dict]:
        """Run a SQL query and return rows."""
        return []


show_output("doc(DatabaseConfig)", doc(DatabaseConfig))

# ══════════════════════════════════════════════════════════════════════════════
# 2. doc() on a function / async method
# ══════════════════════════════════════════════════════════════════════════════

section("2. doc() on a standalone function")


def search(query: str, *, limit: int = 10, offset: int = 0) -> list[str]:
    """Search the index for *query*.

    Returns up to *limit* results starting at *offset*.
    """
    return []


show_output("doc(search)", doc(search))

# ══════════════════════════════════════════════════════════════════════════════
# 3. Annotated[T, "description"] — self-documenting params and fields
# ══════════════════════════════════════════════════════════════════════════════

section("3. Annotated[T, 'description'] on function params and dataclass fields")


@dataclasses.dataclass
class Embedding:
    """A vector embedding with metadata."""

    vector: Annotated[list[float], "The raw embedding values"]
    model: Annotated[str, "Model that produced this embedding"] = "text-embedding-3-small"
    dims: Annotated[int, "Embedding dimensionality"] = 1536
    truncated: Annotated[bool, "True if the input was truncated before embedding"] = False


def embed(
    text: Annotated[str, "Input text to embed"],
    model: Annotated[str, "Embedding model name"] = "text-embedding-3-small",
    truncate: Annotated[bool, "Truncate input to model's max token limit"] = True,
) -> Embedding:
    """Embed *text* using the specified model."""
    return Embedding(vector=[0.1, 0.2], model=model)


show_output("doc(Embedding)", doc(Embedding))
show_output("doc(embed)", doc(embed))
print("  → Annotated descriptions appear as # comments (dataclass) or Args section (function)")

# ══════════════════════════════════════════════════════════════════════════════
# 4. pformat() — current instance state
# ══════════════════════════════════════════════════════════════════════════════

section("4. pformat() — current instance state")


class TaskAgent:
    """Agent that tracks a list of tasks."""

    task_count: int
    completed: int
    label: str

    def __init__(self, label: str = "default") -> None:
        self.task_count = 0
        self.completed = 0
        self.label = label


agent = TaskAgent(label="research")
agent.task_count = 5
agent.completed = 2

show_output("pformat(agent)", pformat(agent))
show_output("doc(agent)", doc(agent))

# ══════════════════════════════════════════════════════════════════════════════
# 5. pformat() with rich field values — lists, dicts, None, booleans
# ══════════════════════════════════════════════════════════════════════════════

section("5. pformat() with rich field values")


class SearchState:
    """Tracks the state of an ongoing search session."""

    query: str
    results: list[str]
    filters: dict[str, str]
    page: int
    exhausted: bool
    error: str | None

    def __init__(self) -> None:
        self.query = "agentdoc"
        self.results = ["result_a", "result_b", "result_c"]
        self.filters = {"lang": "python", "min_stars": "100"}
        self.page = 2
        self.exhausted = False
        self.error = None


state = SearchState()
show_output("pformat(state)", pformat(state))
show_output("doc(state)", doc(state))

# Mutate and format again — pformat() reflects current values
state.results = ["x"] * 50
state.error = "Rate limited"
show_output("pformat(state) after mutation", pformat(state))
print("  → pformat() reflects current values; None renders as None")

# ══════════════════════════════════════════════════════════════════════════════
# 6. doc(instance) with a dataclass
# ══════════════════════════════════════════════════════════════════════════════

section("6. doc(instance) with a dataclass")


@dataclasses.dataclass
class ModelConfig:
    """Parameters for a language model call."""

    model: Annotated[str, "Model ID"] = "gpt-4o"
    temperature: Annotated[float, "Sampling temperature 0–2"] = 0.7
    max_tokens: Annotated[int | None, "Token budget; None = model default"] = None
    stop: Annotated[list[str], "Stop sequences"] = dataclasses.field(default_factory=list)


cfg = ModelConfig(model="claude-opus-4-6", temperature=0.2, max_tokens=4096)
show_output("doc(ModelConfig)", doc(ModelConfig))
show_output("doc(cfg)", doc(cfg))
show_output("pformat(cfg)", pformat(cfg))
print("  → doc(instance) shows current values; pformat shows repr-style; both strip Annotated")

# ══════════════════════════════════════════════════════════════════════════════
# 7. doc() with expand=False sub-objects
# ══════════════════════════════════════════════════════════════════════════════

section("7. doc() with expand=False sub-objects")


class _WritingSkill:
    """Write structured content to files and buffers."""

    def write_file(self, path: Path, content: str) -> None:
        """Write *content* to *path*."""

    def append_file(self, path: Path, content: str) -> None:
        """Append *content* to *path*."""


_writing_ann = spec(expand=False)
assert _writing_ann is not None
_writing_ann(_WritingSkill)


class ResearchAgent:
    """Agent that researches topics and writes reports."""

    writing: _WritingSkill = _WritingSkill()  # type: ignore[assignment]
    max_depth: int = 3

    def research(self, topic: str) -> str:
        """Investigate *topic* and return a summary."""
        return ""


show_output("doc(ResearchAgent())", doc(ResearchAgent()))
show_output("pformat(ResearchAgent())", pformat(ResearchAgent()))
print("  → writing appears as one-liner with expand=False")

# ══════════════════════════════════════════════════════════════════════════════
# 8. @hidden decorator — method hidden from doc()
# ══════════════════════════════════════════════════════════════════════════════

section("8. @hidden decorator on a method")


class SecureAgent:
    """Agent with some internal methods excluded from documentation."""

    api_key: Annotated[str, hidden] = ""  # hidden field
    endpoint: str = "https://api.example.com"

    def query(self, prompt: str) -> str:
        """Send *prompt* to the endpoint and return the response."""
        return ""

    @hidden
    def _refresh_token(self) -> None:
        """Internal token refresh — not part of the documented interface."""

    @hidden
    def _audit_log(self, event: str) -> None:
        """Write to the internal audit log."""


show_output("doc(SecureAgent())", doc(SecureAgent()))
print("  → api_key and _refresh_token/_audit_log should NOT appear above")

# ══════════════════════════════════════════════════════════════════════════════
# 9. Annotated[T, hidden] on fields
# ══════════════════════════════════════════════════════════════════════════════

section("9. Annotated[T, hidden] on class fields")


class UserStore:
    """Manages user records.

    The _password_hash field is excluded from documentation so credentials are not exposed.
    """

    username: str = ""
    email: str = ""
    _password_hash: Annotated[str, hidden] = ""
    _session_token: Annotated[str, hidden] = ""
    role: str = "user"


show_output("doc(UserStore)", doc(UserStore))
print("  → _password_hash and _session_token should NOT appear above")

# ══════════════════════════════════════════════════════════════════════════════
# 10. with hidden: context manager — hide imports
# ══════════════════════════════════════════════════════════════════════════════

section("10. with hidden: context manager (hides names defined in block)")

with hidden:
    import hashlib as _hashlib  # noqa: F401 — excluded from documentation

SALT_ROUNDS = 12  # visible

print("  → _hashlib is hidden from doc() output; SALT_ROUNDS is visible")

# ══════════════════════════════════════════════════════════════════════════════
# 11. @spec(expand=False) — collapsed sub-object in doc()
# ══════════════════════════════════════════════════════════════════════════════

section("11. @spec(expand=False) — collapsed field type")

show_output("doc(ResearchAgent())", doc(ResearchAgent()))
print("  → writing should appear as a one-liner with docstring comment, not expanded")

# ══════════════════════════════════════════════════════════════════════════════
# 12. @spec(description=...) — inline description on a field
# ══════════════════════════════════════════════════════════════════════════════

section("12. spec() field description via imperative form")


class Pipeline:
    """Data processing pipeline."""

    input_path: Path = Path("data/in")
    output_path: Path = Path("data/out")
    batch_size: int = 64
    dry_run: bool = False


spec(Pipeline, "batch_size", description="Number of records to process per batch")
spec(Pipeline, "dry_run", description="If True, process but do not write output")

show_output("doc(Pipeline)", doc(Pipeline))
print("  → batch_size and dry_run should have # comments above")

# ══════════════════════════════════════════════════════════════════════════════
# 13. spec() as Annotated marker
# ══════════════════════════════════════════════════════════════════════════════

section("13. spec() as Annotated marker")


class SearchConfig:
    """Configuration for the search subsystem."""

    query: Annotated[str, spec(description="The search query string")] = ""
    limit: Annotated[int, spec(description="Max results to return")] = 10
    rerank: Annotated[bool, spec(description="Apply reranking model")] = False
    _cache: Annotated[dict, hidden] = {}  # type: ignore[assignment]


show_output("doc(SearchConfig)", doc(SearchConfig))
print("  → each field should show its description as a # comment")

# ══════════════════════════════════════════════════════════════════════════════
# 14. Subclass unhiding
# ══════════════════════════════════════════════════════════════════════════════

section("14. Subclass unhiding a parent's hidden field")


class BaseAgent:
    """Base class — credentials excluded from documentation by default."""

    llm_client: Annotated[object, hidden] = None  # type: ignore[assignment]
    system_prompt: str = "You are a helpful assistant."


class MyAgent(BaseAgent):
    """Subclass that exposes llm_client for inspection."""

    llm_client: object = None  # re-declared without hidden


show_output("doc(BaseAgent())", doc(BaseAgent()))
show_output("doc(MyAgent())", doc(MyAgent()))
print("  → llm_client absent in BaseAgent, present in MyAgent")

# ══════════════════════════════════════════════════════════════════════════════
# 15. spec(method, hidden=False) — opt-in dunders
# ══════════════════════════════════════════════════════════════════════════════

section("15. spec(method, hidden=False) — opt-in __init__ and other dunders")


class Vector:
    """A 2D vector."""

    @spec(hidden=False)  # type: ignore[misc]
    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        """Create a vector."""
        self.x = x
        self.y = y

    @spec(hidden=False)  # type: ignore[misc]
    def __add__(self, other: Vector) -> Vector:
        """Vector addition."""
        return Vector(self.x + other.x, self.y + other.y)

    @spec(hidden=False)  # type: ignore[misc]
    def __repr__(self) -> str:
        """Human-readable repr."""
        return f"Vector({self.x}, {self.y})"

    def magnitude(self) -> float:
        """Length of the vector."""
        return (self.x**2 + self.y**2) ** 0.5


show_output("doc(Vector)", doc(Vector))
print("  → __init__, __add__, __repr__ appear because of @spec(hidden=False)")
print("  → __len__, __eq__ etc. remain hidden (not opted in)")


# Imperative form on an external class
class Wrapper:
    """Thin wrapper around a string."""

    def __init__(self, value: str = "") -> None:
        """Wrap a string."""
        self.value = value

    def upper(self) -> str:
        """Return the value uppercased."""
        return self.value.upper()


spec(Wrapper.__init__, hidden=False)
show_output("doc(Wrapper) after spec(Wrapper.__init__, hidden=False)", doc(Wrapper))

# ══════════════════════════════════════════════════════════════════════════════
# 16. doc() on a nested / referenced type
# ══════════════════════════════════════════════════════════════════════════════

section("16. doc() on a class with a nested typed field")


class Retriever:
    """Fetch documents from a vector store."""

    top_k: int = 5
    score_threshold: float = 0.7

    def retrieve(self, query: str) -> list[str]:
        """Return the top *top_k* documents for *query*."""
        return []


class RAGAgent:
    """Retrieval-Augmented Generation agent."""

    retriever: Retriever = Retriever()
    max_tokens: int = 2048

    def answer(self, question: str) -> str:
        """Answer *question* using retrieved context."""
        return ""


show_output("doc(RAGAgent())", doc(RAGAgent()))

# ══════════════════════════════════════════════════════════════════════════════
# 17. pformat — truncated value formatting
# ══════════════════════════════════════════════════════════════════════════════

section("17. pformat — truncated value formatting")

big_list = list(range(100))
big_dict = {f"key_{i}": f"value_{i}" for i in range(20)}
nested = {"results": [{"id": i, "score": round(i * 0.1, 2)} for i in range(10)]}

show_output("pformat(big_list, max_length=5)", pformat(big_list, max_length=5))
show_output("pformat(big_dict, max_length=3)", pformat(big_dict, max_length=3))
show_output("pformat(nested, max_depth=1)", pformat(nested, max_depth=1))
show_output("pformat(nested, max_depth=2)", pformat(nested, max_depth=2))

# ══════════════════════════════════════════════════════════════════════════════
# 18. doc() on instance with expand=False field
# ══════════════════════════════════════════════════════════════════════════════

section("18. doc(instance) with an expand=False field")

agent_instance = ResearchAgent()
show_output("doc(agent_instance)", doc(agent_instance))
print("  → writing should appear as one-liner in doc(instance)")

# ══════════════════════════════════════════════════════════════════════════════
# 19. doc() on a third-party library
# ══════════════════════════════════════════════════════════════════════════════

section("19. doc() on a third-party class — pathlib.Path")

show_output("doc(Path)", doc(Path))
print("  → agentdoc works on any Python object, not just your own classes")

# ══════════════════════════════════════════════════════════════════════════════
# 20. agentdoc.introspect — methods() and variables()
# ══════════════════════════════════════════════════════════════════════════════

section("20. agentdoc.introspect — methods() and variables()")


class MyService:
    host: str = "localhost"
    port: int = 8080

    def connect(self) -> bool:
        """Open the connection."""
        ...

    def disconnect(self) -> None:
        """Close the connection."""
        ...


svc = MyService()
print("methods(MyService):")
print(methods(MyService))
print()
print("variables(svc):")
print(variables(svc))

# ══════════════════════════════════════════════════════════════════════════════
# 21. agentdoc.visibility — is_hidden_field() and filter_module_globals()
# ══════════════════════════════════════════════════════════════════════════════

section("21. agentdoc.visibility — is_hidden_field() and filter_module_globals()")


class SecureAgent2:
    api_key: Annotated[str, hidden] = "secret"
    name: str = "agent"


print("is_hidden_field(SecureAgent2, 'api_key'):", is_hidden_field(SecureAgent2, "api_key"))
print("is_hidden_field(SecureAgent2, 'name'):", is_hidden_field(SecureAgent2, "name"))

# ══════════════════════════════════════════════════════════════════════════════
# 22. spec.define_doc() — custom extractor for third-party types
# ══════════════════════════════════════════════════════════════════════════════

section("22. spec.define_doc() — custom extractor for third-party types")


@spec.define_doc(PurePosixPath)
def _(cls_or_instance):
    type_info = TypeInfo(
        name="PurePosixPath",
        base=None,
        fields=[FieldInfo(name="parts", type="tuple[str, ...]")],
        methods=[],
        docstring="An immutable POSIX filesystem path.",
    )
    if isinstance(cls_or_instance, type):
        return type_info
    return type_info, {"parts": cls_or_instance.parts}


print(doc(PurePosixPath))
print()
print(doc(PurePosixPath("/usr/local/bin")))

print()
print("─" * 72)
print("  Done.")
print("─" * 72)
print()
