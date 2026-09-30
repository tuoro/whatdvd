"""用 dvdauthor 生成的真实 DVD 结构跑完整的命令行流程。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dvdgen import make_sample_set, missing_tools
from whatdvd.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(missing_tools()), reason=f"缺少外部命令：{missing_tools()}"),
]


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


@pytest.fixture(scope="module")
def movie(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_sample_set(tmp_path_factory.mktemp("dvd"))


def test_scan(movie: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(movie)]) == 0
    out = capsys.readouterr().out
    assert "[Disc 1]" in out and "[Disc 2]" in out
    assert "VOB：VTS_02_1.VOB" in out  # 正片不在 VTS_01，按最大文件选中
    assert "PAR 1.422 → 1024x576" in out
    assert "PAR 0.889 → 720x540" in out
    assert "制式：PAL" in out and "制式：NTSC" in out
    assert "容量：DVD5" in out


def test_run(movie: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    # 盘 1 的 VOB 约 70 秒，间隔 21 秒：第 1–3 张成功，第 4 张（84 秒）超出时长而失败
    assert main(["run", str(movie), "-n", "4", "-o", str(output)]) == 1

    disc1 = "Disc.1.VTS_02_1.VOB"
    for index in (1, 2, 3):
        assert png_size(output / f"{disc1}.scr{index}.png") == (1024, 576)
    assert not (output / f"{disc1}.scr4.png").exists()

    assert png_size(output / "Disc.2.VTS_01_1.VOB.scr1.png") == (720, 540)

    report = (output / f"{disc1}.mediainfo.txt").read_text(encoding="utf-8")
    names = [line.split(":", 1)[1].strip() for line in report.splitlines() if line.startswith("Complete name")]
    # 两个 IFO 同为 12288 字节，按文件名取到 VTS_01_0.IFO：这是 jietu 规则的已知局限
    assert names == [
        "Sample Movie (2001)/Disc 1/VIDEO_TS/VTS_02_1.VOB",
        "Sample Movie (2001)/Disc 1/VIDEO_TS/VTS_01_0.IFO",
    ]
    assert str(movie.parent) not in report
