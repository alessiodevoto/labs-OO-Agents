"""Tests for multimodal media capture via show() in CodeAct execution."""

import base64

import pytest

from nemo_oo_agents.media import Audio, File, Image, Media
from nemo_oo_agents.runtime.media_capture import (
    MAX_ATTACHMENTS_PER_EXECUTION,
    _media_buffer_var,
    _try_auto_convert,
    media_to_content_block,
    show,
)

# ---------------------------------------------------------------------------
# Media class hierarchy
# ---------------------------------------------------------------------------


class TestMediaHierarchy:
    def test_image_is_media(self):
        img = Image.from_bytes(b"fake", media_type="image/png")
        assert isinstance(img, Media)
        assert img.modality == "image"

    def test_audio_is_media(self):
        audio = Audio.from_bytes(b"fake", media_type="audio/wav")
        assert isinstance(audio, Media)
        assert audio.modality == "audio"

    def test_file_is_media(self):
        f = File.from_url("https://example.com/report.pdf")
        assert isinstance(f, Media)
        assert f.modality == "file"

    def test_no_llm_specific_methods(self):
        """Media should NOT have LLM-specific methods."""
        img = Image.from_bytes(b"fake", media_type="image/png")
        assert not hasattr(img, "to_content_block")


class TestImage:
    def test_from_bytes(self):
        img = Image.from_bytes(b"\x89PNG" + b"\x00" * 100, media_type="image/png")
        r = repr(img)
        assert r.startswith("Image(image/png, ")
        assert "bytes)" in r

    def test_data_url_property(self):
        img = Image.from_bytes(b"fake", media_type="image/png")
        assert img.data_url.startswith("data:image/png;base64,")

    def test_from_url_default_media_type(self):
        img = Image.from_url("https://example.com/photo.jpg")
        assert img.media_type == "image/jpeg"


class TestAudio:
    def test_from_bytes(self):
        audio = Audio.from_bytes(b"RIFF" + b"\x00" * 100, media_type="audio/wav")
        r = repr(audio)
        assert r.startswith("Audio(audio/wav, ")
        assert "bytes)" in r

    def test_from_url_default_media_type(self):
        audio = Audio.from_url("https://example.com/clip.wav")
        assert audio.media_type == "audio/wav"

    def test_base64_data(self):
        data = b"test audio data"
        audio = Audio.from_bytes(data, media_type="audio/wav")
        decoded = base64.b64decode(audio._base64_data())
        assert decoded == data


class TestFile:
    def test_from_url(self):
        f = File.from_url("https://example.com/report.pdf")
        assert f.media_type == "application/pdf"
        assert f.data_url == "https://example.com/report.pdf"


# ---------------------------------------------------------------------------
# Content block conversion
# ---------------------------------------------------------------------------


class TestMediaToContentBlock:
    def test_image_block(self):
        img = Image.from_bytes(b"fake", media_type="image/png")
        block = media_to_content_block(img)
        assert block["type"] == "image_url"
        assert block["image_url"]["url"].startswith("data:image/png;base64,")
        assert block["image_url"]["format"] == "image/png"

    def test_image_url_block(self):
        img = Image.from_url("https://example.com/photo.jpg")
        block = media_to_content_block(img)
        assert block["image_url"]["url"] == "https://example.com/photo.jpg"

    def test_audio_block(self):
        audio = Audio.from_bytes(b"wav-data", media_type="audio/wav")
        block = media_to_content_block(audio)
        assert block["type"] == "input_audio"
        assert block["input_audio"]["format"] == "wav"
        decoded = base64.b64decode(block["input_audio"]["data"])
        assert decoded == b"wav-data"

    def test_file_block(self):
        f = File.from_url("https://example.com/report.pdf")
        block = media_to_content_block(f)
        assert block["type"] == "file"
        assert block["file"]["file_data"] == "https://example.com/report.pdf"

    def test_raises_on_non_media(self):
        with pytest.raises(TypeError, match="Expected Media"):
            media_to_content_block("not media")

    def test_image_base64_roundtrips(self):
        original = b"test image data"
        img = Image.from_bytes(original, media_type="image/png")
        block = media_to_content_block(img)
        _, b64_data = block["image_url"]["url"].split(",", 1)
        assert base64.b64decode(b64_data) == original


# ---------------------------------------------------------------------------
# show() function
# ---------------------------------------------------------------------------


class TestShow:
    def test_show_collects_image(self, capsys):
        buf: list[dict] = []
        token = _media_buffer_var.set(buf)
        try:
            img = Image.from_bytes(b"test", media_type="image/png")
            show(img)
            assert len(buf) == 1
            assert buf[0]["type"] == "image_url"
        finally:
            _media_buffer_var.reset(token)

    def test_show_collects_audio(self, capsys):
        buf: list[dict] = []
        token = _media_buffer_var.set(buf)
        try:
            audio = Audio.from_bytes(b"test", media_type="audio/wav")
            show(audio)
            assert len(buf) == 1
            assert buf[0]["type"] == "input_audio"
        finally:
            _media_buffer_var.reset(token)

    def test_show_collects_file(self, capsys):
        buf: list[dict] = []
        token = _media_buffer_var.set(buf)
        try:
            f = File.from_url("https://example.com/doc.pdf")
            show(f)
            assert len(buf) == 1
            assert buf[0]["type"] == "file"
        finally:
            _media_buffer_var.reset(token)

    def test_show_prints_acknowledgment(self, capsys):
        buf: list[dict] = []
        token = _media_buffer_var.set(buf)
        try:
            show(Image.from_bytes(b"test", media_type="image/png"))
            captured = capsys.readouterr()
            assert "[shown:" in captured.out
        finally:
            _media_buffer_var.reset(token)

    def test_show_raises_on_non_media(self):
        buf: list[dict] = []
        token = _media_buffer_var.set(buf)
        try:
            with pytest.raises(TypeError, match="got int"):
                show(42)
        finally:
            _media_buffer_var.reset(token)

    def test_show_limit_enforced(self, capsys):
        buf: list[dict] = []
        token = _media_buffer_var.set(buf)
        try:
            for i in range(MAX_ATTACHMENTS_PER_EXECUTION + 3):
                show(Image.from_bytes(f"img{i}".encode(), media_type="image/png"))
            assert len(buf) == MAX_ATTACHMENTS_PER_EXECUTION
            captured = capsys.readouterr()
            assert "limit reached" in captured.out
        finally:
            _media_buffer_var.reset(token)

    def test_show_outside_context_warns(self, capsys):
        show(Image.from_bytes(b"test", media_type="image/png"))
        captured = capsys.readouterr()
        assert "outside execution context" in captured.out


class TestAutoConvert:
    def test_unknown_type_returns_none(self):
        assert _try_auto_convert("not an image") is None
        assert _try_auto_convert(42) is None

    def test_pil_image_conversion(self):
        try:
            from PIL import Image as PILImage
        except ImportError:
            pytest.skip("Pillow not installed")
        pil_img = PILImage.new("RGB", (10, 10), color="red")
        block = _try_auto_convert(pil_img)
        assert block is not None
        assert block["type"] == "image_url"
        assert block["image_url"]["format"] == "image/png"
