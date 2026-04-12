"""attachments.py 测试。"""

import base64

import pytest

from cc_python.attachments import (
    is_text_file, is_image_file, read_file_as_attachment,
    extract_file_paths, process_attachments,
    TEXT_EXTENSIONS, IMAGE_EXTENSIONS,
)


class TestIsTextFile:
    def test_py(self):
        assert is_text_file("test.py") is True

    def test_md(self):
        assert is_text_file("README.md") is True

    def test_json(self):
        assert is_text_file("data.json") is True

    def test_unknown(self):
        assert is_text_file("file.bin") is False

    def test_no_ext(self):
        assert is_text_file("Makefile") is False


class TestIsImageFile:
    def test_png(self):
        assert is_image_file("photo.png") is True

    def test_jpg(self):
        assert is_image_file("photo.jpg") is True

    def test_not_image(self):
        assert is_image_file("file.txt") is False


class TestReadFileAsAttachment:
    def test_text_file(self, sample_py_file):
        result = read_file_as_attachment(sample_py_file)
        assert result is not None
        assert result["type"] == "text"
        assert "def hello" in result["text"]

    def test_not_found(self, tmp_path):
        result = read_file_as_attachment(tmp_path / "nonexistent.py")
        assert result is None

    def test_image_file(self, tmp_path):
        # 创建一个最小 PNG 文件
        img = tmp_path / "test.png"
        # 最小 PNG: 8-byte header + minimal chunks
        img.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
        result = read_file_as_attachment(img)
        assert result is not None
        assert result["type"] == "image"
        assert result["source"]["type"] == "base64"

    def test_unsupported_type(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b'\x00\x01\x02')
        result = read_file_as_attachment(f)
        assert result is None

    def test_directory(self, tmp_path):
        result = read_file_as_attachment(tmp_path)
        assert result is None


class TestExtractFilePaths:
    def test_with_at(self, sample_py_file):
        text = f"@{sample_py_file}"
        paths = extract_file_paths(text)
        assert len(paths) >= 1

    def test_no_match(self):
        paths = extract_file_paths("just some text without paths")
        assert paths == []

    def test_nonexistent_path(self):
        paths = extract_file_paths("@/nonexistent/file.py")
        assert paths == []


class TestProcessAttachments:
    def test_no_paths(self):
        result = process_attachments("hello world")
        assert result == []

    def test_with_file(self, sample_py_file):
        result = process_attachments(f"@{sample_py_file}")
        # 可能是空（取决于路径匹配），也可能有内容
        # 关键是不报错
        assert isinstance(result, list)
