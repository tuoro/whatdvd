"""按站点规则给出名字：PTP 的发种名称（最外层文件夹名）和 BHD 的标题。

- PTP 2.1.1：文件夹名要和 IMDb 的原名或英文名一致。用英文名，写成 "The.Emerald.Forest.1985.DVD9"。
- BHD 3.3 / 3.4：标题用官方英文名，原名不同时 "English Title AKA Original Title"；DVD 原盘的写法同
  Upload-Assistant：片名 [AKA 原名] 年份 [版本] [地区或发行商] PAL|NTSC DVD9 MPEG-2 音轨，
  音轨 DD 写成 DD5.1（3.4.4 的例外），其他写成 "DTS 5.1"。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from difflib import SequenceMatcher

_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_BRACKETED_YEAR = re.compile(r"[(\[]\s*(19\d{2}|20\d{2})(?!\d)")
_NOISE = re.compile(
    r"(?i)\b(?:\d\s*x\s*)?dvd-?\s?[59]\b|\bdvd\b|\bpal\b|\bntsc\b|\bcustom\b|\bvideo_ts\b|\biso\b|\bdisc\s*\d+\b"
)


def guess_query(text: str) -> tuple[str, int | None]:
    """从文件夹名或种子标题猜 TMDB 搜索词和年份。

    "Изумрудный лес / The Emerald Forest (1985) DVD9 | P" → ("The Emerald Forest", 1985)
    "The.Emerald.Forest.1985.PAL.DVD9" → ("The Emerald Forest", 1985)
    """
    if " " not in text.strip():
        text = re.sub(r"[._]+", " ", text)
    # 括号里的年份最可靠（"2001: A Space Odyssey (1968)"），否则取最后一个
    bracketed = _BRACKETED_YEAR.search(text)
    years = list(_YEAR.finditer(text))
    found = bracketed if bracketed else (years[-1] if years else None)
    year = int(found.group(1)) if found else None
    head = text[: found.start()] if found and found.start() > 0 else text
    head = re.split(r"\s*\|\s*", head)[0]
    head = re.sub(r"[\[\](){}]", " ", head)
    segments = [s.strip() for s in head.split(" / ") if s.strip()] or [head]
    latin = [s for s in segments if re.search(r"[A-Za-z]", s) and not re.search(r"[А-Яа-яЁё]", s)]
    query = (latin or segments)[0]
    query = _NOISE.sub(" ", query)
    query = re.sub(r"\s+", " ", query).strip(" -–_.,")
    return query, year


def disc_kind(media_types: Sequence[str]) -> str:
    """["DVD9"] → "DVD9"；["DVD9", "DVD9"] → "2xDVD9"；["DVD9", "DVD5"] → "DVD9+DVD5"。"""
    counts = Counter(media_types)
    parts = [f"{counts[k]}x{k}" if counts[k] > 1 else k for k in ("DVD9", "DVD5") if counts[k]]
    return "+".join(parts)


def _ascii_fold(text: str) -> str:
    """去掉变音符号（"Amélie" → "Amelie"）；其他文字保留。"""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def ptp_name(title: str, year: int | None, kind: str) -> str:
    """PTP 的发种名称："The Lord of the Rings: The Two Towers" 2002 DVD9 → "The.Lord.of.the.Rings.The.Two.Towers.2002.DVD9"。"""
    text = _ascii_fold(title).replace("&", " and ")
    text = re.sub(r"['’`]", "", text)  # "Schindler's" → "Schindlers"
    text = re.sub(r"[^\w\s.-]", " ", text)
    parts = [p for p in re.split(r"[\s._]+", text) if p.strip("-")]
    parts += [str(year)] if year else []
    parts += [kind] if kind else []
    return ".".join(parts)


def needs_aka(title: str, original_title: str, original_language: str) -> bool:
    """原名和英文名差别够大时才加 AKA（同 Upload-Assistant：相似度 0.7 以下、不是英文名的一部分）。"""
    if not original_title or original_language == "en":
        return False
    a, b = title.casefold(), original_title.casefold()
    return b not in a and SequenceMatcher(None, a, b).ratio() < 0.7


_CODECS = {"AC-3": "DD", "E-AC-3": "DDP", "DTS": "DTS", "MPEG Audio": "MP2", "PCM": "LPCM", "MLP FBA": "TrueHD"}


def audio_from_mediainfo(text: str) -> str | None:
    """MediaInfo 文本中第一条音轨（VOB 在前）的编码和声道："DD5.1"、"DTS 5.1"、"LPCM 2.0"。"""
    section: dict[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if section is not None and section:
                break
            continue
        if re.fullmatch(r"Audio(?: #\d+)?", stripped):
            section = {}
            continue
        if section is not None and ":" in stripped:
            key, value = (part.strip() for part in stripped.split(":", 1))
            section.setdefault(key, value)
    if not section:
        return None
    codec = _CODECS.get(section.get("Format", ""), section.get("Format", "").split()[0] if section.get("Format") else "")
    match = re.match(r"(\d+)", section.get("Channel(s)", ""))
    if not codec or not match:
        return codec or None
    count = int(match.group(1))
    layout = section.get("Channel layout", "")
    lfe = "LFE" in layout or (not layout and count == 6)
    channels = f"{count - 1}.1" if lfe and count > 1 else f"{count}.0"
    return f"DD{channels}" if codec == "DD" else f"{codec} {channels}"


def bhd_title(
    *,
    title: str,
    original_title: str = "",
    original_language: str = "",
    year: int | None,
    standard: str | None,
    kind: str,
    audio: str | None,
    region: str = "",
    edition: str = "",
) -> str:
    parts = [title]
    if needs_aka(title, original_title, original_language):
        parts += ["AKA", original_title]
    parts += [str(year)] if year else []
    parts += [edition.strip(), region.strip(), standard or "", kind, "MPEG-2", audio or ""]
    return " ".join(p for p in parts if p)
