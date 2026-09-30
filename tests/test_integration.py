"""用 dvdauthor 生成的真实 DVD 结构跑完整的命令行流程。图床用假实现，不会真的上传。"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from dvdgen import make_iso, make_sample_set, missing_tools
from whatdvd import cli
from whatdvd.upload import UploadedImage

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(missing_tools()), reason=f"缺少外部命令：{missing_tools()}"),
]
needs_iso_tools = pytest.mark.skipif(
    not (shutil.which("genisoimage") and shutil.which("7z")), reason="缺少 genisoimage 或 7z"
)
needs_mktorrent = pytest.mark.skipif(shutil.which("mktorrent") is None, reason="缺少 mktorrent")


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def bdecode(data: bytes) -> object:
    def parse(i: int) -> tuple[object, int]:
        char = data[i : i + 1]
        if char == b"i":
            end = data.index(b"e", i)
            return int(data[i + 1 : end]), end + 1
        if char in (b"l", b"d"):
            items: list[object] = []
            i += 1
            while data[i : i + 1] != b"e":
                item, i = parse(i)
                items.append(item)
            if char == b"l":
                return items, i + 1
            return dict(zip(items[::2], items[1::2])), i + 1  # type: ignore[arg-type]
        colon = data.index(b":", i)
        length = int(data[i:colon])
        return data[colon + 1 : colon + 1 + length], colon + 1 + length

    return parse(0)[0]


class FakePixhost:
    uploaded: list[str] = []

    def __init__(self, domain: str, *, proxy: str | None = None) -> None:
        self.domain = domain

    def upload(self, path: Path) -> UploadedImage:
        FakePixhost.uploaded.append(path.name)
        return UploadedImage(f"https://img1.{self.domain}/images/1/{path.name}", "thumb", "page")

    def close(self) -> None:
        pass


@pytest.fixture(scope="module")
def movie(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_sample_set(tmp_path_factory.mktemp("dvd"))


@pytest.fixture
def fake_pixhost(monkeypatch: pytest.MonkeyPatch) -> type[FakePixhost]:
    FakePixhost.uploaded = []
    monkeypatch.setattr(cli, "Pixhost", FakePixhost)
    return FakePixhost


def test_scan(movie: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan", str(movie)]) == 0
    out = capsys.readouterr().out
    assert "[Disc 1]" in out and "[Disc 2]" in out
    assert "VOB：VTS_02_1.VOB" in out  # 正片不在 VTS_01，按最大文件选中
    assert "PAR 1.422 → 1024x576" in out
    assert "PAR 0.889 → 720x540" in out
    assert "制式：PAL" in out and "制式：NTSC" in out
    assert "容量：DVD5" in out


def test_run(movie: Path, tmp_path: Path, fake_pixhost: type[FakePixhost]) -> None:
    output = tmp_path / "out"
    # 盘 1 的 VOB 约 70 秒，间隔 21 秒：第 1–3 张成功，第 4 张（84 秒）超出时长而失败
    assert cli.main(["run", str(movie), "-n", "4", "-o", str(output)]) == 1

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

    # 成功的 4 张截图都上传了，截图失败不影响生成发布说明
    assert fake_pixhost.uploaded == [f"{disc1}.scr{i}.png" for i in (1, 2, 3)] + ["Disc.2.VTS_01_1.VOB.scr1.png"]
    post = (output / "Sample.Movie.2001.post.txt").read_text(encoding="utf-8")
    assert post.startswith("[b]Disc 1[/b]\n[quote]\nGeneral\n")
    assert f"[img]https://img1.pixhost.to/images/1/{disc1}.scr3.png[/img]" in post
    assert "[b]Disc 2[/b]" in post


def test_run_no_upload(movie: Path, tmp_path: Path, fake_pixhost: type[FakePixhost]) -> None:
    output = tmp_path / "out"
    assert cli.main(["run", str(movie / "Disc 1"), "-n", "3", "-o", str(output), "--no-upload"]) == 0
    assert fake_pixhost.uploaded == []
    assert not list(output.glob("*.post.txt"))


@needs_iso_tools
def test_iso(movie: Path, tmp_path: Path, fake_pixhost: type[FakePixhost], capsys: pytest.CaptureFixture[str]) -> None:
    iso = make_iso(movie / "Disc 1", tmp_path / "Sample Disc (PAL).iso")
    output = tmp_path / "out"
    temp_root = tmp_path / "temp"
    temp_root.mkdir()

    assert cli.main(["run", str(iso), "-n", "3", "-o", str(output), "--temp-dir", str(temp_root)]) == 0
    out = capsys.readouterr().out
    assert "[Sample Disc (PAL)]" in out
    assert "VOB：VTS_02_1.VOB" in out
    assert f"容量：DVD5，共 {cli.format_bytes(iso.stat().st_size)}" in out

    title = "Sample.Disc.PAL.VTS_02_1.VOB"
    assert png_size(output / f"{title}.scr1.png") == (1024, 576)
    report = (output / f"{title}.mediainfo.txt").read_text(encoding="utf-8")
    assert "Complete name                            : Sample Disc (PAL)/VIDEO_TS/VTS_02_1.VOB" in report
    assert str(temp_root) not in report
    assert list(temp_root.iterdir()) == []  # 临时解包的文件已清理
    assert (output / "Sample.Disc.PAL.post.txt").is_file()


@needs_mktorrent
def test_torrent(movie: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "out"
    assert cli.main(["torrent", str(movie), "-a", "https://tracker.example/announce", "-o", str(output)]) == 0
    torrent = bdecode((output / "Sample.Movie.2001.torrent").read_bytes())
    assert isinstance(torrent, dict)
    assert torrent[b"announce"] == b"https://tracker.example/announce"
    info = torrent[b"info"]
    assert info[b"private"] == 1
    assert info[b"piece length"] == 2**24
    assert info[b"name"] == b"Sample Movie (2001)"
    paths = sorted(b"/".join(f[b"path"]) for f in info[b"files"])
    assert b"Disc 1/VIDEO_TS/VTS_02_1.VOB" in paths
    assert "种子：" in capsys.readouterr().out


@needs_mktorrent
def test_torrent_without_announce(movie: Path, tmp_path: Path) -> None:
    assert cli.main(["torrent", str(movie / "Disc 2"), "-l", "20", "-o", str(tmp_path)]) == 0
    torrent = bdecode((tmp_path / "Disc.2.torrent").read_bytes())
    assert isinstance(torrent, dict)
    assert b"announce" not in torrent
    assert torrent[b"info"][b"piece length"] == 2**20
