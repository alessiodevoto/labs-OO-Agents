# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Media capture for CodeAct execution via show().

Uses a ContextVar-based collector (same pattern as stdout/stderr capture)
to accumulate multimodal content blocks during execute_python() calls.

All content blocks follow LiteLLM's format conventions:
- Images:  {"type": "image_url", "image_url": {"url": ..., "format": ...}}
- Audio:   {"type": "input_audio", "input_audio": {"data": ..., "format": ...}}
- Files:   {"type": "file", "file": {"file_data": "data:...;base64,...", "filename": ...}}

LiteLLM automatically converts these to provider-native formats.
See: https://docs.litellm.ai/docs/completion/vision
"""

import base64
import contextvars
import io
from typing import Any

# Task-local media buffer for async-safe capture
_media_buffer_var: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "media_buffer", default=None
)

# Safety limit — prevent LLMs from flooding context with media
MAX_ATTACHMENTS_PER_EXECUTION = 5


def media_to_content_block(media: Any) -> dict:
    """Convert a Media object to a LiteLLM-compatible content block.

    Uses LiteLLM's universal formats. Provider-specific conversion
    (e.g. Anthropic image source format) is handled by LiteLLM.
    See: https://docs.litellm.ai/docs/completion/vision
    """
    from nemo_oo_agents.media import Audio, File, Image, Media

    if not isinstance(media, Media):
        raise TypeError(f"Expected Media (Image/Audio/File), got {type(media).__name__}")

    if isinstance(media, Image):
        image_url_dict: dict = {"url": media.data_url}
        # Add format hint — used by Anthropic/Bedrock/Vertex AI, ignored by OpenAI
        if media.media_type and media.media_type != "application/octet-stream":
            image_url_dict["format"] = media.media_type
        # Merge vendor metadata (e.g. detail="high" for OpenAI)
        if media.vendor_metadata:
            image_url_dict.update(media.vendor_metadata)
        return {"type": "image_url", "image_url": image_url_dict}

    if isinstance(media, Audio):
        # LiteLLM input_audio format: {"type": "input_audio", "input_audio": {"data": base64, "format": "wav"}}
        fmt = media.media_type.split("/")[-1] if media.media_type else "wav"
        return {
            "type": "input_audio",
            "input_audio": {"data": media._base64_data(), "format": fmt},
        }

    if isinstance(media, File):
        # LiteLLM file format
        return {
            "type": "file",
            "file": {"file_data": media.data_url, "filename": "attachment"},
        }

    # Generic fallback for unknown Media subclasses
    return {"type": "image_url", "image_url": {"url": media.data_url}}


# Backward-compatible alias
image_to_content_block = media_to_content_block


def show(obj: Any) -> None:
    """Display an image, audio clip, or file so you can see/hear it.

    Call show() on any Image, Audio, or File object to perceive its contents.
    Also accepts PIL images and matplotlib figures (auto-converted to PNG).
    """
    from nemo_oo_agents.media import Media

    if isinstance(obj, Media):
        block = media_to_content_block(obj)
    else:
        block = _try_auto_convert(obj)
        if block is None:
            raise TypeError(
                f"show() expects Image, Audio, File, PIL.Image, or matplotlib Figure, "
                f"got {type(obj).__name__}"
            )

    buf = _media_buffer_var.get()
    if buf is not None:
        if len(buf) >= MAX_ATTACHMENTS_PER_EXECUTION:
            print(f"[show() limit reached ({MAX_ATTACHMENTS_PER_EXECUTION}), attachment not added]")
            return
        buf.append(block)
        print(f"[shown: {obj}]")
    else:
        print(f"[show() called outside execution context, not captured: {obj}]")


def _try_auto_convert(obj: Any) -> dict | None:
    """Try to convert a non-Media object to a content block.

    Supports PIL.Image.Image and matplotlib.figure.Figure via lazy imports.
    Returns None if the object is not a recognized type.
    """
    block = _try_pil_to_content_block(obj)
    if block is not None:
        return block
    return _try_matplotlib_to_content_block(obj)


# Alias used by tests covering line 113 (the try_auto_convert return path).
_to_content_block = _try_auto_convert


def _try_pil_to_content_block(obj: Any) -> dict | None:
    """Convert a PIL Image to an image_url content block, or None."""
    try:
        from PIL import Image as PILImage  # type: ignore[import-not-found]

        if isinstance(obj, PILImage.Image):
            buf = io.BytesIO()
            obj.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "format": "image/png"},
            }
    except ImportError:
        pass
    return None


def _try_matplotlib_to_content_block(obj: Any) -> dict | None:
    """Convert a matplotlib Figure to an image_url content block, or None."""
    try:
        from matplotlib.figure import Figure  # type: ignore[import-not-found]

        if isinstance(obj, Figure):
            buf = io.BytesIO()
            obj.savefig(buf, format="png", bbox_inches="tight")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "format": "image/png"},
            }
    except ImportError:
        pass
    return None
