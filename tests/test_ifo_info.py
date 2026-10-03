"""VIDEO_TS.IFO 头部和 IFO 统计的导出。"""

from pathlib import Path

import pytest

from whatdvd.ifo_info import SampleLog, parse_vmg, read_vmg


def _vmg(provider: bytes = b"", region_mask: int = 0, title_sets: int = 3) -> bytes:
    data = bytearray(0x800)
    data[:12] = b"DVDVIDEO-VMG"
    data[0x21] = 0x11
    data[0x23] = region_mask
    data[0x3E:0x40] = title_sets.to_bytes(2, "big")
    data[0x40 : 0x40 + len(provider)] = provider
    return bytes(data)


@pytest.mark.parametrize(
    ("mask", "regions"),
    [(0x00, "1-8"), (0xFD, "2"), (0xED, "2,5"), (0xFF, "")],
)
def test_regions(mask: int, regions: str) -> None:
    info = parse_vmg(_vmg(region_mask=mask))
    assert info is not None and info.regions == regions


def test_parse_vmg() -> None:
    info = parse_vmg(_vmg(b"DVD-LAB PRO\0\0  ", title_sets=5))
    assert info is not None
    assert (info.provider, info.version, info.title_sets) == ("DVD-LAB PRO", "1.1", 5)
    assert parse_vmg(b"DVDVIDEO-VTS" + bytes(0x800)) is None  # 标题集 IFO，不是 VMG
    assert parse_vmg(b"short") is None


def test_read_vmg(tmp_path: Path) -> None:
    video_ts = tmp_path / "VIDEO_TS"
    video_ts.mkdir()
    assert read_vmg(video_ts) is None
    (video_ts / "video_ts.ifo").write_bytes(_vmg(b"STUDIO"))  # 文件名不分大小写
    info = read_vmg(video_ts)
    assert info is not None and info.provider == "STUDIO" and info.bup_identical is None
    (video_ts / "VIDEO_TS.BUP").write_bytes(_vmg(b"OTHER"))
    info = read_vmg(video_ts)
    assert info is not None and info.bup_identical is False


def test_sample_log_csv(tmp_path: Path) -> None:
    log = SampleLog(tmp_path / "ifo_samples.jsonl")
    assert log.rows() == [] and log.csv().splitlines()[0].startswith("﻿time,disc")
    sample = {"disc": "Film", "total_bytes": 4_000_000_000, "provider": "", "regions": "5",
              "source_title": "Фильм / Film (2001) DVD5", "warnings": ["a", "b"]}
    log.append({**sample, "provider": "OLD"})
    log.append(sample)  # 同一张盘处理两次：只留最后一次
    log.append({**sample, "disc": "Other"})
    rows = log.rows()
    assert [r["disc"] for r in rows] == ["Film", "Other"] and rows[0]["provider"] == ""
    lines = log.csv().splitlines()
    assert len(lines) == 3 and "Фильм / Film (2001) DVD5" in lines[1] and "a / b" in lines[1]
