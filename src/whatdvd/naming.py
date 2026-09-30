"""输出文件命名，规则同 jietu。"""

from __future__ import annotations

# jietu 用 `tr '[:space:]' '.'`，GNU tr 只认 ASCII 空白字符。
_SPACE_TO_DOT = str.maketrans({char: "." for char in " \t\n\v\f\r"})


def clean_title(title: str) -> str:
    """空白字符换成 "."，删除圆括号。

    jietu 里的 `sed 's/[.]$//'` 只是去掉 echo 换行符变成的那个点，不会删掉标题本身的字符。
    """
    return title.translate(_SPACE_TO_DOT).replace("(", "").replace(")", "")


def screenshot_name(file_title: str, index: int, count: int) -> str:
    """第 index 张截图的文件名，序号按 `seq -w 1 count` 补零。"""
    width = len(str(count))
    return f"{file_title}.scr{index:0{width}d}.png"


def mediainfo_name(file_title: str) -> str:
    return f"{file_title}.mediainfo.txt"
