# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for nemo_oo_agents.media — classmethods and _base64_data edge cases.

Covers:
- Image.from_file(path) via tmp_path fixture (lines 64-68)
- Image.from_url(url, media_type="") — empty media_type (line 82)
- Image._base64_data() when data_url does NOT start with 'data:' (line 88)
"""

from pathlib import Path

from nemo_oo_agents.media import Image


class TestImageFromFile:
    def test_from_file_reads_bytes_and_returns_image(self, tmp_path: Path):
        """Image.from_file() loads a file and returns an Image instance."""
        png_file = tmp_path / "test.png"
        # Minimal valid-looking bytes; mimetypes will guess image/png from extension
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        img = Image.from_file(png_file)

        assert isinstance(img, Image)
        assert img.media_type == "image/png"
        assert img.data_url.startswith("data:image/png;base64,")
        assert img.modality == "image"

    def test_from_file_unknown_extension_falls_back_to_octet_stream(self, tmp_path: Path):
        """from_file() uses application/octet-stream for unrecognised extensions."""
        unknown_file = tmp_path / "data.xyzzy"
        unknown_file.write_bytes(b"\x00\x01\x02\x03")

        img = Image.from_file(unknown_file)

        assert isinstance(img, Image)
        assert img.media_type == "application/octet-stream"


class TestImageFromUrl:
    def test_from_url_with_empty_media_type(self):
        """Image.from_url() accepts an empty string as media_type."""
        url = "https://example.com/photo.jpg"
        img = Image.from_url(url, media_type="")

        assert isinstance(img, Image)
        assert img.data_url == url
        assert img.media_type == ""

    def test_from_url_default_media_type_is_image_jpeg(self):
        """Image.from_url() defaults to 'image/jpeg' when no media_type is given."""
        img = Image.from_url("https://example.com/photo.jpg")

        assert img.media_type == "image/jpeg"


class TestMediaFromUrl:
    def test_base_media_from_url_stores_url_and_media_type(self):
        """Media.from_url() stores the URL and media type without downloading."""
        from nemo_oo_agents.media import Media

        url = "https://example.com/file.bin"
        m = Media.from_url(url, media_type="application/octet-stream")

        assert m.data_url == url
        assert m.media_type == "application/octet-stream"
        assert m.size_bytes is None  # URL reference, no local data


class TestBaseBase64Data:
    def test_data_url_starting_with_data_colon_extracts_base64(self):
        """_base64_data() strips the data URL prefix and returns the base64 portion."""
        img = Image.from_bytes(b"hello", media_type="image/png")
        b64 = img._base64_data()

        # Should not contain the data URL prefix
        assert not b64.startswith("data:")
        # Verify it is pure base64
        import base64

        decoded = base64.b64decode(b64)
        assert decoded == b"hello"

    def test_data_url_not_starting_with_data_returns_url_as_is(self):
        """_base64_data() returns the URL verbatim when it doesn't start with 'data:'."""
        url = "https://example.com/image.png"
        img = Image.from_url(url)

        result = img._base64_data()

        assert result == url
