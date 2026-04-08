# UnifiedLLM

A minimalistic wrapper around LLM libraries (litellm, OpenAI) providing a unified interface for function calling, structured outputs, and automatic retry logic.

## Features

- **Unified Interface**: Single API for multiple LLM providers via litellm and OpenAI SDK
- **Multiple Client Types**:
  - `CompletionClient`: Standard completion API via litellm
  - `ResponsesClient`: OpenAI Responses API via litellm
  - `OpenAIResponseClient`: Direct OpenAI SDK client with Responses API support
  - `FakeLLMClient`: Deterministic test client with scripted responses
- **Tool/Function Calling**: Standardized tool calling across different APIs
- **Structured Outputs**: Automatic JSON parsing with Pydantic model validation
- **Automatic Retry**: Built-in retry logic for parsing errors with helpful feedback
- **HTTP Request Logging**: Debug utility to capture and inspect API requests/responses

## Installation

Basic installation:
```bash
pip install unifiedllm
```

With OpenAI SDK support:
```bash
pip install unifiedllm[openai]
```

With HTTP logging support:
```bash
pip install unifiedllm[http-logging]
```

All optional features:
```bash
pip install unifiedllm[all]
```

## Quick Start

### Basic Usage with litellm

```python
from unifiedllm import CompletionClient
from pydantic import BaseModel

class Answer(BaseModel):
    response: str
    confidence: float

llm = CompletionClient(model="gpt-4o", api_key="your-key")

response = llm.call_llm_with_retry(
    messages=[{"role": "user", "content": "What is 2+2?"}],
    tools=[],
    output_model=Answer,
    max_retries=3
)

print(response.content.response)
```

### Function Calling

```python
from unifiedllm import CompletionClient, create_tool_from_callable

def get_weather(location: str, unit: str = "celsius") -> str:
    """Get the current weather for a location"""
    return f"Weather in {location}: 20°{unit[0].upper()}"

tool = create_tool_from_callable(get_weather)
llm = CompletionClient(model="gpt-4o", api_key="your-key")

response = llm.call(
    messages=[{"role": "user", "content": "What's the weather in Paris?"}],
    tools=[tool],
    output_model=None
)

if response.tool_calls:
    for tc in response.tool_calls:
        print(f"Calling {tc.name} with {tc.arguments}")
```

### Using OpenAI Responses API Directly

```python
from unifiedllm import OpenAIResponseClient
from pydantic import BaseModel

class Story(BaseModel):
    title: str
    content: str

llm = OpenAIResponseClient(
    model="gpt-4o-2024-08-06",
    api_key="your-key"
)

response = llm.call_llm_with_retry(
    messages=[{"role": "user", "content": "Write a short story about AI"}],
    tools=[],
    output_model=Story
)

print(response.content.title)
```

### HTTP Request Logging

Debug LLM API calls by capturing requests and responses:

```python
from unifiedllm.http_logging import enable_http_request_logging
from unifiedllm import CompletionClient

disable_logging = enable_http_request_logging(
    output_dir="debug_logs",
    url_filter="api.openai.com",
    save_responses=False,
    verbose=True
)

llm = CompletionClient(model="gpt-4o", api_key="your-key")
response = llm.call(
    messages=[{"role": "user", "content": "Hello!"}],
    tools=[],
    output_model=None
)

disable_logging()
```

## Architecture

### Core Components

- **`UnifiedLLM`**: Abstract base class defining the interface
- **`Tool`/`ToolParameter`**: Standardized tool representation
- **`ToolCall`**: Standardized tool call from LLM
- **`LLMResponse`**: Unified response format across all APIs

### Client Implementations

1. **`CompletionClient`**: Uses litellm's `completion()` API
   - Supports all litellm-compatible models
   - Standard message format
   - Tool calling via `tools` parameter

2. **`ResponsesClient`**: Uses litellm's `responses()` API
   - OpenAI Responses API via litellm
   - Message transformation for tool results
   - Structured outputs via `text_format`

3. **`OpenAIResponseClient`**: Direct OpenAI SDK integration
   - Native `responses.parse()` with `text_format`
   - Full structured output support
   - Native async support

4. **`FakeLLMClient`**: Deterministic test client
   - Returns scripted responses for hermetic testing
   - Thread-safe concurrent access
   - Helper methods for common test patterns

## Testing with FakeLLMClient

`FakeLLMClient` provides deterministic responses for testing without making actual API calls:

