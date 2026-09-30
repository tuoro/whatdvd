from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import pytest

from whatdvd.runner import Arg, CommandError, CommandResult

Handler = Callable[[tuple[str, ...]], CommandResult]


def ok(argv: tuple[str, ...], stdout: str = "") -> CommandResult:
    return CommandResult(args=argv, returncode=0, stdout=stdout, stderr="")


class FakeRunner:
    """记录调用的假 Runner，handler 决定每条命令的返回。"""

    def __init__(self, handler: Handler | None = None, available: Iterable[str] = ()) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.cwds: list[Path | None] = []
        self.handler = handler or ok
        self.available = set(available)

    def run(self, args: Sequence[Arg], *, check: bool = True, cwd: Path | None = None) -> CommandResult:
        argv = tuple(os.fspath(arg) for arg in args)
        self.calls.append(argv)
        self.cwds.append(cwd)
        result = self.handler(argv)
        if check and result.returncode != 0:
            raise CommandError(result)
        return result

    def which(self, name: str) -> str | None:
        return f"/usr/bin/{name}" if name in self.available else None


def make_file(path: Path, size: int) -> Path:
    """创建指定大小的稀疏文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


@pytest.fixture
def fake_runner() -> type[FakeRunner]:
    return FakeRunner


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个测试使用独立的 HOME，避免读写真实的 ~/.config 和 ~/.local/share。"""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WHATDVD_TOKEN", raising=False)
    monkeypatch.delenv("WHATDVD_CONFIG", raising=False)
    return home
