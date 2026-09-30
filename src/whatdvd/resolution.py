"""截图分辨率换算：默认同 Upload-Assistant，也可切换为 minfo 或 jietu 的算法。"""

from __future__ import annotations


def _awk_int(value: float) -> int:
    """模拟 jietu 的 `awk "BEGIN{print x}" | awk '{print int($0)}'`。

    awk 的 print 先按 %.6g 输出，再由第二个 awk 取整。
    """
    return int(float(f"{value:.6g}"))


def display_size(width: int, height: int, par: float) -> tuple[int, int]:
    """按 MediaInfo 的像素宽高比（PAR）还原显示尺寸，只放大不缩小，结果保持偶数。

    PAR > 1 放大宽度，PAR <= 1 放大高度。
    """
    if par <= 0:
        raise ValueError(f"PAR 必须大于 0：{par}")
    if par <= 1:
        new_height = _awk_int(height / par)
        return width, new_height + new_height % 2
    new_width = _awk_int(width * par)
    return new_width + new_width % 2, height


ASPECT_MODES = ("ua", "minfo", "jietu")
"""截图比例修正方式：ua 同 Upload-Assistant，只放大不缩小；minfo 按 DAR、高度不变；jietu 按 PAR、只放大不缩小。"""


def _round_even(value: float) -> int:
    """同 Upload-Assistant 的 round_to_even：四舍五入（Python round），奇数加 1。"""
    size = int(round(value))
    return size + size % 2


def display_size_ua(width: int, height: int, par: float, dar: float | None) -> tuple[int, int]:
    """同 Upload-Assistant：PAR >= 1 时宽 × PAR；PAR < 1 时高度放大到 宽 ÷ DAR。

    Upload-Assistant 的写法是 高 × 宽 ÷ (DAR × 高)，化简即 宽 ÷ DAR。没有 DAR 时退回 高 ÷ PAR。
    """
    if par <= 0:
        raise ValueError(f"PAR 必须大于 0：{par}")
    if par == 1:
        return width, height
    if par < 1:
        new_height = width / dar if dar is not None and dar > 0 else height / par
        return width, _round_even(new_height)
    return _round_even(width * par), height

# minfo 把接近这些比例的 DAR 视为该比例（误差 0.01 以内）
_KNOWN_RATIOS = (4 / 3, 16 / 9, 1.85, 2.39, 2.35)


def _even_floor(value: float) -> int:
    size = int(value)
    return max(size - size % 2, 2)


def display_size_minfo(width: int, height: int, dar: float | None, par: float) -> tuple[int, int]:
    """同 minfo：DAR 与编码画面比例相差超过 0.02 时，高度不变，宽 = 高 × DAR，向下取偶数。

    没有 DAR 时退回按 PAR 换算宽度（同样向下取偶数）。
    """
    if dar is not None and dar > 0:
        for known in _KNOWN_RATIOS:
            if abs(dar - known) <= 0.01:
                dar = known
                break
        if abs(dar - width / height) > 0.02:
            return _even_floor(height * dar), height
        return width, height
    if par == 1:
        return width, height
    return _even_floor(width * par), height


def screenshot_size(width: int, height: int, par: float, dar: float | None, mode: str) -> tuple[int, int]:
    if mode == "ua":
        return display_size_ua(width, height, par, dar)
    if mode == "jietu":
        return display_size(width, height, par)
    if mode == "minfo":
        return display_size_minfo(width, height, dar, par)
    raise ValueError(f"未知的比例修正方式：{mode}")
