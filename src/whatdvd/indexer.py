"""Jackett（Torznab）搜索与 DVD 原盘过滤。

标题写法按 rutor / rutracker / kinozal 的实际情况：
"Название / Title (2002) DVD9 | P -Custom"、"2 х DVD9"、"DVD9+DVD5"、"DVD5 | P2-сжатый"。
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
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


def is_film(release: Release) -> bool:
    """影视类：电影（2000–2999）或电视（5000–5999，体育 5060 除外）。不区分分类的站点全部保留。"""
    if release.indexer_id in UNCATEGORIZED_INDEXERS:
        return True
    return any(2000 <= c < 3000 or (5000 <= c < 6000 and c != TV_SPORT) for c in release.categories)


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

_EXCLUDE = [
    (re.compile(r"custom|кастом", re.IGNORECASE), "Custom（改制过的盘）"),
    (re.compile(r"сжат", re.IGNORECASE), "压缩过的盘（сжатый）"),
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
