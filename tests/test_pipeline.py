"""截图流程中的剔除黑屏（同 Upload-Assistant）。ffmpeg 用假实现，按时间点写出指定大小的文件。"""

import random
from pathlib import Path

from conftest import FakeRunner, ok
from whatdvd.dvd import Disc
from whatdvd.pipeline import Analysis, generate
from whatdvd.probe import VideoInfo
from whatdvd.runner import CommandResult

BIG = 300_000
DARK = 50_000


def _analysis(tmp_path: Path, duration: int = 3000) -> Analysis:
    vob = tmp_path / "Movie" / "VIDEO_TS" / "VTS_01_1.VOB"
    disc = Disc(
        name="Movie",
        video_ts=vob.parent,
        vob=vob,
        ifo=None,
        total_bytes=1,
        mediainfo_root=tmp_path,
    )
    video = VideoInfo(width=720, height=576, par=1.422, par_text="1.422", dar=1.778)
    return Analysis(disc=disc, video=video, size=(1024, 576), duration=duration)


def _ffmpeg(sizes: dict[str, int], default: int = BIG) -> FakeRunner:
    """sizes：时间点（HH:MM:SS）→ 文件大小；大小为 0 表示截图失败。"""

    def handler(argv: tuple[str, ...]) -> CommandResult:
        if argv[0] == "ffmpeg":
            at = argv[argv.index("-ss") + 1]
            size = sizes.get(at, default)
            if size:
                Path(argv[-1]).write_bytes(b"\0" * size)
        return ok(argv, "")

    return FakeRunner(handler)


def _run(tmp_path: Path, runner: FakeRunner, count: int = 3, **kwargs: object) -> tuple[list, list[str]]:
    messages: list[str] = []
    output = generate(
        runner,
        _analysis(tmp_path),
        count=count,
        output_dir=tmp_path / "out",
        progress=lambda message, tick: messages.append(message),
        rng=random.Random(1),
        **kwargs,  # type: ignore[arg-type]
    )
    return output.shots, messages


def test_takes_one_extra_and_drops_smallest(tmp_path: Path) -> None:
    # 3000 秒，间隔 121：121 / 242 / 363 / 484，最小的是 242 秒那张
    runner = _ffmpeg({"00:04:02": 200_000, "00:02:01": 250_000})
    shots, messages = _run(tmp_path, runner)
    assert [shot.at for shot in shots] == [121, 363, 484]
    assert [shot.path.name for shot in shots] == [f"Movie.VTS_01_1.VOB.scr{i}.png" for i in (1, 2, 3)]
    assert all(shot.ok and shot.path.stat().st_size >= 250_000 for shot in shots)
    assert "剔除体积最小的一张（00:04:02，195 KiB）" in messages
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "Movie.VTS_01_1.VOB.mediainfo.txt",
        *(f"Movie.VTS_01_1.VOB.scr{i}.png" for i in (1, 2, 3)),
    ]  # 临时目录已清理


def test_small_image_is_retaken_at_random_time(tmp_path: Path) -> None:
    # 121 秒是黑屏，其余正常：先剔除最小的（121 秒），不需要重截
    shots, _ = _run(tmp_path, _ffmpeg({"00:02:01": DARK}))
    assert [shot.at for shot in shots] == [242, 363, 484]

    # 两张黑屏：剔除一张，另一张重截
    runner = _ffmpeg({"00:02:01": DARK, "00:04:02": DARK - 1})
    shots, messages = _run(tmp_path, runner)
    assert all(shot.ok for shot in shots)
    retaken = [shot for shot in shots if shot.at not in (242, 363, 484)]
    assert len(retaken) == 1 and 0 <= retaken[0].at <= 2998
    assert retaken[0].path.stat().st_size == BIG
    assert [shot.at for shot in shots] == sorted(shot.at for shot in shots)  # 按时间排序
    assert any("可能是黑屏" in m for m in messages)


def test_retake_gives_up_after_three_attempts_and_keeps_original(tmp_path: Path) -> None:
    shots, messages = _run(tmp_path, _ffmpeg({}, default=DARK), count=1)
    assert len(shots) == 1 and shots[0].ok
    assert shots[0].path.stat().st_size == DARK
    assert sum("仍然偏小" in m for m in messages) == 3
    assert "  重截都不理想，保留原图" in messages


def test_failed_capture_is_the_one_dropped(tmp_path: Path) -> None:
    # 第 4 张（484 秒）失败，多截的一张就是它，不删掉成功的
    shots, messages = _run(tmp_path, _ffmpeg({"00:08:04": 0}))
    assert [shot.at for shot in shots] == [121, 242, 363]
    assert all(shot.ok for shot in shots)
    assert not any("剔除" in m for m in messages)


def test_more_failures_than_extra_are_reported(tmp_path: Path) -> None:
    shots, _ = _run(tmp_path, _ffmpeg({"00:06:03": 0, "00:08:04": 0}))
    assert [shot.ok for shot in shots] == [True, True, False]


def test_dark_filter_off_matches_jietu(tmp_path: Path) -> None:
    runner = _ffmpeg({}, default=DARK)
    shots, messages = _run(tmp_path, runner, dark_filter=False)
    assert [shot.at for shot in shots] == [121, 242, 363]
    assert sum(call[0] == "ffmpeg" for call in runner.calls) == 3
    assert not any("黑屏" in m or "剔除" in m for m in messages)
