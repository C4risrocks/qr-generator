from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "bake-git-sha.py"
    spec = importlib.util.spec_from_file_location("bake_git_sha", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_arg_wins(monkeypatch, tmp_path, capsys) -> None:
    mod = load_module()
    monkeypatch.setenv("GIT_SHA", "abc1234")
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    assert mod.main() == 0
    assert capsys.readouterr().out.strip() == "abc1234"


def test_resolves_loose_ref(tmp_path) -> None:
    mod = load_module()
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("deadbeef\n", encoding="utf-8")
    assert mod.from_git(str(git)) == "deadbeef"


def test_resolves_detached_head(tmp_path) -> None:
    mod = load_module()
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("cafef00d\n", encoding="utf-8")
    assert mod.from_git(str(git)) == "cafef00d"


def test_resolves_packed_refs(tmp_path) -> None:
    mod = load_module()
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "0123456789abcdef0123456789abcdef01234567 refs/heads/main\n",
        encoding="utf-8",
    )
    assert mod.from_git(str(git)) == "0123456789abcdef0123456789abcdef01234567"


def test_resolves_git_dir_env(monkeypatch, tmp_path, capsys) -> None:
    """Docker mounts .git elsewhere and passes GIT_DIR; main must use it."""
    mod = load_module()
    git = tmp_path / "mounted-git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("feedface\n", encoding="utf-8")
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("GIT_DIR", str(git))
    assert mod.main() == 0
    assert capsys.readouterr().out.strip() == "feedface"


def test_missing_git_returns_none(tmp_path) -> None:
    mod = load_module()
    assert mod.from_git(str(tmp_path / "nogit")) is None


def test_non_hex_build_arg_falls_back_to_git(monkeypatch, tmp_path, capsys) -> None:
    """A malformed GIT_SHA build arg must never be baked: resolve .git."""
    mod = load_module()
    monkeypatch.setenv("GIT_SHA", "not-a-sha")
    monkeypatch.setenv("GIT_DIR", str(tmp_path))  # no .git inside -> unknown
    assert mod.main() == 0
    assert capsys.readouterr().out.strip() == "unknown"


def test_unknown_build_arg_stays_unknown(monkeypatch, tmp_path, capsys) -> None:
    mod = load_module()
    monkeypatch.setenv("GIT_SHA", "unknown")
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    assert mod.main() == 0
    assert capsys.readouterr().out.strip() == "unknown"


def test_is_hex_sha_bounds() -> None:
    mod = load_module()
    assert mod.is_hex_sha("d" * 40)
    assert mod.is_hex_sha("abc1")
    assert not mod.is_hex_sha("abc")
    assert not mod.is_hex_sha("ABC1")
    assert not mod.is_hex_sha("unknown")
    assert not mod.is_hex_sha("")
