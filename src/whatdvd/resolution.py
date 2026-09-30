"""截图分辨率换算，算法同 jietu。"""

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
