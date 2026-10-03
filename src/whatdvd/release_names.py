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
_YEAR_RANGE = re.compile(r"(?<![\d.])(19\d{2}|20\d{2})\s*[-–]\s*(?:19|20)\d{2}(?!\d)")
_BRACKETED_YEAR = re.compile(r"[(\[]\s*(19\d{2}|20\d{2})(?!\d)")
_NOISE = re.compile(
    r"(?i)\b(?:\d\s*x\s*)?dvd-?\s?[59]\b|\bdvd\b|\bpal\b|\bntsc\b|\bcustom\b|\bvideo_ts\b|\biso\b|\bdisc\s*\d+\b"
)


# 季、集、碟号和 3D 标记（不是片名的一部分）："S1E8 of 216"、"S2E20-22"、"- E2 -"、"Выпуск 7"、"ДИСКИ 12 и 13 из 13"、
# "+ диск допами"
_EPISODE = re.compile(
    r"\bS\d{1,2}(?:-\d{1,2})?(?:\s*E\d+(?:-\d+)?)?(?:\s+of\s+\d+)?\b|(?<![\w-])E\d{1,3}(?:-\d+)?\b|\bвыпуск\s*\d+|"
    r"\bдиск(?:и|а)?\s+[\d\sи,-]+из\s+\d+|\+\s*диск\b.*$",
    re.IGNORECASE,
)

# NFKD 拆不开的字母
_FOLD = str.maketrans({"ł": "l", "đ": "d", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i", "þ": "th"})
# 单独的罗马数字当作阿拉伯数字："Dva kapitana II" 和 "Два капитана 2" 能对上
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}


def normalize(name: str) -> str:
    """查找用的片名：小写、ё 当作 е、去掉变音符号和标点。"Terminator 2: Judgment Day" → "terminator 2 judgment day"。"""
    text = unicodedata.normalize("NFKD", name.casefold().replace("ё", "е").translate(_FOLD))
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("&", " and ")
    return " ".join(_ROMAN.get(word, word) for word in re.sub(r"[^\w]+", " ", text).split())


def guess_queries(text: str) -> tuple[list[str], int | None]:
    """从文件夹名或种子标题猜搜索词（标题里用 " / " 隔开的每个名字，拉丁字母的在前）和年份。

    "Одиночное плавание / Im Alleingang / Solo Voyage (1985) DVD9" → (["Im Alleingang", "Solo Voyage", "Одиночное плавание"], 1985)
    """
    if " " not in text.strip():
        text = re.sub(r"[._]+", " ", text)
    # 括号里的年份最可靠（"2001: A Space Odyssey (1968)"），否则取最后一个
    bracketed = _BRACKETED_YEAR.search(text)
    years = list(_YEAR.finditer(text))
    # 年份范围（kinozal："Русь изначальная - E2 - 1985-1986"）取第一个，片名在范围之前
    ranged = _YEAR_RANGE.search(text)
    found = bracketed or ranged or (years[-1] if years else None)
    year = int(found.group(1)) if found else None
    head = text[: found.start()] if found and found.start() > 0 else text
    head = re.split(r"\s*\|\s*", head)[0]
    # 单独的括号是别名或说明（"Непобедимые (Ленинградцы)"）；贴着单词的不是（"(m)eines"）
    head = re.sub(r"(?<!\S)\([^()]*\)(?!\S)|\[[^\[\]]*\]", " ", head)
    head = re.sub(r"[\[\](){}]", " ", head)
    # rutracker 有时写成 "допами/ Twin Peaks"：斜杠一边有空格就算分隔
    parts = []
    for part in re.split(r"\s+/\s*|\s*/\s+", head):
        part = re.sub(r"\s+", " ", _NOISE.sub(" ", _EPISODE.sub(" ", part)))
        part = re.sub(r"(?:\s*\.){2,}", ".", part).strip(" -–_.,+")  # 去掉 "Выпуск 7" 后留下的 ". ."
        if part:
            parts.append(part)
    has_latin = any(re.search(r"[A-Za-z]", p) and not re.search(r"[А-Яа-яЁё]", p) for p in parts)
    segments = []
    for part in parts:
        # kinozal 不用斜杠："Дюплекс Duplex"、"BBC: Жизнь в микромире Life in the undergrowth"：俄文名后面跟着原名。
        # 已经有斜杠分开的拉丁字母名字时不拆（"Гангста Love / Rob the Mob"、"Часть II / The Hunger Games"）
        mixed = None if has_latin else re.fullmatch(r"(.*[А-Яа-яЁё][^A-Za-z]*?)\s+([A-Za-z][^А-Яа-яЁё]*)", part)
        if mixed is None:
            segments.append(part)
            continue
        russian, original = mixed[1], mixed[2]
        # 俄文名末尾的罗马数字归俄文名："Частная жизнь Генриха VIII The Private Life Of Henry VIII"
        # （单个 I 不算：“I Take This Woman”、“I Vampiri”）
        if numeral := re.match(r"([IVXLC]{2,})\s+(?=\S)", original):
            russian, original = f"{russian} {numeral[1]}", original[numeral.end():]
        if len(re.findall(r"[A-Za-z]", original)) >= 3 and not re.fullmatch(r"[IVXLC]+", original.strip()):
            # 原名以数字开头时，数字被分到了俄文名末尾（"В 3:10 на Юму 3:10 to Yuma"、"99 Ривер стрит 99 River Street"）；
            # 俄文名本身也可能以数字结尾（"Терминатор 2 Terminator 2"），所以带数字、不带数字的都试
            if number := re.search(r"(?<=\s)(\d[\d:.,/½]*)$", russian.strip()):
                segments.append(f"{number[1]} {original.strip(' -–_.,')}")
            segments += [original.strip(" -–_.,"), russian.strip(" -–_.,")]
        else:
            segments.append(part)
    latin = [s for s in segments if re.search(r"[A-Za-z]", s) and not re.search(r"[А-Яа-яЁё]", s)]
    queries = list(dict.fromkeys([*latin, *(s for s in segments if s not in latin)]))
    others = [s for s in segments if s not in latin]
    if len(others) > 1:  # "Энтузиазм / Симфония Донбасса"：IMDb 上是 "Entuziazm: Simfoniya Donbassa"
        queries.append(" ".join(others))
    return queries or [head.strip()], year


def guess_query(text: str) -> tuple[str, int | None]:
    """从文件夹名或种子标题猜 TMDB 搜索词和年份（第一个名字，拉丁字母的优先）。

    "Изумрудный лес / The Emerald Forest (1985) DVD9 | P" → ("The Emerald Forest", 1985)
    "The.Emerald.Forest.1985.PAL.DVD9" → ("The Emerald Forest", 1985)
    """
    queries, year = guess_queries(text)
    return queries[0], year


# 俄文转写成 IMDb 的写法（IMDb 的苏联、俄罗斯片名大多是这种拉丁字母转写）："Долгая дорога в дюнах" → "Dolgaya doroga v dyunakh"
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
})


