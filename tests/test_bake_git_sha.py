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
    monkeypatch.setenv("GIT_SHA", "abc123")
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    assert mod.main() == 0
    assert capsys.readouterr().out.strip() == "abc123"


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
