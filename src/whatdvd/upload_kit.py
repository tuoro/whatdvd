"""发种清单：每个启用的站点一份，手动发种时照着填。

- 能不能发：按查重结果（站点上已有这张盘、已有同格式同制式的盘、还没有）和处理时的提示给出判断；
- 表单字段：按站点的要求填好；
- 发布说明：按站点的格式（UNIT3D 同 Upload-Assistant：VOB 的 MediaInfo 放进 spoiler，截图为缩略图链接原图）；
- MediaInfo：UNIT3D 的 MediaInfo 栏填 IFO 的（同 Upload-Assistant），VOB 的放在发布说明里。

目前适配 UNIT3D 标准接口的站点（Blutopia 等）；PTP、BHD 之后再做。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .mediainfo import split_report
from .release_names import unit3d_title

SUPPORTED_KINDS = frozenset({"unit3d"})


@dataclass(frozen=True)
class KitDisc:
    name: str
    vob: str
    """截图和 VOB MediaInfo 用的 VOB 文件名，例如 VTS_01_1.VOB。"""
    report: str
    """whatdvd 生成的 MediaInfo（VOB 在前、IFO 在后）。"""
    shots: list[tuple[str, str]] = field(default_factory=list)
    """上传成功的截图：（直链，图床页面）。"""
    warnings: list[str] = field(default_factory=list)


def _verdict(dupes: dict[str, Any] | None, discs: list[KitDisc], imdb_id: str | None) -> dict[str, Any]:
    """level："ok" 可以发、"check" 需要确认、"no" 不建议发；reasons 为原因。"""
    no: list[str] = []
    check: list[str] = []
    if not imdb_id:
        check.append("没有 IMDb 编号：请先在来源页选好片名")
    if dupes is None:
        check.append("没有查重")
    elif dupes.get("error"):
        check.append(f"查重没有结果：{dupes['error']}")
    else:
        items = dupes.get("items", [])
        exact = [i for i in items if i.get("size_match") == "exact"]
        near = [i for i in items if i.get("size_match") == "near"]
        same = [i for i in items if i.get("same") and i.get("size_match") is None]
        if exact:
            no.append(f"站点上已经有这张盘（大小完全相同）：{exact[0]['title']}")
        if near:
            check.append(f"站点上有大小只差一点的盘，可能就是这张：{near[0]['title']}")
        if same:
            check.append(f"站点上已有同格式、同制式的 DVD（{len(same)} 个）：请按站点规则判断能否共存或替换")
    for disc in discs:
        check += [f"{disc.name}：{w}" for w in disc.warnings]
    level = "no" if no else "check" if check else "ok"
    return {"level": level, "reasons": no + check}


def _shots_bbcode(shots: list[tuple[str, str]], per_row: int = 2, width: int = 350) -> str:
    rows = []
    for start in range(0, len(shots), per_row):
        rows.append(" ".join(f"[url={page}][img={width}]{direct}[/img][/url]" for direct, page in shots[start:start + per_row]))
    return "\n".join(rows)


def unit3d_description(discs: list[KitDisc]) -> str:
    """同 Upload-Assistant 给 UNIT3D 站点的 DVD 写法：每张盘一个 VOB MediaInfo 的 spoiler，再接截图缩略图。"""
    parts = []
    for disc in discs:
        vob, _ = split_report(disc.report)
        block = [f"[center][spoiler={disc.vob}][code]{vob.rstrip()}[/code][/spoiler][/center]"]
        if len(discs) > 1:
            block.insert(0, f"[center][b]{disc.name}[/b][/center]")
        if disc.shots:
            block.append(f"[center]{_shots_bbcode(disc.shots)}[/center]")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _tmdb_id(url: str | None) -> str | None:
    match = re.search(r"themoviedb\.org/(?:movie|tv)/(\d+)", url or "")
    return match[1] if match else None


def unit3d_kit(
    site: Any, names: dict[str, Any] | None, title: dict[str, Any] | None, params: dict[str, Any],
    discs: list[KitDisc], dupes: dict[str, Any] | None,
) -> dict[str, Any]:
    """Blutopia 等 UNIT3D 站点的发种清单。site 为 SiteConfig；names 为 BHD 标题那一组信息（制式、格式、音轨）。"""
    standard = names.get("standard") if names else None
    kind = names.get("kind", "") if names else ""
    fields: list[dict[str, str]] = []
    if title and names:
        name = unit3d_title(
            title=title["title"], original_title=title.get("original_title", ""),
            original_language=title.get("original_language", ""), year=title.get("year"), standard=standard, kind=kind,
            audio=names.get("audio"), region=params.get("region", ""), edition=params.get("edition", ""),
        )
        fields.append({"label": "名称", "value": name, "hint": "同站上已有 DVD 的写法：英文名 [AKA 原名] 年份 [版本] [地区] 制式 格式 音轨"})
    else:
        fields.append({"label": "名称", "value": "", "hint": "先在来源页选好片名再处理，才能给出名称"})
    fields += [
        {"label": "分类", "value": "Movie", "hint": "剧集选 TV"},
        {"label": "类型", "value": "Full Disc", "hint": "DVD 原盘在 UNIT3D 上算 Full Disc，不区分 DVD5、DVD9"},
        {"label": "分辨率", "value": {"PAL": "576i", "NTSC": "480i"}.get(standard or "", ""),
         "hint": "PAL 选 576i，NTSC 选 480i"},
    ]
    imdb_id = names.get("imdb_id") if names else None
    fields.append({"label": "IMDb", "value": imdb_id or "", "hint": ""})
    tmdb = _tmdb_id(names.get("tmdb_url") if names else None)
    fields.append({"label": "TMDB", "value": tmdb or "",
                   "hint": "" if tmdb else "片名来自 IMDb 数据集时没有 TMDB 编号：在 themoviedb.org 按片名查，或让站点按 IMDb 自动匹配"})
    if params.get("region"):
        fields.append({"label": "地区", "value": params["region"], "hint": "站点的地区下拉框中选同一个"})
    _, ifo = split_report(discs[0].report) if discs else ("", None)
    return {
        "site": site.id, "name": site.name, "kind": site.kind, "supported": True,
        "verdict": _verdict(dupes, discs, imdb_id),
        "fields": fields,
        "mediainfo": (ifo or (discs[0].report if discs else "")).rstrip(),
        "mediainfo_hint": "MediaInfo 栏填 IFO 的（同 Upload-Assistant），VOB 的已经放在发布说明里",
        "description": unit3d_description(discs),
        "torrent_hint": "UNIT3D 站点会改写上传的种子：上传后从站点下载种子，再用它做种。",
        "announce_set": bool(site.announce),
    }


def build_kits(
    sites: Any, names: dict[str, Any] | None, title: dict[str, Any] | None, params: dict[str, Any],
    discs: list[KitDisc], dupes: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """每个启用的站点一份发种清单；还没适配的站点类型写明。"""
    by_site = {d["site"]: d for d in dupes or []}
    kits = []
    for site in sites:
        if not site.enabled:
            continue
        if site.kind == "unit3d":
            kits.append(unit3d_kit(site, names, title, params, discs, by_site.get(site.id)))
        else:
            kits.append({"site": site.id, "name": site.name, "kind": site.kind, "supported": False})
    return kits
