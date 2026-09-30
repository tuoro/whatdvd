"""发布说明：把 MediaInfo 和截图直链套进模板，默认 BBCode。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template

DEFAULT_TEMPLATE = """[b]$name[/b]
[quote]
$mediainfo
[/quote]
$screenshots
"""
"""每张盘套一次模板，多张盘之间空一行。可用变量：$name、$mediainfo、$screenshots。"""


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class PostDisc:
    name: str
    mediainfo: str
    image_urls: Sequence[str]


def render_post(discs: Sequence[PostDisc], template: str = DEFAULT_TEMPLATE) -> str:
    blocks: list[str] = []
    for disc in discs:
        try:
            block = Template(template).substitute(
                name=disc.name,
                mediainfo=disc.mediainfo.strip(),
                screenshots="\n".join(f"[img]{url}[/img]" for url in disc.image_urls),
            )
        except (KeyError, ValueError) as error:
            raise TemplateError(f"模板变量有误：{error}（可用 $name、$mediainfo、$screenshots）") from None
        blocks.append(block.strip())
    return "\n\n".join(blocks) + "\n"


def load_template(path: Path | None) -> str:
    """读取并校验模板文件；None 表示用默认模板。读取失败抛 OSError，变量有误抛 TemplateError。"""
    if path is None:
        return DEFAULT_TEMPLATE
    template = path.read_text(encoding="utf-8")
    render_post([PostDisc(name="", mediainfo="", image_urls=[])], template)
    return template
