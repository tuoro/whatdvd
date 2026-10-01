# whatdvd

DVD 发种助手：把 DVD（VIDEO_TS 文件夹或 ISO）整理成发布所需的素材，包括截图、MediaInfo、种子和发布说明。发布到站点由用户手动完成。

截图、MediaInfo 和做种的规则以 [Aniverse/inexistence](https://github.com/Aniverse/inexistence) 的 `jietu` 和 `zuozhong` 脚本为准；主片选择、截图时间点、截图尺寸和剔除黑屏参考 [Upload-Assistant](https://github.com/Audionut/Upload-Assistant)。本项目为独立实现，不包含它们的代码。

## 当前进度

- 第 1 阶段（完成）：识别 DVD、生成 MediaInfo 和截图
- 第 2 阶段（完成）：ISO 按需解包、mktorrent 做种、Pixhost 上传、发布说明
- 第 3 阶段（完成）：Web 界面
- 第 4 阶段（完成）：Docker 镜像、systemd 用户服务

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
cp config.example.toml ~/.config/whatdvd/config.toml   # 至少改 roots
whatdvd serve                                           # 或 whatdvd serve --root ~/downloads
whatdvd token                                           # 查看登录 token 和登录链接
```

- 默认只监听 `127.0.0.1:26873`。远程使用时通过 SSH 隧道（`ssh -L 26873:127.0.0.1:26873 盒子`）或 HTTPS 反向代理访问，不要直接暴露到公网。
- 需要 token 登录。第一次启动时自动生成并保存在 `~/.local/share/whatdvd/token`，终端会打印带 token 的登录链接；之后重启不变，随时运行 `whatdvd token` 查看。也可以在配置文件的 `token` 或环境变量 `WHATDVD_TOKEN` 中自己指定。
- 只能浏览和处理 `roots` 中的目录；指向这些目录以外的符号链接不会显示，也无法访问。
- 界面中可以选择 DVD 文件夹、ISO 或包含多张盘的目录，生成截图和 MediaInfo（上传图床并生成发布说明），或者做种；日志实时显示，发布说明和 MediaInfo 可一键复制，种子可直接下载。
- **设置页面**（右上角齿轮）：截图、图床、做种、发布说明模板、同时运行的任务数、ISO 临时目录、qBittorrent 和 Jackett 都可以在界面中修改，保存后立即生效，不用重启；qBittorrent 和 Jackett 可以先“测试连接”，Jackett 测试成功后列出已配置的站点供选择。修改保存在 `settings_file`（默认 `~/.local/share/whatdvd/settings.json`，权限 600），覆盖配置文件中的同名项，可以一键恢复为配置文件。监听地址、token、`roots`、输出目录、数据库只能在配置文件中修改：改了需要重启，或者关系到能访问哪些文件。
- 任务排队执行，默认同一时间只运行一个（`max_jobs`）。任务记录保存在内存中，重启服务后清空，输出文件保留在 `output_dir`。

## 资源获取（Jackett + qBittorrent，可选）

通过 [Jackett](https://github.com/Jackett/Jackett) 搜索 rutracker、rutor、kinozal 等站点上的 DVD 原盘，在界面中挑选后推送到 qBittorrent，下载完成后自动走完整流程（截图、MediaInfo、上传图床、发布说明）。whatdvd 只对接两者的 API，不负责部署。可以在设置页面中填写，也可以写在配置文件里：

```toml
[qbittorrent]
url = "http://127.0.0.1:8080"
username = "admin"
password = ""            # 或环境变量 WHATDVD_QB_PASSWORD
category = "whatdvd"
path_map = { "/downloads" = "/home/me/downloads" }   # qB 中的路径 = whatdvd 中的路径，两边一致时不用填

[jackett]
url = "http://127.0.0.1:9117"
api_key = ""             # 或环境变量 WHATDVD_JACKETT_API_KEY
indexer = "all"          # 或某个站点，例如 rutracker
queries = ["DVD9", "DVD5"]
interval = 60            # 每 60 分钟自动搜索；0 为只手动搜索
```

- 界面左侧出现“资源”入口：候选、进行中、已完成、已忽略。
- **只列候选，点了“下载”才推送**：种子文件经 Jackett 取得（站点只给磁力链接时用磁力链接），添加到 qB 的 `category` 分类并打上 `whatdvd` 标签。
- **过滤**：标题中没有 DVD5 / DVD9 的、Custom（改制过的盘）、`сжатый`（压缩过的盘）、`Реставрация`（修复版）、各种 Rip / Remux / 高清格式直接排除。以下情况保留但加提示，由你判断：
  - 带俄语配音标记（`| D, P, A, L1` 等）：可能加过俄语音轨，不一定是原盘。
  - 体积超出盘数容量：可能是合集或标错了。
  - `Лицензия`（俄罗斯正版盘）、全屏 / Pan & Scan 版本、没有做种者。
- **自动处理**：定时检查 qB 中这个分类的种子，下载完成后按 `path_map` 换成本机路径，在 `roots` 之内才处理。在 qB 中手动添加到这个分类的种子也会自动处理。
- 状态保存在 `database`（SQLite）中，重启后保留；重启时正在处理的任务会标为失败，可以点“重新处理”。
- 做种由 qB 继续负责，whatdvd 不删除、不移动下载的文件。

## Docker

镜像基于 Debian 12，以普通用户（uid 1000）运行，不需要 `privileged`，也不 mount ISO。

```bash
git clone https://github.com/tuoro/whatdvd && cd whatdvd
# 编辑 docker-compose.yml：媒体目录、user（与媒体文件属主一致）
docker compose up -d --build
docker exec whatdvd whatdvd token     # 查看登录 token 和登录链接
```

- 登录 token 第一次启动时自动生成，保存在 `./output/.whatdvd/token`，重启不变。
- 端口只映射到宿主机 `127.0.0.1:26873`，远程访问用 SSH 隧道：`ssh -L 26873:127.0.0.1:26873 盒子`，然后打开 `http://127.0.0.1:26873`。
- 媒体目录只读挂载到 `/media`；输出（截图、MediaInfo、发布说明、种子）在 `./output`，ISO 解包的临时文件也放在这里的 `.tmp` 中，用完即删。
- 容器根文件系统只读，去掉所有 capability，并禁止提权。
- 需要改配置时，复制 `docker/config.toml`，改好后按 `docker-compose.yml` 中的注释挂载进去。
- 也可以用镜像跑命令行：`docker compose run --rm whatdvd run /media/某部片 --no-upload -o /output`。

## systemd 用户服务

不用 Docker 时，可以用 pipx 安装，再交给 systemd 以当前用户身份常驻：

```bash
sudo apt install pipx ffmpeg mediainfo p7zip-full mktorrent
pipx install git+https://github.com/tuoro/whatdvd
mkdir -p ~/.config/whatdvd ~/.config/systemd/user
cp config.example.toml ~/.config/whatdvd/config.toml      # 改 roots
cp packaging/systemd/whatdvd.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whatdvd
sudo loginctl enable-linger "$USER"                      # 退出登录后继续运行
whatdvd token                                            # 查看登录 token 和登录链接
journalctl --user -u whatdvd -f                          # 查看日志
```

升级：`pipx upgrade whatdvd && systemctl --user restart whatdvd`。

## 规则摘要

| 环节 | 规则 |
| --- | --- |
| 主片 | 同 Upload-Assistant：读各组 `VTS_xx_0.IFO` 的时长，最长的一组为主片；后面的组要长 10% 以上才替换，所以剧集盘选第一集 |
| 截图和 VOB 的 MediaInfo | 主片组中最大的 VOB（不含 `_0.VOB` 菜单），大小相同时按文件名取第一个 |
| IFO 的 MediaInfo | 主片组的 `VTS_xx_0.IFO` |
| 假标题 | 本项目额外加的：IFO 时长配上该组 VOB（不含 `_0`）总大小，平均码率低于 0.5 Mbps 的组视为假标题跳过（复制保护盘常见），日志中会列出 |
| IFO 读不出时长时 | 退回 jietu 的规则：盘内体积最大的文件作 VOB，最大的 `.IFO` 作 IFO |
| MediaInfo 输出 | 一个文件，VOB 在前、IFO 在后；在输入路径的上级目录用相对路径运行 mediainfo，输出不做任何修改；ISO 显示为 `<ISO 名>/VIDEO_TS/…` |
| 截图时间点 | 同 Upload-Assistant：在选中 VOB 时长的 5%–90% 之间均匀取点（从 5% 起，每隔 85% ÷ 张数 一张），避开片头和片尾字幕 |
| 截图尺寸 | 默认同 Upload-Assistant（`aspect = "ua"`）：只放大不缩小，PAR ≥ 1 时宽 × PAR，PAR < 1 时高 = 宽 ÷ DAR，四舍五入后奇数加 1。PAL 16:9 → 1024×576，PAL 4:3 → 768×576，NTSC 16:9 → 854×480，NTSC 4:3 → 720×540。也可设为 `"minfo"`（按 DAR，高度不变，NTSC 4:3 → 640×480）或 `"jietu"` |
| 显示比例来源 | 优先读主片组 `VTS_xx_0.IFO` 中的比例标记（4:3 或 16:9，播放器实际使用的值），读不到时用 ffprobe，最后才用 MediaInfo。MediaInfo 会按 MPEG-2 的显示区域计算比例，PAL 16:9 盘常把显示宽度标为 540，它因此报 DAR 2.37，截图会被拉成 1366×576；两者不一致时日志会提示 |
| 剔除黑屏 | 同 Upload-Assistant：多截一张，删掉体积最小的；不超过 120 KB 的视为黑屏，在随机时间点重截，最多 3 次，超过 75 KB 即可，都不理想时保留原图。`--no-dark-filter` 或 `dark_filter = false` 关闭 |
| 画面处理 | 除尺寸换算外不做任何处理 |
| 做种 | `mktorrent -v -p -l 24`，announce 可不填 |
| 做种前检查 | 列出盘目录中和上传无关的文件（PTP 2.1.3）：Thumbs.db、Desktop.ini、.DS_Store、`@eaDir` 等系统文件，图片、视频样片、种子和发布说明、没下载完的文件、快捷方式，以及 VIDEO_TS 中不属于 DVD 结构的文件。只提示，不删除，照常做种；.nfo、日志、AUDIO_TS、JACKET_P 不提示 |

## 开发

```bash
make dev    # 创建 .venv 并安装开发依赖
make test   # 全部测试；集成测试需要 ffmpeg、mediainfo、dvdauthor，ISO 和做种测试另需 genisoimage、7z、mktorrent
```

测试用的 DVD 由 `tests/dvdgen.py` 用 ffmpeg 测试画面和 dvdauthor 现场生成，仓库中不包含任何影片。测试不会真的上传图床。

## 许可证

MIT
