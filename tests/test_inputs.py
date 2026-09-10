from __future__ import annotations

import pytest

from qrgen.core import InvalidInput
from qrgen.inputs import MAX_INPUT_CHARS, parse_file, parse_text


def test_parse_text_ok() -> None:
    payload = parse_text("  https://ejemplo.com  ")
    assert payload.filename is None
    assert payload.content == "https://ejemplo.com"
    assert payload.content_type == "text"


def test_parse_text_empty_rejected() -> None:
    with pytest.raises(InvalidInput, match="cannot be empty"):
        parse_text("   ")


def test_parse_text_too_long_rejected() -> None:
    with pytest.raises(InvalidInput, match="maximum length"):
        parse_text("x" * (MAX_INPUT_CHARS + 1))


def test_parse_file_ok() -> None:
    payload = parse_file("archivo.txt", b"contenido del archivo")
    assert payload.filename == "archivo.txt"
    assert payload.content == "contenido del archivo"
    assert payload.content_type == "text/plain"


def test_parse_file_strips_path() -> None:
    payload = parse_file("../subida/archivo.txt", b"hola")
    assert payload.filename == "archivo.txt"


def test_parse_file_utf8_bom() -> None:
    payload = parse_file("a.txt", "\ufeffHola mundo".encode("utf-8"))
    assert payload.content == "Hola mundo"


def test_parse_file_invalid_utf8_rejected() -> None:
    with pytest.raises(InvalidInput, match="not valid UTF-8"):
        parse_file("a.txt", b"\xff\xfe\x00binary")


def test_parse_file_binary_rejected() -> None:
    with pytest.raises(InvalidInput, match="binary files are not supported"):
        parse_file("a.bin", b"PNG\x00\x01\x02data")


def test_parse_file_empty_rejected() -> None:
    with pytest.raises(InvalidInput, match="empty"):
        parse_file("a.txt", b"  \n  ")


def test_parse_file_too_long_rejected() -> None:
    with pytest.raises(InvalidInput, match="maximum length"):
        parse_file("a.txt", b"x" * (MAX_INPUT_CHARS + 1))


def test_parse_file_missing_name_rejected() -> None:
    with pytest.raises(InvalidInput, match="file name"):
        parse_file("", b"hola")


def test_parse_file_bad_name_rejected() -> None:
    with pytest.raises(InvalidInput, match="invalid file name"):
        parse_file("/", b"hola")
