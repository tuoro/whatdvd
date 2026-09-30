# whatdvd

DVD 发种助手：把 DVD（VIDEO_TS 文件夹，后续支持 ISO）整理成发布所需的素材，包括截图、MediaInfo、种子和发布说明。发布到站点由用户手动完成。

截图、MediaInfo 和做种的规则沿用 [Aniverse/inexistence](https://github.com/Aniverse/inexistence) 的 `jietu` 和 `zuozhong` 脚本；本项目为独立实现，不包含其代码。

## 当前进度

第 1 阶段（命令行版）：识别 DVD、生成 MediaInfo 和截图。ISO、做种、图床上传、发布说明和 Web 界面在后续阶段加入。

## 运行环境

- Debian 12 / Ubuntu 24.04 或更新版本，Python 3.11+
- 外部命令：`ffmpeg`、`ffprobe`、`mediainfo`
- 可选：`nconvert`，装了才对截图做无损重新压缩

```bash
sudo apt install ffmpeg mediainfo
pip install .
```

## 使用

```bash
# 识别：显示选中的 VOB / IFO、容量、制式和截图尺寸
whatdvd scan "/path/to/Movie Name (2001)"

# 生成截图（默认 10 张）和 MediaInfo，输出到 ./whatdvd-output
whatdvd run "/path/to/Movie Name (2001)" -n 10 -o ./whatdvd-output
```

输入可以是 `VIDEO_TS` 本身、盘目录，或包含多张盘的目录，每张盘单独处理。

## 规则摘要（同 jietu）

| 环节 | 规则 |
| --- | --- |
| 截图和 VOB 的 MediaInfo | 盘内体积最大的文件，大小相同时按文件名取第一个 |
| IFO 的 MediaInfo | 盘内体积最大的 `.IFO` |
| MediaInfo 输出 | 一个文件，VOB 在前、IFO 在后，删除输入路径的上级目录前缀 |
| 截图时间点 | 第 k 张取在 k × 间隔 秒；间隔按 VOB 时长分档：≥ 3600 秒 331，≥ 1500 秒 121，≥ 600 秒 71，否则 21 |
| 截图尺寸 | 按 MediaInfo 的 PAR 只放大不缩小：PAR > 1 放大宽度，否则放大高度，结果取偶数 |
| 画面处理 | 除尺寸换算外不做任何处理 |

## 开发

```bash
make dev    # 创建 .venv 并安装开发依赖
make test   # 全部测试；集成测试需要 ffmpeg、mediainfo、dvdauthor
```

测试用的 DVD 由 `tests/dvdgen.py` 用 ffmpeg 测试画面和 dvdauthor 现场生成，仓库中不包含任何影片。

## 许可证

MIT