def transliterate(text: str) -> str:
    return text.lower().translate(_TRANSLIT)


def search_variants(query: str) -> list[str]:
    """按片名在 IMDb 数据集中查找时依次尝试的写法：原样、俄文转写、去掉副标题（". Special Edition"、": ……"）。"""
    variants = [query]
    if re.search(r"[А-Яа-яЁёІіЇїЄєҐґ]", query):
        variants.append(transliterate(query))
    for text in list(variants):  # "Up Denali 3D"：IMDb 上没有 3D（"Step Up 3D" 有，所以先查原样的）
        plain = re.sub(r"\s*(?<!\w)3[\s-]?[DД](?!\w)", "", text).strip()
        if plain != text and len(plain) >= 3:
            variants.append(plain)
    for text in list(variants):
        short = re.split(r"\s*[.:]\s+", text, maxsplit=1)[0]  # 不按 " - " 切：那通常是“艺人 - 专辑”
        if short != text and len(short) >= 3:
            variants.append(short)
    return list(dict.fromkeys(v.strip() for v in variants if v.strip()))


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


def folder_reflects_title(folder: str, titles: Sequence[str]) -> bool:
    """文件夹名是否已经看得出片名（PTP《Site Policies About Modifying Files》：文件夹名要清楚写出片名，
    不必完全一致，MONTY_PYTHON_HOLY_GRAIL 可以、MPHGRAIL 不行；只有缩写得看不出或不是原名、英文名时才改名）。

    titles 为 IMDb 的英文名、原名等。文件夹名中的点、下划线当作空格，俄文文件夹名再按 IMDb 的写法转写一次
    （俄语片 IMDb 的原名是转写，“Иди и смотри” 即原名 “Idi i smotri”）。
    """
    spaced = re.sub(r"[._]+", " ", folder)
    candidates = {normalize(spaced), normalize(transliterate(spaced))}
    for title in titles:
        wanted = normalize(title)
        if not wanted:
            continue
        for name in candidates:
            padded = f" {name} "
            at = padded.find(f" {wanted} ")
            if at >= 0:
                following = padded[at + len(wanted) + 2 :].split()[:1]
                if not (following and following[0].isdigit() and len(following[0]) <= 2):  # "Predator 2" 不是 "Predator"
                    return True
            # 转写方式不同（"Juriev den" 和 "Yurev den"）：多个词的片名开头部分足够相近也算
            if " " in wanted and len(wanted) >= 6 and any(
                SequenceMatcher(None, wanted, name[: len(wanted) + extra]).ratio() >= 0.8 for extra in (-2, -1, 0, 1, 2)
            ):
                return True
    return False


def unit3d_title(
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
    """UNIT3D 站点（Blutopia 等）的 DVD 标题，同 Upload-Assistant 和站上已有的写法：
    "Come and See AKA Idi i smotri 1985 PAL 2xDVD9 DD 5.1"。和 BHD 不同：没有 MPEG-2，DD 和声道之间有空格。"""
    parts = [title]
    if needs_aka(title, original_title, original_language):
        parts += ["AKA", original_title]
    parts += [str(year)] if year else []
    unit3d_audio = re.sub(r"^DD(?=\d)", "DD ", audio) if audio else ""
    parts += [edition.strip(), region.strip(), standard or "", kind, unit3d_audio]
    return " ".join(p for p in parts if p)
