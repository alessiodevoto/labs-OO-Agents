# Unified Multimodal Support — show() for Images, Audio, Files

## Goal

Enable agents to pass multimodal content (images, audio, files) to LLMs. CodeAct
agents call `show()` inside `execute_python()`; PredictStrategy agents pass Media
objects as method parameters. Both paths produce LiteLLM-native content blocks that
work across all supported providers.

## Architecture

### Media classes (`agent006.media`)

LLM-agnostic data holders. Store raw data (as data URLs) + MIME type. No
provider-specific logic.

```python
class Media:
    """Base class — stores data URL + MIME type."""
    _data_url: str
    _media_type: str

    @classmethod
    def from_file(cls, path) -> Media: ...
    @classmethod
    def from_bytes(cls, data, *, media_type) -> Media: ...
    @classmethod
    def from_url(cls, url, *, media_type) -> Media: ...

class Image(Media): ...   # _modality = "image"
class Audio(Media): ...   # _modality = "audio"
class File(Media): ...    # _modality = "file"
```

### Content block conversion (`runtime/media_capture.py`)

`media_to_content_block()` converts any Media object to a **LiteLLM-universal
content block**. LiteLLM handles provider-specific conversion automatically.

| Media type | LiteLLM format |
|-----------|----------------|
| Image | `{"type": "image_url", "image_url": {"url": "data:...", "format": "image/png"}}` |
| Audio | `{"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav"}}` |
| File | `{"type": "file", "file": {"file_data": "data:...;base64,...", "filename": "attachment"}}` |

See: https://docs.litellm.ai/docs/completion/vision

### `show()` builtin + ContextVar media collector

`show()` is injected into CodeAct's `exec_globals`. Uses a ContextVar-based buffer
(same pattern as stdout/stderr capture) for async-safe, task-local media collection.

```python
_media_buffer_var: ContextVar[list[dict] | None] = ContextVar("media_buffer", default=None)

MAX_ATTACHMENTS_PER_EXECUTION = 5  # Safety limit

def show(obj: Any) -> None:
    """Display an image, audio clip, or file so you can see/hear it."""
    # Accepts Media, PIL.Image, matplotlib.Figure (auto-converted to PNG)
    # Buffer lifecycle: set before execute_code(), cleared in finally block
```

**Why ContextVar?** Mirrors the existing stdout/stderr capture in `actor.py`. Each
concurrent execution gets its own buffer (task-local), with clean lifecycle via
`try/finally`. Anyone who understands stdout capture immediately understands media
capture.

### Data flow

#### CodeAct path

```
LLM calls execute_python() with show(Image.from_file("photo.png"))
  → media_capture.show() converts to content block via media_to_content_block()
  → block appended to ContextVar buffer
  → actor.py collects buffer → ExecutionResult(images=[...])
  → codeact.py propagates → PythonOutput(images=[...])
  → provider formatter renders as multipart content
```

#### PredictStrategy path

```
method(img=Image.from_file("photo.png"))
  → predict.py detects Media in call.args + call.kwargs
  → media_to_content_block(img) → LiteLLM content block
  → Task(prompt=..., images=[block])
  → formatter renders as multipart content
```

#### Prefill auto-show (CodeAct with Media parameters)

`InspectInputsPrefill` auto-detects Media parameters and emits `show(param)`
instead of `pprint(param)`, so the LLM perceives images/audio/files passed as
method arguments.

### Event fields

Both `ExecutionResult` and `PythonOutput` carry an `images: list[dict]` field
(`repr=False` to exclude from `PlainBlockFormatter` text serialization).

`Task` also has `images: list[dict]` (`repr=False`) for PredictStrategy media.

### Provider formatting

Cross-package boundary: `context_blocks` cannot import `agent006`. Uses
`getattr(block.event, 'images', None)` for generic access.

- **OpenAI format**: multipart content array with text + image_url blocks
- **Anthropic format**: `_openai_image_to_anthropic()` converts image_url to
  native `{"type": "image", "source": {"type": "base64", ...}}` blocks

> NOTE: The data URL parsing in `formatter.py:_openai_image_to_anthropic()` mirrors
> `Media._base64_data()` in `agent006/media.py`. If the parsing logic changes,
> update both locations.

### Vision capability check

`UnifiedLLM.supports_vision()` delegates to `litellm.supports_vision(model=...)`.
Returns `False` for unknown models.

## Files Changed

1. **NEW** `src/agent006/media.py` — `Media` base + `Image`, `Audio`, `File` (LLM-agnostic data holders)
2. **NEW** `src/agent006/runtime/media_capture.py` — ContextVar collector, `show()`, `media_to_content_block()`, PIL/matplotlib auto-conversion
3. **EDIT** `src/agent006/events.py` — `images` field on `ExecutionResult`, `PythonOutput`, `Task`
4. **EDIT** `src/agent006/runtime/actor.py` — Wire media buffer lifecycle, inject `show`+Media types into exec_globals
5. **EDIT** `src/agent006/strategies/codeact.py` — Propagate `result.images` to all `PythonOutput` sites
6. **EDIT** `src/agent006/strategies/predict.py` — Collect Media from args+kwargs as content blocks
7. **EDIT** `src/agent006/strategies/prefill.py` — `InspectInputsPrefill` auto-calls `show()` for Media params
8. **EDIT** `packages/context-blocks/src/context_blocks/formatter.py` — Provider formatters emit multipart content
9. **EDIT** `packages/unifiedllm/src/unifiedllm/unifiedllm.py` — `supports_vision()` method
10. **EDIT** `examples/quickstart/12_multimodal.py` — Use `agent006.media` types
11. **NEW** `tests/test_media_capture.py` — Unit tests for media capture, show(), auto-conversion, limits
12. **NEW** `tests/test_provider_image_rendering.py` — Unit tests for multipart content in provider formatters

## Out of Scope

- Video support (can be added later using same Media pattern)
- Vision capability gating (skip `show()` for non-vision models — follow-up)
