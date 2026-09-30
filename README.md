# whatdvd

DVD 发种助手：把 DVD（VIDEO_TS 文件夹或 ISO）整理成发布所需的素材，包括截图、MediaInfo、种子和发布说明。发布到站点由用户手动完成。

截图、MediaInfo 和做种的规则沿用 [Aniverse/inexistence](https://github.com/Aniverse/inexistence) 的 `jietu` 和 `zuozhong` 脚本；本项目为独立实现，不包含其代码。

## 当前进度

- 第 1 阶段（完成）：识别 DVD、生成 MediaInfo 和截图
- 第 2 阶段（完成）：ISO 按需解包、mktorrent 做种、Pixhost 上传、发布说明
- 第 3 阶段（完成）：Web 界面
- 第 4 阶段：Docker / systemd 打包

## 运行环境

- Debian 12 / Ubuntu 24.04 或更新版本，Python 3.11+
- 外部命令：`ffmpeg`、`ffprobe`、`mediainfo`；处理 ISO 需要 `7z`；做种需要 `mktorrent`
- 可选：`nconvert`，装了才对截图做无损重新压缩
- 不需要 root，不 mount ISO

```bash
sudo apt install ffmpeg mediainfo p7zip-full mktorrent
pip install .
```

## 使用

```bash
# 识别：显示选中的 VOB / IFO、容量、制式和截图尺寸
whatdvd scan "/path/to/Movie Name (2001)"

# 生成截图（默认 10 张）和 MediaInfo，上传到 Pixhost，生成发布说明
whatdvd run "/path/to/Movie Name (2001)" -o ./whatdvd-output

# 做种：private，分块默认 16 MiB
whatdvd torrent "/path/to/Movie Name (2001)" -a "https://tracker.example/announce"
```

- 输入可以是 ISO、`VIDEO_TS`、盘目录，或包含多张盘（文件夹或 ISO）的目录，每张盘单独处理。
- ISO 只解包选中的 VOB 和 IFO 到临时目录，处理完删除；临时目录可用 `--temp-dir` 指定。
- 截图必须全部上传成功才会生成发布说明 `<名称>.post.txt`。`--no-upload` 跳过上传，也不生成发布说明。
- 发布说明默认是 BBCode，可用 `--template` 指定模板文件，模板变量为 `$name`、`$mediainfo`、`$screenshots`，每张盘套用一次。
- Pixhost 可用 `--pixhost-domain pixhost.cc` 换备用域名，用 `--proxy` 走代理。

## Web 界面

盒子通常没有桌面环境，可以用浏览器操作：

```bash
cp config.example.toml ~/.config/whatdvd/config.toml   # 至少改 roots 和 token
whatdvd serve                                           # 或 whatdvd serve --root ~/downloads
```

- 默认只监听 `127.0.0.1:28090`。远程使用时通过 SSH 隧道（`ssh -L 28090:127.0.0.1:28090 盒子`）或 HTTPS 反向代理访问，不要直接暴露到公网。
- 需要 token 登录。配置里不写 token 时，每次启动随机生成一个，并在终端打印带 token 的登录链接。
- 只能浏览和处理 `roots` 中的目录；指向这些目录以外的符号链接不会显示，也无法访问。
- 界面中可以选择 DVD 文件夹、ISO 或包含多张盘的目录，生成截图和 MediaInfo（上传图床并生成发布说明），或者做种；日志实时显示，发布说明和 MediaInfo 可一键复制，种子可直接下载。
- 任务排队执行，默认同一时间只运行一个（`max_jobs`）。任务记录保存在内存中，重启服务后清空，输出文件保留在 `output_dir`。

## 规则摘要（同 jietu / zuozhong）

| 环节 | 规则 |
| --- | --- |
| 截图和 VOB 的 MediaInfo | 盘内体积最大的文件，大小相同时按文件名取第一个 |
| IFO 的 MediaInfo | 盘内体积最大的 `.IFO` |
| MediaInfo 输出 | 一个文件，VOB 在前、IFO 在后，删除输入路径的上级目录前缀；ISO 显示为 `<ISO 名>/VIDEO_TS/…` |
| 截图时间点 | 第 k 张取在 k × 间隔 秒；间隔按 VOB 时长分档：≥ 3600 秒 331，≥ 1500 秒 121，≥ 600 秒 71，否则 21 |
| 截图尺寸 | 按 MediaInfo 的 PAR 只放大不缩小：PAR > 1 放大宽度，否则放大高度，结果取偶数 |
| 画面处理 | 除尺寸换算外不做任何处理 |
| 做种 | `mktorrent -v -p -l 24`，announce 可不填 |

## 开发

```bash
make dev    # 创建 .venv 并安装开发依赖
make test   # 全部测试；集成测试需要 ffmpeg、mediainfo、dvdauthor，ISO 和做种测试另需 genisoimage、7z、mktorrent
```

测试用的 DVD 由 `tests/dvdgen.py` 用 ffmpeg 测试画面和 dvdauthor 现场生成，仓库中不包含任何影片。测试不会真的上传图床。

## 许可证

MIT
