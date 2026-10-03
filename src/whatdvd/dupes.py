"""查重：片名确定后按 IMDb 编号在站点上查已有的 DVD 原盘（通过 Jackett，只读），只列出来，不判断是否重复。

站点上的 DVD 标题同 Upload-Assistant 的写法："Come and See AKA Idi i smotri 1985 PAL 2xDVD9 DD 5.1"、
"Blade Runner 1982 Final Cut 2in1 EUR PAL 2xDVD9 DD 5.1-DHTs"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .indexer import Jackett, Release

_DISC = re.compile(r"(?<![A-Za-z\d])(?:(\d{1,2})\s*x\s*)?DVD-?([59])(?!\d)", re.IGNORECASE)
_STANDARD = re.compile(r"(?<![A-Za-z])(PAL|NTSC)(?![A-Za-z])")
# 不是原盘：压制、Remux、单个视频文件
_NOT_DISC = re.compile(
    r"dvdrip|remux|\b(?:x26[45]|h\.?26[45]|hevc|xvid|divx)\b|\.(?:mkv|mp4|avi|m2ts)$|blu-?ray|web-?dl", re.IGNORECASE
)


@dataclass(frozen=True)
class Existing:
    site: str
    title: str
    kind: str
    """"DVD9"、"2xDVD9"、"DVD9+DVD5"。"""
    standard: str | None
    size: int
    url: str | None
    seeders: int | None
    same: bool
    """格式和制式都和这张盘相同（制式写了才比较）。"""


def dvd_kind(title: str) -> str | None:
    """标题中的 DVD 原盘格式："2xDVD9"、"DVD9+DVD5"；不是 DVD 原盘时为 None。"""
    if _NOT_DISC.search(title):
        return None
    counts = {"9": 0, "5": 0}
    for count, layer in _DISC.findall(title):
        counts[layer] += int(count) if count else 1
    parts = [f"{counts[layer]}xDVD{layer}" if counts[layer] > 1 else f"DVD{layer}" for layer in ("9", "5") if counts[layer]]
    return "+".join(parts) or None


def existing_dvds(site: str, releases: list[Release], kind: str, standard: str | None) -> list[Existing]:
    found = []
    for release in releases:
        theirs = dvd_kind(release.title)
        if theirs is None:
            continue
        match = _STANDARD.search(release.title)
        their_standard = match[1] if match else None
        same = theirs == kind and (standard is None or their_standard is None or their_standard == standard)
        found.append(Existing(site, release.title, theirs, their_standard, release.size, release.details_url,
                              release.seeders, same))
    return sorted(found, key=lambda e: (not e.same, e.title))


def check(jackett: Jackett, site: str, imdb_id: str, kind: str, standard: str | None) -> list[Existing]:
    """在一个站点上查这部片已有的 DVD 原盘。jackett 为这个站点的 Jackett 客户端（indexer 为站点 id）。"""
    return existing_dvds(site, jackett.search_imdb(imdb_id), kind, standard)
