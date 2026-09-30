"""命令行和 Web 共用的处理流程：识别 → 截图与 MediaInfo → 上传 → 发布说明。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .dvd import ScanError
from .naming import clean_title
from .pipeline import Analysis, DiscOutput, analyze, generate
from .post import DEFAULT_TEMPLATE, PostDisc, render_post
from .probe import ProbeError
from .runner import CommandError, Runner
from .sources import find_sources, is_iso, open_disc
from .upload import ImageHost, UploadResult, upload_all

MEDIA_TOOLS = ("ffmpeg", "ffprobe", "mediainfo")


class Reporter(Protocol):
    def info(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...


class WorkflowError(RuntimeError):
    """整个任务无法开始，例如缺少外部命令或路径里没有 DVD。"""


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{size} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} GiB"


def output_title(path: Path) -> str:
    """输出文件名前缀：输入路径最后一级（ISO 去掉扩展名），清理后使用。"""
    path = path.absolute()
    return clean_title(path.stem if path.is_file() else path.name)


def check_tools(runner: Runner, tools: tuple[str, ...] | list[str]) -> None:
    missing = [tool for tool in tools if runner.which(tool) is None]
    if missing:
        raise WorkflowError(f"缺少外部命令：{'、'.join(missing)}，请先安装。")


@dataclass
class DiscResult:
    source: Path
    label: str
    analysis: Analysis | None = None
    output: DiscOutput | None = None
    uploads: list[UploadResult] = field(default_factory=list)
    error: str | None = None


@dataclass
class RunResult:
    discs: list[DiscResult]
    post_path: Path | None = None
    ok: bool = True


@dataclass(frozen=True)
class RunOptions:
    output_dir: Path
    count: int = 10
    temp_dir: Path | None = None
    generate: bool = True
    """False 时只识别，不生成截图和 MediaInfo（scan 命令）。"""
    upload: bool = True
    template: str = DEFAULT_TEMPLATE


class ClosableHost(ImageHost, Protocol):
    def close(self) -> None: ...


HostFactory = Callable[[], ClosableHost]


def describe(source: Path, analysis: Analysis) -> list[str]:
    disc = analysis.disc
    video = analysis.video
    return [
        f"[{disc.name}]",
        f"  来源：{source}",
        f"  VOB：{disc.vob.name}（{format_bytes(disc.vob.stat().st_size)}）",
        f"  IFO：{disc.ifo.name if disc.ifo else '无'}",
        f"  容量：{disc.media_type}，共 {format_bytes(disc.total_bytes)}",
        f"  制式：{analysis.standard or f'未知（高度 {video.height}）'}",
        f"  截图尺寸：{video.width}x{video.height}，PAR {video.par_text} → {analysis.size[0]}x{analysis.size[1]}",
        f"  VOB 时长：{analysis.duration} 秒",
    ]


def _process_discs(runner: Runner, path: Path, options: RunOptions, reporter: Reporter) -> list[DiscResult]:
    sources = find_sources(path)
    if any(is_iso(source) for source in sources):
        check_tools(runner, ["7z"])

    results: list[DiscResult] = []
    for source in sources:
        result = DiscResult(source=source, label=source.name if is_iso(source) else source.parent.name)
        results.append(result)
        try:
            with open_disc(runner, source, path, options.temp_dir) as disc:
                result.analysis = analyze(runner, disc)
                for line in describe(source, result.analysis):
                    reporter.info(line)
                if options.generate:
                    result.output = generate(
                        runner,
                        result.analysis,
                        count=options.count,
                        output_dir=options.output_dir,
                        progress=lambda message: reporter.info(f"  {message}"),
                    )
                    if result.output.failed:
                        reporter.error(f"  有 {len(result.output.failed)} 张截图失败。")
        except (ScanError, ProbeError, CommandError, OSError) as error:
            result.error = str(error)
            reporter.error(f"[{result.label}] 失败：{error}")
    return results


def _upload_and_post(
    discs: list[DiscResult], options: RunOptions, path: Path, reporter: Reporter, host_factory: HostFactory
) -> Path | None:
    """截图全部上传成功才生成发布说明。"""
    done = [d for d in discs if d.output is not None and d.analysis is not None]
    if not done:
        return None
    reporter.info("[上传截图到图床]")
    host = host_factory()
    try:
        for disc in done:
            assert disc.output is not None
            disc.uploads = upload_all(
                host,
                [shot.path for shot in disc.output.shots if shot.ok],
                lambda message: reporter.info(f"  {message}"),
            )
    finally:
        host.close()

    if not all(upload.image for disc in done for upload in disc.uploads):
        reporter.error("有截图上传失败，未生成发布说明。请重新运行。")
        return None
    post_discs = [
        PostDisc(
            name=disc.analysis.disc.name,
            mediainfo=disc.output.mediainfo.read_text(encoding="utf-8"),
            image_urls=[upload.image.direct_url for upload in disc.uploads if upload.image],
        )
        for disc in done
        if disc.analysis is not None and disc.output is not None
    ]
    post_path = options.output_dir / f"{output_title(path)}.post.txt"
    post_path.write_text(render_post(post_discs, options.template), encoding="utf-8")
    reporter.info(f"发布说明：{post_path}")
    return post_path


def run(runner: Runner, path: Path, options: RunOptions, reporter: Reporter, host_factory: HostFactory) -> RunResult:
    """处理一个输入路径下的所有盘。无法开始时抛出 WorkflowError。"""
    check_tools(runner, MEDIA_TOOLS)
    try:
        discs = _process_discs(runner, path, options, reporter)
    except ScanError as error:
        raise WorkflowError(str(error)) from None

    result = RunResult(discs=discs)
    result.ok = all(
        d.error is None and (d.output is None or not d.output.failed) for d in discs
    )
    if options.generate and options.upload:
        result.post_path = _upload_and_post(discs, options, path, reporter, host_factory)
        result.ok = result.ok and result.post_path is not None
    return result
