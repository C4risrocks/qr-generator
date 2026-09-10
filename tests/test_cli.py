from __future__ import annotations

from fastapi.testclient import TestClient

from qrgen.server import app

client = TestClient(app)


def test_cli_script_entry_point() -> None:
    from qrgen import cli

    assert callable(cli.run_generate)


def test_hash_password_roundtrip(capsys) -> None:
    from qrgen import cli
    from qrgen.passwords import verify_password

    assert cli.run_hash_password(["secret123"]) == 0
    encoded = capsys.readouterr().out.strip()
    assert verify_password("secret123", encoded) is True
    assert verify_password("wrong", encoded) is False


def test_admin_setup_prints_env_vars(capsys) -> None:
    from qrgen import cli
    from qrgen.passwords import verify_password

    assert cli.run_admin_setup(["secret123"]) == 0
    out = capsys.readouterr().out
    lines = {line for line in out.splitlines() if "=" in line and not line.startswith("#")}
    username = next(line.split("=", 1)[1] for line in lines if line.startswith("ADMIN_USERNAME="))
    hashed = next(line.split("=", 1)[1] for line in lines if line.startswith("ADMIN_PASSWORD_HASH="))
    secret = next(line.split("=", 1)[1] for line in lines if line.startswith("SESSION_SECRET="))
    assert username == "admin"
    assert verify_password("secret123", hashed) is True
    assert len(secret) >= 32


def test_admin_setup_custom_username_and_secret(capsys) -> None:
    from qrgen import cli
    from qrgen.passwords import verify_password

    assert cli.run_admin_setup(["s3cret", "--username", "root", "--session-secret", "my-secret"]) == 0
    out = capsys.readouterr().out
    assert "ADMIN_USERNAME=root" in out
    assert "SESSION_SECRET=my-secret" in out
    hashed = next(
        line.split("=", 1)[1]
        for line in out.splitlines()
        if line.startswith("ADMIN_PASSWORD_HASH=")
    )
    assert verify_password("s3cret", hashed) is True


def test_admin_setup_generates_unique_secret(capsys) -> None:
    from qrgen import cli

    cli.run_admin_setup(["secret123"])
    first = next(
        line.split("=", 1)[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("SESSION_SECRET=")
    )
    cli.run_admin_setup(["secret123"])
    second = next(
        line.split("=", 1)[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("SESSION_SECRET=")
    )
    assert first != second


def test_admin_setup_rejects_empty_password(capsys) -> None:
    from qrgen import cli

    assert cli.run_admin_setup([""]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_missing_input(tmp_path) -> None:
    from qrgen import cli

    assert cli.run_generate(["-o", str(tmp_path / "x.png")]) == 2


def test_cli_file_input(tmp_path, capsys) -> None:
    from qrgen import cli

    source = tmp_path / "archivo.txt"
    source.write_text("contenido del archivo", encoding="utf-8")
    out = tmp_path / "qr.png"
    assert cli.run_generate(["--file", str(source), "-o", str(out)]) == 0
    assert out.exists()
    assert "QR code written" in capsys.readouterr().out


def test_cli_file_both_inputs_rejected(tmp_path) -> None:
    from qrgen import cli

    source = tmp_path / "a.txt"
    source.write_text("hola", encoding="utf-8")
    assert cli.run_generate(["data", "--file", str(source), "-o", str(tmp_path / "x.png")]) == 2


def test_cli_file_binary_rejected(tmp_path) -> None:
    from qrgen import cli

    source = tmp_path / "a.bin"
    source.write_bytes(b"\x00\x01\x02")
    assert cli.run_generate(["--file", str(source), "-o", str(tmp_path / "x.png")]) == 2


def test_cli_missing_file(tmp_path) -> None:
    from qrgen import cli

    assert cli.run_generate(["--file", str(tmp_path / "nope.txt"), "-o", str(tmp_path / "x.png")]) == 2
