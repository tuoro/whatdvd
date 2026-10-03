"""Jackett（Torznab）搜索与 DVD 原盘过滤。

标题写法按 rutor / rutracker / kinozal 的实际情况：
"Название / Title (2002) DVD9 | P -Custom"、"2 х DVD9"、"DVD9+DVD5"、"DVD5 | P2-сжатый"。
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from .dvd import DVD5_MAX_BYTES

DVD9_MAX_BYTES = 8_543_666_176
"""双层 DVD 的容量。"""

_TORZNAB = "{http://torznab.com/schemas/2015/feed}attr"


class IndexerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    guid: str
    indexer: str
    title: str
    size: int
    published: float | None
    details_url: str | None
    download_url: str | None
    magnet: str | None
    info_hash: str | None
    seeders: int | None
    indexer_id: str = ""
    categories: tuple[int, ...] = ()
    """Torznab 分类（标准分类和站点自定义分类）。"""


# 只要影视类时向 Jackett 请求的 Torznab 分类：电影、电视（剧集、动画、纪录片）、其他。
# 带上“其他”是因为 Jackett 的 rutor 不区分分类，所有结果都标为 8000；
# 其他站点标为“其他”的（rutracker 的游戏附赠盘、音色库等）在 is_film 中去掉。
FILM_QUERY_CATEGORIES = "2000,5000,8000"
TV_SPORT = 5060
UNCATEGORIZED_INDEXERS = frozenset({"rutor"})
"""Jackett 中不区分分类的站点（定义文件里写明 RuTor 的搜索结果页不显示分类）。"""


# 站点自己的分类（Jackett 中 100000 以上，各站点不同）：kinozal 把演唱会、体育、戏剧歌剧芭蕾、综艺节目
# 也归在“电影”（2000）下，只能按它自己的分类排除
SITE_NON_FILM = {
    "kinozal": frozenset({
        100048,  # Movies - Concerts
        100037,  # Movies - Sport
        100038,  # Movies - Theatre, Opera, Ballet
        100049,  # Movies - Shows / TV Shows
        100050,  # Movies - TV Show Mir
    }),
}


def is_film(release: Release) -> bool:
    """影视类：电影（2000–2999）或电视（5000–5999，体育 5060 除外），去掉站点自己标为演唱会、体育等的。
    不区分分类的站点全部保留。"""
    return film_categories(release.indexer_id, release.categories)


def film_categories(indexer_id: str, categories: Sequence[int]) -> bool:
    """同 is_film，用保存下来的站点 id 和分类判断。"""
    if indexer_id in UNCATEGORIZED_INDEXERS:
        return True
    for site, excluded in SITE_NON_FILM.items():  # Jackett 中的 id："kinozal"、"kinozal-magnet"
        if indexer_id.startswith(site) and excluded.intersection(categories):
            return False
    return any(2000 <= c < 3000 or (5000 <= c < 6000 and c != TV_SPORT) for c in categories)


# Jackett 的 "Add RUSSIAN to end of all titles"（kinozal 默认开着）在标题末尾加的标记，给 Sonarr / Radarr 用
_LANGUAGE_SUFFIX = re.compile(r"\s+-\s+RUSSIAN$|\s+RUS$")


def _strip_language_suffix(title: str) -> str:
    return _LANGUAGE_SUFFIX.sub("", title)


def parse_torznab(text: str) -> list[Release]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise IndexerError(f"Jackett 返回的内容无法解析：{error}") from None
    if root.tag == "error":
        raise IndexerError(f"Jackett 返回错误：{root.get('description') or root.get('code')}")
    channel_title = root.findtext("channel/title") or ""
    releases = []
    for item in root.iter("item"):
        attrs = {a.get("name"): a.get("value") for a in item.iter(_TORZNAB)}
        indexer = item.find("jackettindexer")
        published = None
        if date := item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(date).timestamp()
            except (TypeError, ValueError):
                published = None
        enclosure = item.find("enclosure")
        seeders = attrs.get("seeders")
        releases.append(
            Release(
                guid=item.findtext("guid") or item.findtext("link") or item.findtext("title") or "",
                indexer=(indexer.text if indexer is not None and indexer.text else channel_title),
                title=_strip_language_suffix((item.findtext("title") or "").strip()),
                size=int(item.findtext("size") or 0),
                published=published,
                details_url=item.findtext("comments"),
                download_url=item.findtext("link") or (enclosure.get("url") if enclosure is not None else None),
                magnet=attrs.get("magneturl"),
                info_hash=(attrs.get("infohash") or "").lower() or None,
                seeders=int(seeders) if seeders and seeders.isdigit() else None,
                indexer_id=(indexer.get("id") or "") if indexer is not None else "",
                categories=tuple(
                    int(c.text) for c in item.findall("category") if c.text and c.text.strip().isdigit()
                ),
            )
        )
    return releases


class Jackett:
    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        indexer: str = "all",
        films_only: bool = False,
        timeout: float = 120.0,
        delay: float = 0.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """delay：两次搜索之间至少间隔多少秒。全面搜索会连续搜几百次，
        kinozal 等站点对搜索频率有限制，太快会被暂时封禁。"""
        self._delay = delay
        self._last_search = 0.0
        self._base = url.rstrip("/")
        self._api_key = api_key
        self._indexer = indexer
        self._films_only = films_only
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def search(self, query: str) -> list[Release]:
        url = f"{self._base}/api/v2.0/indexers/{quote(self._indexer, safe='')}/results/torznab/api"
        try:
            wait = self._last_search + self._delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            params = {"apikey": self._api_key, "t": "search", "q": query}
            if self._films_only:  # Jackett 按分类分别向站点搜索，每个分类各有一份条数上限
                params["cat"] = FILM_QUERY_CATEGORIES
            response = self._client.get(url, params=params)
        except httpx.HTTPError as error:
            raise IndexerError(f"连不上 Jackett：{error or type(error).__name__}") from error
        finally:
            self._last_search = time.monotonic()
        if not response.is_success:
            if response.text.lstrip().startswith("<"):
                parse_torznab(response.text)  # Torznab 的 <error> 在这里抛出
            try:
                detail = response.json().get("error")  # 例如 {"error": "Unknown indexer: nosuch"}
            except (ValueError, AttributeError):
                detail = None
            raise IndexerError(f"Jackett 返回错误：{detail or f'HTTP {response.status_code}'}")
        releases = parse_torznab(response.text)
        return [r for r in releases if is_film(r)] if self._films_only else releases

    def indexers(self) -> list[tuple[str, str]]:
        """Jackett 中已配置的站点：[(ID, 名称)]。也用来测试地址和 API Key 是否正确。"""
        url = f"{self._base}/api/v2.0/indexers/all/results/torznab/api"
        try:
            response = self._client.get(url, params={"apikey": self._api_key, "t": "indexers", "configured": "true"})
        except httpx.HTTPError as error:
            raise IndexerError(f"连不上 Jackett：{error or type(error).__name__}") from error
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            raise IndexerError(f"Jackett 返回 HTTP {response.status_code}，内容无法识别") from None
        if root.tag == "error":
            raise IndexerError(f"Jackett 返回错误：{root.get('description') or root.get('code')}")
        return [(item.get("id") or "", item.findtext("title") or item.get("id") or "") for item in root.iter("indexer")]

    def fetch(self, download_url: str) -> bytes | str:
        """通过 Jackett 下载种子文件；站点只给磁力链接时 Jackett 会重定向，返回磁力链接字符串。"""
        try:
            response = self._client.get(download_url, follow_redirects=False)
            if response.is_redirect:
                location = str(response.headers.get("location", ""))
                if location.startswith("magnet:"):
                    return location
                response = self._client.get(download_url, follow_redirects=True)
        except httpx.HTTPError as error:
            raise IndexerError(f"下载种子失败：{error or type(error).__name__}") from error
        if not response.is_success or not response.content.startswith(b"d"):
            raise IndexerError(f"下载种子失败：HTTP {response.status_code}")
        return response.content


# ---------- 过滤 ----------

# 西里尔字母中和拉丁字母长得一样的，标题里常混用（"2 х DVD9"、"Р2"、"А"）
_LOOKALIKES = str.maketrans("хХРрАаОоСсДдЛл", "xXPpAaOoCcDdLl")

_DISCS = re.compile(r"(?<![\d.])(?:(\d{1,2})\s*[x×]?\s*)?DVD-?\s?([59])(?!\d)", re.IGNORECASE)

# 删掉了菜单或花絮的盘（PTP、BHD 只收未改动的原盘）："без меню"、"без доп. материалов"、"только фильм"、"Movie only"。
# 不含 "Доп. материалы: нет" 这类字段：那是原盘本来就没有花絮
_STRIPPED = re.compile(
    r"без\s*(?:меню|доп(?![а-яё])|допов|доп\.|дополнительн|бонус)|только\s+фильм|"
    r"\b(?:no|without|w/o)[\s._-]+(?:menus?|extras)\b|\b(?:menus?|extras)[\s._-]+(?:removed|stripped)\b|\bmenu-?less\b|"
    r"(?:\bmain[\s._-]+|[(\[|]\s*)(?:movie|film|feature)[\s._-]+only\b",  # 不匹配 "Only Lovers Left Alive" 这类片名
    re.IGNORECASE,
)

_EXCLUDE = [
    (re.compile(r"custom|[кk]аст[оo]м", re.IGNORECASE), "Custom（改制过的盘）"),
    (_STRIPPED, "删掉了菜单或花絮的盘，不是完整的原盘"),
    (re.compile(r"[сc]жат", re.IGNORECASE), "压缩过的盘（сжатый）"),
    (re.compile(r"реставр", re.IGNORECASE), "修复版（Реставрация），不是原盘"),
    # rutracker 写明来源的转制盘："Betacam SP > DVD5"、"VHS > DVD9"、"LD > DVD5"
    (re.compile(r">\s*DVD", re.IGNORECASE), "从其他来源转制成的 DVD（“… > DVD”），不是原盘"),
    (
        re.compile(
            r"rip\b|remux|blu-?ray|\bbd\b|hdtv|web-?dl|\b(?:2160|1080|720)[pi]\b|x26[45]|hevc|avc\b|mkv|avi\b",
            re.IGNORECASE,
        ),
        "不是 DVD 原盘",
    ),
]

# "| D, P, P2, A, L1" 这类俄语配音标记
_DUB_CODES = re.compile(r"^(?:D|P\d?|A\d?|L\d?|O|Ж)$", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str | None = None
    """排除原因。"""
    kind: str | None = None
    """例如 "DVD9"、"2×DVD9"、"DVD9+DVD5"。"""
    discs: int = 0
    notes: list[str] = field(default_factory=list)
    """需要留意的地方（可能不是原盘、体积不对、没人做种……）。"""
    labels: list[str] = field(default_factory=list)
    """说明是原盘的正面标记（俄语原版片、原声无翻译、正版盘）。"""


def _dub_codes(title: str) -> list[str]:
    """标题中 "|" 之后的俄语配音标记。rutor 原始写法 "DVD9 | D, P | FullScreen"，
    Jackett 会改写成 "DVD9 | D, P-FullScreen"，两种都认。"""
    for segment in title.split("|")[1:]:
        segment = re.split(r"\s*-\s*", segment.translate(_LOOKALIKES), maxsplit=1)[0]
        codes = [part.strip() for part in segment.split(",") if part.strip()]
        if codes and all(_DUB_CODES.match(code) for code in codes):
            return codes
    return []


# rutracker / kinozal 的配音写法："Dub + 2x MVO + AVO + Sub Rus"、"DUB, Sub"、"MVO (Ozz)"
_VOICE = re.compile(r"(?<![A-Za-z])(\d+x\s*)?(DUB|Dub|MVO|DVO|AVO|VO)(?![A-Za-z])")


def _voice_codes(title: str) -> list[str]:
    codes: list[str] = []
    for _, code in _VOICE.findall(title):
        code = "Dub" if code.upper() == "DUB" else code
        if code not in codes:
            codes.append(code)
    return codes


def _word(word: str, title: str) -> bool:
    """单独出现的俄文缩写，前后不是西里尔字母。"""
    return re.search(rf"(?<![А-Яа-яЁё]){word}(?![А-Яа-яЁё])", title) is not None


def _looks_stripped(title: str) -> bool:
    """Jackett 的 "Strip Cyrillic Letters" 删掉俄文后，会留下 "/ /" 或几乎没有字母的标题。"""
    if re.search(r"[А-Яа-яЁё]", title):
        return False
    letters = re.sub(r"(?i)dvd-?\s?[59]|[^A-Za-z]", "", title)
    return bool(re.search(r"(^|[\s\[(])/\s+/", title)) or len(letters) < 3


_DISC_FILES = (".vob", ".ifo", ".bup", ".iso")


def inspect_contents(name: str, files: list[str]) -> str | None:
    """种子里的文件夹名和文件列表：网页标题没写、但文件夹名写着 Custom 等标记的盘（rutor 上常见，
    例如标题 "Терминатор 2 … DVD9 | P, A"，文件夹 "Terminator.2.(1991).(DVD9.CUSTOM.FS…)"），或者根本没有
    DVD 文件的种子。返回拒绝的原因；没问题时为 None。"""
    for pattern, reason in _EXCLUDE:
        if pattern.search(name):
            return f"种子文件夹名“{name}”说明是{reason}"
    if files and not any(f.lower().endswith(_DISC_FILES) for f in files):
        return f"种子“{name}”里没有 VOB、IFO 或 ISO 文件，不是 DVD 原盘"
    return None


# 发布页描述中的“发布类型 / 画质”字段（rutor、rutracker 的写法），只看这些字段的值：
# 描述里嵌的 MediaInfo 有 “CustomMatrix”“Метод сжатия”，页面下方还列着同一部片的 BDRemux，都不能算
_RELEASE_FIELD = re.compile(r"(Тип релиза|Качество видео|Качество|Release type|Quality)\s*:\s*([^:]{0,60})", re.IGNORECASE)
_FIELD_END = re.compile(r"\s+[A-ZА-ЯЁ][\w() /-]{1,40}$")  # 值后面紧跟的下一个字段名
_RELEASE_BAD = [
    (re.compile(r"custom|[кk]аст[оo]м", re.IGNORECASE), "Custom（改制过的盘）"),
    (_STRIPPED, "删掉了菜单或花絮的盘"),
    (re.compile(r"[сc]жат", re.IGNORECASE), "压缩过的盘（сжатый）"),
    (re.compile(r"реставр", re.IGNORECASE), "修复版（Реставрация）"),
    (re.compile(r"рип|rip\b|remux|ремукс|пересоб|rebuil", re.IGNORECASE), "重新压制或封装过的，不是 DVD 原盘"),
]


def _description(page: str) -> str:
    """发布页中发布者写的描述（不含评论和“其他版本”列表），去掉标签。"""
    start = page.find('id="details"')  # rutor：#details 的第一行
    if start >= 0:
        end = page.find("</tr>", page.find("<tr", start) + 1)
    else:
        start = page.find('class="post_body')  # rutracker：第一个帖子
        end = page.find('class="post_body', start + 1) if start >= 0 else -1
    if start < 0:
        start, end = 0, len(page)
    body = page[start : end if end > start else start + 50_000]
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


def release_page_issue(page: str) -> str | None:
    """发布页描述的“发布类型 / 画质”写着 Custom、сжатый、Рип 等时返回原因（标题和种子文件夹名都没写的也能发现，
    例如 “Тип релиза : DVD5 (Custom)”“Качество: DVD-5 (Custom)”）。"""
    text = _description(page)
    for match in _RELEASE_FIELD.finditer(text):
        value = match.group(2)
        if text[match.end() : match.end() + 1] == ":":  # 后面还有字段：去掉末尾的下一个字段名
            value = _FIELD_END.sub("", value)
        value = value.strip()
        for pattern, reason in _RELEASE_BAD:
            if pattern.search(value):
                return f"发布页写着“{match.group(1)}: {value}”，是{reason}"
    return None


# 原盘里混进的零散音视频流文件（PTP：这样的种子可以被替换，删掉这些文件再发）
LOOSE_STREAMS = (".h264", ".264", ".avc", ".m2v", ".mpv", ".ac3", ".eac3", ".dts", ".mpa", ".mp2", ".wav", ".pcm",
                 ".lpcm", ".sup")


def loose_streams(files: list[str]) -> list[str]:
    return [f for f in files if f.lower().endswith(LOOSE_STREAMS)]


_VTS_FILE = re.compile(r"VTS_(\d\d)_(\d)\.(VOB|IFO|BUP)", re.IGNORECASE)
_VMG_FILE = re.compile(r"VIDEO_TS\.(VOB|IFO|BUP)", re.IGNORECASE)


@dataclass(frozen=True)
class Structure:
    """按种子里的文件列表看盘的结构：refuse 为拒绝推送的原因，notes 为只提示的说明。"""

    refuse: str | None = None
    notes: tuple[str, ...] = ()


def _gb(size: int) -> str:
    return f"{size / 1e9:.2f} GB"


def disc_structure(files: list[tuple[str, int]], title: str = "") -> Structure:
    """files 为（相对路径，大小）。每个含 VIDEO_TS / VTS 文件的目录算一张盘：

    - 拒绝：缺 VIDEO_TS.IFO（每张 DVD 都必须有）、标题集缺 IFO、标题 VOB 编号中间缺号（VTS_01_1、VTS_01_3），
      一张盘的大小超过 DVD9 的容量；
    - 提示：标着 DVD9、只有一张盘却放得进 DVD5 的（可能压缩过或删掉了部分内容）。
    ISO 只看大小。2026 年 10 月 rutor 上随机 800 个 DVD 种子：拒绝 1 个（只有 VOB、没有任何 IFO），
    缺 BUP 的 0 个；没有 VIDEO_TS.VOB 的 196 个（正版盘常见，不算）。
    """
    discs: dict[str, tuple[str, dict[str, int]]] = {}  # 目录（不分大小写）→（原样的目录名，{文件名: 大小}）
    for path, size in files:
        parent, _, name = path.replace("\\", "/").rpartition("/")
        if _VTS_FILE.fullmatch(name) or _VMG_FILE.fullmatch(name) or name.lower().endswith(".iso"):
            key = path if name.lower().endswith(".iso") else parent
            discs.setdefault(key.casefold(), (key, {}))[1][name.upper()] = size
    sizes = []
    for key, names in discs.values():
        sizes.append(sum(names.values()))
        if key.lower().endswith(".iso"):
            continue
        where = f"“{key}”" if key else "根目录"
        if "VIDEO_TS.IFO" not in names:
            return Structure(f"{where}里没有 VIDEO_TS.IFO（每张 DVD 都必须有）：盘不完整或改动过")
        sets: dict[str, set[int]] = {}
        for name in names:
            if match := _VTS_FILE.fullmatch(name):
                parts = sets.setdefault(match[1], set())
                if match[3].upper() == "VOB" and match[2] != "0":
                    parts.add(int(match[2]))
        for number, parts in sorted(sets.items()):
            if f"VTS_{number}_0.IFO" not in names:
                return Structure(f"{where}里有标题集 {number} 的文件，却没有 VTS_{number}_0.IFO：盘不完整")
            if parts and sorted(parts) != list(range(1, max(parts) + 1)):
                missing = sorted(set(range(1, max(parts) + 1)) - parts)
                return Structure(f"{where}里缺少 VTS_{number}_{missing[0]}.VOB：盘不完整")
    if any(size > DVD9_MAX_BYTES for size in sizes):
        return Structure(f"一张盘有 {_gb(max(sizes))}，超过 DVD9 的容量：不是原盘")
    notes = []
    claimed = {layer for _, layer in _DISCS.findall(title.translate(_LOOKALIKES))}
    # 只看单张盘：多碟合集常把每张都放得进 DVD5 的盘统称 DVD9（rutor 抽样的 800 个种子中这样的 2 个都是合集）
    if len(sizes) == 1 and claimed == {"9"} and sizes[0] <= DVD5_MAX_BYTES:
        notes.append(f"标题写的是 DVD9，盘却只有 {_gb(max(sizes))}，放得进 DVD5：可能压缩过或删掉了部分内容，请确认")
    return Structure(notes=tuple(notes))


def classify(title: str, size: int, seeders: int | None = None) -> Verdict:
    for pattern, reason in _EXCLUDE:
        if pattern.search(title):
            return Verdict(accepted=False, reason=reason)

    # 写明数量的累加（"2 x DVD-9"、"2 DVD9 1DVD5"）；没写数量的同一种盘只算一张：
    # rutracker 的标题常把 "[DVD9]" 写两遍（原名和译名各一次）
    explicit = {5: 0, 9: 0}
    mentioned = {5: False, 9: False}
    for count, layer in _DISCS.findall(title.translate(_LOOKALIKES)):
        if count and int(count) > 0:  # "0 DVD9" 不是 0 张盘
            explicit[int(layer)] += int(count)
        else:
            mentioned[int(layer)] = True
    counts = {layer: explicit[layer] or int(mentioned[layer]) for layer in (5, 9)}
    discs = counts[5] + counts[9]
    if discs == 0:
        return Verdict(accepted=False, reason="标题中没有 DVD5 / DVD9")
    parts = [f"{n}×DVD{layer}" if n > 1 else f"DVD{layer}" for layer, n in ((9, counts[9]), (5, counts[5])) if n]
    kind = "+".join(parts)

    notes = []
    capacity = counts[9] * DVD9_MAX_BYTES + counts[5] * DVD5_MAX_BYTES
    if size > capacity:
        notes.append(f"体积超出 {kind} 的容量，可能是合集或标错了")
    if codes := _dub_codes(title) or _voice_codes(title):
        notes.append(f"带俄语配音标记（{', '.join(codes)}），可能加过音轨，需确认是不是原盘")
    if _looks_stripped(title):
        notes.append("标题中的俄文像是被 Jackett 删掉了（Strip Cyrillic Letters），片名和部分过滤标记会丢失")
    labels = []
    if _word("РУ", title):  # kinozal：俄语原版片，原声就是俄语
        labels.append("俄语原版片（РУ），没有后加配音")
    if _word("БП", title):  # kinozal：без перевода
        labels.append("原声，没有翻译（БП）")
    if re.search(r"лицензи", title, re.IGNORECASE):
        labels.append("俄罗斯正版盘（Лицензия）")
    if re.search(r"full\s*screen|pan\s*scan", title, re.IGNORECASE):
        notes.append("全屏 / Pan & Scan 版本")
    if seeders == 0:
        notes.append("目前没有做种者")
    return Verdict(accepted=True, kind=kind, discs=discs, notes=notes, labels=labels)
