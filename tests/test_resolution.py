import pytest

from whatdvd.resolution import display_size, display_size_minfo, screenshot_size


@pytest.mark.parametrize(
    ("width", "height", "par", "expected"),
    [
        (720, 576, 1.422, (1024, 576)),  # PAL 16:9
        (720, 576, 1.067, (768, 576)),  # PAL 4:3
        (720, 480, 1.185, (854, 480)),  # NTSC 16:9：853 为奇数，加 1
        (720, 480, 0.889, (720, 540)),  # NTSC 4:3：放大高度，539 为奇数，加 1
        (720, 576, 1.0, (720, 576)),
        (352, 288, 1.455, (512, 288)),  # 半 D1
    ],
)
def test_display_size_matches_jietu(width: int, height: int, par: float, expected: tuple[int, int]) -> None:
    assert display_size(width, height, par) == expected


def test_awk_rounding_before_int() -> None:
    # awk 先按 %.6g 输出：1023.9999999 印成 1024，再取整得 1024，而不是 1023。
    assert display_size(720, 576, 1023.9999999 / 720) == (1024, 576)


def test_rejects_non_positive_par() -> None:
    with pytest.raises(ValueError):
        display_size(720, 576, 0)


@pytest.mark.parametrize(
    ("width", "height", "dar", "par", "expected"),
    [
        (720, 576, 1.778, 1.422, (1024, 576)),  # PAL 16:9
        (720, 576, 1.333, 1.067, (768, 576)),  # PAL 4:3
        (720, 480, 1.778, 1.185, (852, 480)),  # NTSC 16:9：853.3 向下取偶数
        (720, 480, 1.333, 0.889, (640, 480)),  # NTSC 4:3：宽度缩小，高度不变
        (1920, 1080, 1.778, 1.0, (1920, 1080)),  # 方形像素，比例相差不超过 0.02，不缩放
        (720, 576, None, 1.422, (1022, 576)),  # 没有 DAR 时按 PAR 换算宽度
        (720, 576, None, 1.0, (720, 576)),
    ],
)
def test_display_size_matches_minfo(
    width: int, height: int, dar: float | None, par: float, expected: tuple[int, int]
) -> None:
    assert display_size_minfo(width, height, dar, par) == expected


def test_screenshot_size_modes() -> None:
    assert screenshot_size(720, 480, 0.889, 1.333, "minfo") == (640, 480)
    assert screenshot_size(720, 480, 0.889, 1.333, "jietu") == (720, 540)
    with pytest.raises(ValueError):
        screenshot_size(720, 480, 0.889, 1.333, "other")