```python
from unifiedllm import FakeLLMClient, LLMResponse

# Simple message response
fake = FakeLLMClient.simple_message("Hello, world!")
response = await fake.acall(messages=[{"role": "user", "content": "Hi"}])
print(response.content)  # "Hello, world!"

# Tool call response
fake = FakeLLMClient.with_tool_call(
    tool_name="get_weather",
    tool_args={"location": "Paris", "unit": "celsius"}
)
response = await fake.acall(messages=[{"role": "user", "content": "Weather?"}])
print(response.tool_calls[0].name)  # "get_weather"

# Reasoning response (o1-style)
fake = FakeLLMClient.with_reasoning(
    reasoning="Let me think... 2+2=4",
    message="The answer is 4"
)
response = await fake.acall(messages=[{"role": "user", "content": "What is 2+2?"}])
print(response.reasoning)  # "Let me think... 2+2=4"

# Multiple scripted responses
fake = FakeLLMClient(scripted_responses=[
    LLMResponse(content="First response", ...),
    LLMResponse(content="Second response", ...),
])
r1 = await fake.acall(messages=[...])  # "First response"
r2 = await fake.acall(messages=[...])  # "Second response"
```

## Advanced Features

### API-Level Retry with RetryConfig

`CompletionClient` supports automatic retry for transient API errors (rate limits, server errors, timeouts) via `RetryConfig`:

```python
from unifiedllm import CompletionClient, RetryConfig

client = CompletionClient(
    model="gpt-4o-mini",
    api_key="your-key",
    retry_config=RetryConfig(
        max_retries=3,              # Retry up to 3 times for server errors
        rate_limit_extra_retries=3, # Extra retries for 429 rate limits
        base_delay=1.0,             # Initial delay between retries (seconds)
        max_delay=60.0,             # Maximum delay cap
        exponential_base=2.0,       # Exponential backoff multiplier
        jitter_factor=0.3,          # Randomization to avoid thundering herd
    ),
)

# API calls automatically retry on transient failures
response = await client.acall(messages=[{"role": "user", "content": "Hello!"}])
```

#### RetryConfig Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_retries` | 3 | Maximum retry attempts for server errors (500, 502, 503, 504) |
| `rate_limit_extra_retries` | 3 | Additional retries specifically for 429 rate limits |
| `base_delay` | 1.0 | Initial delay in seconds before first retry |
| `max_delay` | 60.0 | Maximum delay cap (prevents excessive waits) |
| `exponential_base` | 2.0 | Multiplier for exponential backoff |
| `jitter_factor` | 0.3 | Random jitter (0-1) to prevent synchronized retries |
| `rate_limit_base_delay` | 3.0 | Longer initial delay for rate limit errors |
| `on_retry` | None | Optional callback `(attempt, exception, delay) -> None` |

#### Retryable Errors

The retry logic automatically handles:
- **429 Rate Limits**: Extra retries with longer delays
- **500/502/503/504**: Server errors with exponential backoff
- **Timeouts**: Connection and request timeouts
- **Connection Errors**: Network-related failures

Non-retryable errors (400 Bad Request, 401 Unauthorized, etc.) are raised immediately.

#### Standalone Retry Functions

For custom retry logic outside of `CompletionClient`:

```python
from unifiedllm import with_retry, sync_retry, RetryConfig, RetryingWrapper

# Async retry
result = await with_retry(
    async_function,
    arg1, arg2,
    config=RetryConfig(max_retries=5),
    kwarg1="value"
)

# Sync retry
result = sync_retry(
    sync_function,
    arg1, arg2,
    config=RetryConfig(max_retries=5),
    kwarg1="value"
)

# Wrapper class for reusable retry behavior
retrying_client = RetryingWrapper(
    client.acall,
    config=RetryConfig(max_retries=5)
)
response = await retrying_client(messages=[...])
```

### Parsing Retry with Error Feedback

Separate from API-level retry, `call_llm_with_retry` handles parsing/validation errors:

```python
response = llm.call_llm_with_retry(
    messages=messages,
    tools=tools,
    output_model=OutputModel,
    max_retries=3  # Will retry up to 3 times on validation errors
)
```

When validation fails, the LLM receives formatted error messages:
```
Your previous response did not match the required format.
Please fix the following validation errors:
  - Field 'response -> confidence': value is not a valid float
```

### Async Support

All clients support async operations:

```python
response = await llm.acall_llm_with_retry(
    messages=messages,
    tools=tools,
    output_model=OutputModel
)
```

### Custom Validation

Pydantic models can include custom validation:

```python
from pydantic import BaseModel, field_validator

class Answer(BaseModel):
    response: str
    confidence: float

    @field_validator('confidence')
    def check_confidence(cls, v):
        if not 0 <= v <= 1:
            raise ValueError('confidence must be between 0 and 1')
        return v
```

## Development

### Running Tests

```bash
pytest tests/
```

### Formatting and Linting

```bash
ruff format ./src/unifiedllm/
ruff check ./src/unifiedllm/ --fix
```

## License

MIT
