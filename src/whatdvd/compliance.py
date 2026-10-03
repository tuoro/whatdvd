"""从 MediaInfo 看盘是否可能改制过（只提示，不拒绝）。

俄语站点上的 Custom 盘最常见的做法是给外国片加上几条俄语配音或画外音。2026 年 10 月用 rutor 上 469 个
DVD 原盘发布页统计：带两条以上俄语音轨、同时还有原声的，30 个里 11 个写明是 Custom，其余 19 个也几乎都是
外国片加了多条俄语配音；只有一条俄语音轨的不能说明问题（俄罗斯正版盘本来就有一条俄语配音）。

只看 IFO 的 MediaInfo（VOB 里的音轨通常没有语言标记）；没有 IFO 部分时看全部。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioTrack:
    language: str | None
    format: str | None
    channels: str | None


def _sections(report: str) -> list[tuple[str, dict[str, str]]]:
    """MediaInfo 文本：[(段名, {字段: 值})]，段名如 General、Video、Audio #2。"""
    sections: list[tuple[str, dict[str, str]]] = []
    for block in re.split(r"\n\s*\n", report):
        lines = [line for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        fields: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = (part.strip() for part in line.split(":", 1))
                fields.setdefault(key, value)
        sections.append((lines[0].strip(), fields))
    return sections


def audio_tracks(report: str) -> list[AudioTrack]:
    """IFO 中的音轨（报告里 VOB 在前、IFO 在后）。"""
    current = ""
    by_file: dict[str, list[AudioTrack]] = {}
    for name, fields in _sections(report):
        if name == "General":
            current = fields.get("Complete name", current)
            by_file.setdefault(current, [])
        elif re.fullmatch(r"Audio(?: #\d+)?", name):
            by_file.setdefault(current, []).append(
                AudioTrack(fields.get("Language"), fields.get("Format"), fields.get("Channel(s)"))
            )
    ifo = [tracks for file, tracks in by_file.items() if file.upper().endswith(".IFO")]
    return ifo[-1] if ifo else [t for tracks in by_file.values() for t in tracks]


def added_dub_warning(report: str) -> str | None:
    """两条以上俄语音轨、同时还有别的语言（原声）的：多半是给外国片加了俄语配音的 Custom 盘。"""
    tracks = [t for t in audio_tracks(report) if t.language]
    russian = [t for t in tracks if t.language and t.language.casefold() in ("russian", "русский")]
    others = [t for t in tracks if t not in russian]
    if len(russian) >= 2 and others:
        languages = "、".join(sorted({t.language for t in others if t.language}))
        return (f"有 {len(russian)} 条俄语音轨，另有原声（{languages}）：多半是给外国片加了俄语配音的 Custom 盘，"
                "发布前请确认（PTP、BHD 都只收未改动的原盘）")
    return None


LONG_DVD5_MINUTES = 110
"""2026 年 10 月用 rutor 上的单张 DVD5 电影统计（片长取发布页的“Продолжительность”）：110 分钟以上的，
标题写明压缩过（сжатый）的 67 个，普通的 29 个；更短的普通盘占多数。只能说明“值得留意”：俄罗斯正版盘
也常把长片放在单层盘上。界限先用 110 分钟，积累了真实的盘再调。"""


def long_dvd5_note(media_type: str, title_seconds: float | None) -> str | None:
    """主片很长却放在一张 DVD5 上：可能是从 DVD9 压缩过来的（说明性提醒，不是警告）。"""
    if media_type != "DVD5" or not title_seconds or title_seconds < LONG_DVD5_MINUTES * 60:
        return None
    return (f"主片 {round(title_seconds / 60)} 分钟，放在一张 DVD5 上，可能是从 DVD9 压缩过的（сжатый），"
            "请对照这部片正版 DVD 的信息确认。俄罗斯正版盘也常把长片放在单层盘上，这条只是提醒")
