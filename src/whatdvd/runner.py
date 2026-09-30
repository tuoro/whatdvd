"""外部命令调用的统一封装。

所有外部命令（ffmpeg、mediainfo、nconvert……）都经过 Runner 执行，测试时换成假实现。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

Arg = str | os.PathLike[str]


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip()
        message = f"命令执行失败（退出码 {result.returncode}）：{' '.join(result.args)}"
        super().__init__(f"{message}\n{detail}" if detail else message)


class Runner(Protocol):
    def run(self, args: Sequence[Arg], *, check: bool = True) -> CommandResult: ...

    def which(self, name: str) -> str | None: ...


class SubprocessRunner:
    def run(self, args: Sequence[Arg], *, check: bool = True) -> CommandResult:
        argv = tuple(os.fspath(arg) for arg in args)
        proc = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL)
        result = CommandResult(
            args=argv,
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
        )
        if check and result.returncode != 0:
            raise CommandError(result)
        return result

    def which(self, name: str) -> str | None:
        return shutil.which(name)
