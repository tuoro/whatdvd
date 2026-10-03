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
- **设置页面**（右上角齿轮）：截图、图床、做种、发布说明模板、同时运行的任务数、ISO 临时目录、发种目录、qBittorrent、Jackett 和 TMDB 都可以在界面中修改，保存后立即生效，不用重启；qBittorrent 和 Jackett 可以先“测试连接”，Jackett 测试成功后列出已配置的站点供选择。修改保存在 `settings_file`（默认 `~/.local/share/whatdvd/settings.json`，权限 600），覆盖配置文件中的同名项，可以一键恢复为配置文件。监听地址、token、`roots`、输出目录、数据库只能在配置文件中修改：改了需要重启，或者关系到能访问哪些文件。
- 任务排队执行，默认同一时间只运行一个（`max_jobs`）。任务记录保存在内存中，重启服务后清空，输出文件保留在 `output_dir`。

## 发种目录（硬链接，可选）

各站对文件夹名的要求不同：PTP 要求和 IMDb 的原名或英文名一致（只能改最外层文件夹名，2.1.1、2.1.4），BHD 禁止改动文件和文件夹名。为了不动原始下载，可以设置发种目录：处理和做种之前，whatdvd 先用硬链接把盘放到发种目录，可以另起最外层的名字。有的种子把 DVD 文件直接平铺在盘目录里、没有 VIDEO_TS 子目录（PTP 要求 VIDEO_TS 结构）：发种目录中会把这些文件放进 `VIDEO_TS/`，其他文件（nfo、封面）位置不变，原始下载保持平铺，qB 照常做种；旧版本按原样建的平铺副本，确认是同一份数据后自动换成 VIDEO_TS 结构。没设发种目录时平铺的盘照常截图、生成 MediaInfo，日志里提示需要整理。

```toml
seed_dir = "/data/seed"     # 也可以在设置页面填写；必须和下载目录在同一个文件系统
```

- 硬链接和原文件是同一份数据，不多占空间；原始下载删除后发种目录里的仍然有效。qB 中原来的任务继续按原名做种，不受影响。
- 来源页面多出“发种名称”：留空用原名；填写后放到 `发种目录/发种名称/`（ISO 自动补上扩展名），MediaInfo、截图、种子和输出目录都按这个名字。DVD 结构不变，只改最外层的名字。批量提交时各自用原名。
- 发种目录中已有同名的：是同一份数据就直接使用，不是就报错，不会覆盖。
- 发种目录和下载目录必须在同一个文件系统、同一个挂载点下，否则报错（不退回到复制或符号链接）。Docker 中同一块盘分开挂载两次（例如 `/media` 和 `/seed`）也不能建硬链接，要挂载它们共同的上级目录。来源页面会提前提示无法建立硬链接的目录。
- 做种完成后可以点“添加到 qB 做种”：种子添加到单独的分类 `qbittorrent.seed_category`（默认 `whatdvd-seed`，必须和下载用的分类不同，否则会被当成新下载再处理一遍），保存路径指向发种目录（经 `path_map` 换算成 qB 中的路径），跳过校验直接做种。qB 必须能看到发种目录。
- 这是项目负责人同意的、和 inexistence 不同的做法（inexistence 直接在原目录上做种）。不设置 `seed_dir` 时行为不变。

## 查片名（TMDB，可选）

在设置页面填写 TMDB 的 API Key（在 themoviedb.org 的账号设置中免费申请，v3 API Key 和 v4 读取令牌都可以；也可以用环境变量 `WHATDVD_TMDB_API_KEY` 或配置文件的 `[tmdb] api_key`）后，来源页面多出“片名”：

- 搜索框也可以直接粘贴 IMDb 链接或编号（例如 `https://www.imdb.com/title/tt0035118/`）：先按编号在 TMDB 中找，TMDB 中没有的直接用 IMDb 数据集（没有 TMDB API Key 时也可以这样查）。TMDB 的条目没有 IMDb 编号时，也可以这样补上。
- 按文件夹名猜搜索词和年份（括号里的别名会去掉，例如 `Непобедимые (Ленинградцы) 1942` 搜 `Непобедимые`）（从资源页下载的，用种子标题猜，常带英文名，例如 `Изумрудный лес / The Emerald Forest (1985)`），可以修改；电影和剧集一起搜，俄文等译名也能搜到。
- 选中后给出：
  - **PTP 发种名称**：IMDb / TMDB 的英文名 + 年份 + 盘型，例如 `Come.and.See.1985.DVD9`（PTP 2.1.1 要求和 IMDb 的原名或英文名一致）。配置了发种目录时自动填进“发种名称”。
  - **BHD 标题**：写法同 Upload-Assistant，`英文名 [AKA 原名] 年份 [版本] [地区或发行商] PAL|NTSC DVD9 MPEG-2 音轨`，例如 `Come and See AKA Иди и смотри 1985 RUS PAL DVD9 MPEG-2 DD5.1`。原名和英文名差别够大时才加 AKA；DD 音轨写成 `DD5.1`（BHD 3.4.4），其他写成 `DTS 5.1`。制式和音轨在生成截图后从识别结果和 VOB 的 MediaInfo 中补全，完整标题显示在任务结果里。
  - TMDB 和 IMDb 链接，以及片名来自哪里（IMDb 数据集或 TMDB）。

### IMDb 数据集（可选，推荐发 PTP 时使用）

PTP 要求文件夹名和 IMDb 的名字一致，而 IMDb 没有免费的在线 API。设置页面的“IMDb 数据集”可以下载 IMDb 官方数据集（[datasets.imdbws.com](https://datasets.imdbws.com/)，个人非商业使用）：

- 点“下载数据集 / 更新数据集”，在后台下载约 740 MB，边下载边导入本地 SQLite（放在数据库旁边的 `imdb.db`），不保存压缩包，界面显示进度。2026 年 10 月实测约 2–3 分钟，导入 169 万部（电影、电视电影、剧集、迷你剧、特别节目、录像，不含单集），占用约 190 MB。更新失败或中途取消时保留原来的数据。
- 搜索结果里直接标出每部片的 IMDb 编号和 IMDb 的名字、年份（没有编号或数据集中没有时也会标明）。
- 也按片名查 IMDb 数据集：IMDb 显示的名字、原名，以及国际、美国、英国、俄罗斯、苏联地区的片名都能查到，统一大小写、去掉标点和变音符号，俄文名再按 IMDb 的写法转写一次（`Долгая дорога в дюнах` → `Dolgaya doroga v dyunakh`），罗马数字当作数字（`Два капитана 2` 能找到 `Dva kapitana II`），找不到时去掉副标题再查。TMDB 漏掉的片、没有 TMDB API Key 时都能这样找到。2026 年 10 月用 rutor 上 207 个影视 DVD 种子标题实测：189 个（91%）找到且年份一致，8 个找到但 IMDb 的年份不同，10 个没找到（大多 IMDb 上没有）。音乐录像、演唱会大多不在 IMDb 上。
- **资源候选列表也显示 IMDb**：每条候选下面标出在 IMDb 数据集中找到的编号、名字和年份（点击打开 IMDb），没把握时写明原因。规则同下面的自动改名；只查当前这一页，全部在本地，每条约 0.5 毫秒，不访问任何网站，也不用重新搜索。没有数据集时不显示。筛选栏的“IMDb 找得到”只显示能对上的候选（音乐会、合辑、培训等 IMDb 上没有的都会隐藏，也会隐藏 IMDb 上对不上的冷门片），第一次勾选要把全部候选查一遍。
- **怎样算找到**（自动改名和候选列表共用）：
  - 标题里的每个名字都查（rutor、rutracker 的 " / "，rutracker 斜杠只有一边有空格的，kinozal 俄文名和原名之间没有斜杠的），优先能直接对上片名本身（不是靠别名）、年份又完全一致的名字；去掉季、集、碟号（`S1E8 of 216`、`- E1-7 -`、`ДИСКИ 12 и 13 из 13`），年份范围取第一个；`3D` 先按原样查、查不到再去掉。
  - 年份相差一年以内的才算对上；有季、集标记（`[S07]`、`[01-12 из 12]`、`[01-03Х01-26]`）的只要剧集，年份落在播出期间内也算。
  - 只有俄文名时，俄文原样和转写都查，片名本身（转写，允许 Sedmoe / Sedmoye 这样的小差别）对上的优先，不要俄文译名恰好相同的外国片（`Жаворонок (1964)` 是苏联的 Zhavoronok，不是匈牙利的 Pacsirta；`Агент [01-16 из 16] (2013)` 不是美剧 Turn）。
  - 好几个都对得上时，电影依次只留：不是剧集的、片名本身对上的、年份完全一致的、投票人数是其他十倍以上的、电影；剧集先看投票人数（Supernatural 各季都是 2005 年开播的那部）。靠别名对上的电影和同名的 video 冲突时（Birdman 该选电影，Saving Santa 该选 video）分不出来，不选。
  - 年份不完全一致、而附近三年内有有名得多的同名片时不选（You're Next 的 DVD 标 2013 年，年份只对得上 2014 年的同名剧集）；合集（Трилогия、Квадрология、Сборник、`(4 в 1)`、Box Set……）不选。
  - 投票人数和剧集结束年份来自 IMDb 的 title.ratings 和 title.basics，旧版本导入的数据集没有，照常能查，只是用不上相关的规则；在设置页面点一次“更新数据集”即可。
- **实测**（2026 年 10 月，本地数据集跑全部标题）：rutor 按年份采集的 5237 个 DVD 标题找到 77%，kinozal 6860 个找到 80%，rutracker 3186 个找到 74%；没找到的大多是音乐会、合辑、培训、电视节目，IMDb 上本来就没有。rutor 的 839 个发布页带 IMDb 链接，给出结果的 795 个全部正确（另有 8 个和发布页不同，逐个核对都是发布页链错了）；名字对不上的 207 个逐条人工核对，已知还会选错的约 7 个，都是 IMDb 上没有的俄罗斯小片对上了俄文译名相同的外国片。
- **自动按 IMDb 改名**（设置页面“任务”一节，默认打开）：按种子标题（从资源页下载的）或文件夹名在 IMDb 数据集中查找，只有唯一一个年份对得上（相差一年以内）的结果时才自动采用：
  - **只在必要时改名**（PTP《Site Policies About Modifying Files》：发布要和拿到时一样，只有文件夹名缩写得看不出片名、或者不是原名也不是英文名时才必须改；不必完全一致，`MONTY_PYTHON_HOLY_GRAIL` 可以，`MPHGRAIL` 不行）：原文件夹名里看得出 IMDb 的英文名或原名的（忽略大小写、点、下划线、变音符号；俄文文件夹名转写后比较，所以俄语片用俄文原名也算），保持原名；看不出的（`2k2`、`VIDEO_TS`、外国片的俄文译名）才改。2026 年 10 月抽查 rutor 上 82 个非 Custom 的候选：36 个保持原名，37 个改名，9 个没把握、保留原名。
  - 下载完成后的自动处理：需要改名时，发种目录中的文件夹直接用 IMDb 名（例如 `Nepobedimye.1943.DVD5`），任务结果中给出 BHD 标题，日志里写明选了哪部片、依据是什么。没有把握时保留原名，并在日志中说明原因；自动选的名字和发种目录中别的盘重名时也改用原名。
  - 来源页：打开时预先选中；需要改名时发种名称也填好，原名已经合规时提示保持原名。请核对。
  - 发 BHD 时改名和 BHD 规则第 3 节“不要修改文件和文件夹名”的字面要求不一致，这是项目负责人的选择；需要保持原名时关掉这个开关。
- 旧版本导入的数据集不能按片名查，需要在设置页面点一次“更新数据集”（数据库约 190 MB，导入约 2–3 分钟）。
- 导入后，查片名时按 TMDB 给出的 IMDb 编号取 IMDb 的名字：IMDb 显示的名字（通常就是英文名，例如 Come and See、Moscow Does Not Believe in Tears）；非英语片显示的是原名时，用国际英文名（例如 Sen to Chihiro no kamikakushi → Spirited Away）。原名用 IMDb 的 originalTitle（俄语片是拉丁字母转写，例如 Idi i smotri），BHD 标题的 AKA 也用它。
- 数据集里没有（新片）或者还没下载时，退回 TMDB 的名字，界面上会说明。

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
films_only = true        # 只要影视类（分类 2000 电影、5000 电视）
```

- 界面左侧出现“资源”入口：候选、进行中、已完成、已忽略。候选可以按标题文字、DVD5 / DVD9 / 多张盘、有做种者、没有提示筛选，每页 50 个。
- **搜索结果数量**：Jackett 每个关键词只读站点搜索结果的第 1 页（100 条），所以定时搜索只能拿到最近发布的资源（新资源总在最前面，定时搜索足够跟上）。要找更早的资源，点“按年份全面搜索”：把每个关键词和 1920 年到今年逐年组合（例如 `DVD9 1999`）分别搜索，在后台进行，界面显示进度。
- **各站的差异**（2026 年 10 月通过 Jackett 实测）：rutor 每次搜索最多 100 条，rutracker 和 kinozal 最多 50 条，加年份拆分后单个年份也可能触到上限。kinozal 的标题写 `DVD-9` / `DVD-5`，只搜 `DVD9` 一条也搜不到，所以 Jackett 的默认关键词是 `DVD9 DVD5 DVD-9 DVD-5`。rutracker 在标题中写明 `(Custom)`，会被排除（实测 rutracker 电影分区最近 50 个 DVD9 中 49 个是 Custom，所以 rutracker 上能收录的原盘以音乐、纪录片为主）；写明转制来源的（`Betacam SP > DVD5`、`VHS > DVD9`）也排除；kinozal 不标 Custom，带 `Dub`、`MVO`、`AVO`、`VO` 等配音标记的会提示“可能加过音轨”。
- **只要影视类**（默认开启，设置页面可关）：Jackett 搜索时请求 Torznab 分类 2000（电影）、5000（电视：剧集、动画、纪录片）和 8000（其他），再去掉只标为“其他”和体育（5060）的结果。带上 8000 是因为 Jackett 的 rutor 不区分分类，所有结果都标为“其他”，只请求 2000、5000 时一条也搜不到；所以通过 Jackett 搜 rutor 时无法去掉音乐，rutor 请用直连。2026 年 10 月实测 rutracker：不限分类时 `DVD9`、`DVD5` 的结果大多是音乐视频、培训讲座，限定后都去掉了，体育比赛、游戏附赠盘也去掉了；而且 Jackett 按分类分别搜索，每个分类各有 50 条上限，结果从 50 条变成约 150 条。kinozal 把演唱会、体育、戏剧歌剧芭蕾、综艺节目也归在“电影”下，按它自己的分类（Concerts、Sport、Theatre/Opera/Ballet、Shows）去掉。候选会记下站点和分类，以后改了这些过滤规则，已有的候选按新规则重新过滤；更早的版本存下的候选没有记分类，可以点资源页的“重建候选列表”：清空“候选”（已忽略、进行中、已完成的不动，忽略过的不会再出现），再按现在的规则全面搜索一遍。rutor 直连搜索一次只能选一个分类，而影视分类有十个，所以反过来多搜音乐、其他、体育三个分类，再从结果中剔除：实测最新 100 个 DVD9 中去掉 36 个音乐 DVD，DVD5 中去掉 16 个。
- **新资源没出现时**：资源页搜索按钮下方按来源列出自动搜索的间隔和下次时间、上次搜索的结果数和新增数、结果中最新一条的发布时间，以及出错信息。常见原因：间隔为 0（只手动搜索）；Jackett 中没有添加该站点（例如 rutor 已从 Jackett 删掉但没有启用 rutor 直连）；Jackett 的 "Strip Cyrillic Letters" 删掉了俄文。日常搜索只读最新的一页，新发布的资源总在最前面；如果最新一条的发布时间明显落后，问题在 Jackett 或站点一侧。
- **kinozal**：只能通过 Jackett（需要登录，搜索页有 Cloudflare 人机验证；RSS 只有全站最新 100 条，没有磁力链接，用处不大）。Jackett 对 kinozal 不能翻页，按分类拆分也没有增加结果；按年份拆分有效：实测 `DVD-9` 本身 50 条，加上 2000–2012 共 13 个年份后为 664 条，每个年份仍触到 50 条上限。所以对 kinozal，“按年份全面搜索”很重要。通过 Jackett 连续搜索时每次间隔 2 秒，避免触发站点的防刷限制（4 个关键词 × 约 107 个年份，全面搜索约需 15–20 分钟）。
- **Jackett 设置**：请关掉俄语站点的“Strip Cyrillic Letters”（kinozal 默认开着）。“Add RUSSIAN to end of all titles”加在标题末尾的 ` - RUSSIAN` / ` RUS` 会被 whatdvd 去掉，开不开都可以。开着时片名的俄文部分会被删掉（有的标题只剩 `DVD-5`），`сжатый`、`Реставрация`、`Лицензия` 等过滤用的标记也会丢失；候选列表会对这种标题给出提示。
- **rutor 直连**（推荐用于 rutor）：rutor 是公开站，whatdvd 可以不经过 Jackett，直接读取它的搜索页，并且翻页（每个关键词最多 20 页、2000 条）。在设置页面填写地址（`https://rutor.info` 或镜像 `https://rutor.is`）即启用，种子从 `d.rutor.info` 下载，不需要账号。启用后建议在 Jackett 中去掉 rutor；同一个种子两边都搜到时只列一次。2026 年 10 月实测全面搜索（216 步，每次请求间隔 0.7 秒，约 7 分钟）：rutor 直连得到 5239 个候选，通过 Jackett 为 4283 个，原因是 Jackett 对单个年份超过 100 条的年份（例如 DVD5 2003–2014、DVD9 2010–2016）只能拿到前 100 条。rutor 改版时页面解析可能失效，界面会明确报错而不是显示 0 个结果。

```toml
[rutor]
url = "https://rutor.info"
queries = ["DVD9", "DVD5"]
interval = 60            # 每 60 分钟读一次最新的一页；0 为只手动搜索
films_only = true        # 只要影视类
```
- **推送前检查种子内容**：rutor 上很多 Custom 盘的标题没写 Custom，种子里的文件夹名却写着（例如标题 `Терминатор 2 / Terminator 2: Judgment Day (1991) DVD9 | P, A`，文件夹 `Terminator.2.Judgment.Day.(1991).(DVD9.CUSTOM.FS…)`）。推送到 qBittorrent 前读出种子里的文件夹名和文件列表，写着 Custom、сжатый、Реставрация、Rip 等，或者没有 VOB、IFO、ISO 文件的，拒绝推送并移到“已忽略”，写明原因。2026 年 10 月抽查 rutor 上通过标题过滤的 90 个候选，其中 18 个（20%）是这种 Custom 盘。只有磁力链接的、在 qB 中手动加进分类的，下载完成后按文件夹名和文件再查一次，有问题的标为失败、不自动处理。
- **去菜单、去花絮的盘**：标题、种子文件夹名或发布页“Тип релиза / Качество”写着“без меню”“без доп. материалов”“только фильм”“no menu”“movie only”等的，同 Custom 一样拒绝（PTP、BHD 只收未改动的原盘）。“Доп. материалы: нет”这类字段不算，那是原盘本来就没有花絮。
- **盘的结构**：推送前按种子里的文件列表和大小检查每张盘。缺 `VIDEO_TS.IFO`（每张 DVD 都必须有）、有标题集文件却缺 `VTS_xx_0.IFO`、标题 VOB 编号中间缺号、一张盘超过 DVD9 容量的，拒绝推送；标着 DVD9、只有一张盘却放得进 DVD5 的，只提示（可能压缩过或删掉了部分内容）。下载完成后按本地文件夹再查一次。2026 年 10 月 rutor 上随机 800 个 DVD 种子：拒绝 1 个（只有 VOB、没有任何 IFO）；缺 BUP 的 0 个，不检查；没有 `VIDEO_TS.VOB` 的 196 个（正版盘常见），不算问题。
- **推送前读发布页**：有的 Custom 盘标题和种子文件夹名都没写，只在发布页描述的“Тип релиза”（发布类型）或“Качество”（画质）里写着 `DVD5 (Custom)`。推送前读一次发布页（rutor；rutracker 等通过 Jackett 的发布页也读，读不到时不拦，在资源上说明没有检查），只看这几个字段，写着 Custom、сжатый、Реставрация、Рип、Remux 的拒绝推送。描述里嵌的 MediaInfo 中的 “CustomMatrix”“Метод сжатия”和页面下方其他版本的列表不算。2026 年 10 月抽查 rutor 上通过标题过滤的 95 个候选：发布页查出 19 个 Custom 盘，其中 7 个标题和文件夹名都没写；文件夹名另外查出 6 个发布页没写的，两项合起来 25 个，没有误拒。
- **零散的音视频文件**：种子里混着 .ac3、.dts、.h264 等单独的流文件时照样推送，但在资源上提示：PTP 规定混进原盘的这类文件要删掉再发（这样的种子可以被替换）。做种前的多余文件检查也会列出它们。
- **下载后看 MediaInfo 的音轨**：IFO 的 MediaInfo 中有两条以上俄语音轨、同时还有原声的，在任务结果和日志中提示“多半是给外国片加了俄语配音的 Custom 盘”（只提示，不拒绝）。2026 年 10 月用 rutor 上 469 个发布页统计：符合这个条件的 30 个中，11 个写明是 Custom，其余也几乎都是外国片加了多条俄语配音；只有一条俄语音轨的不能说明问题（俄罗斯正版盘本来就有一条俄语配音），不提示。按码率判断是否压缩过暂时不做：发布页里的码率写法太乱，拿不到可靠的数据定阈值。
- **IFO 统计（攒数据）**：每处理一张盘，读出 `VIDEO_TS.IFO` 头部的提供者标识、区码、标题集数量、BUP 是否和 IFO 相同，显示在结果和日志中，并连同种子标题、发布页链接、处理时的提示记入数据库旁边的 `ifo_samples.jsonl`。设置页面“IFO 统计”可以一键导出 CSV（Excel 可直接打开）。只记录、不判断：重新制作过的盘有时会在提供者标识里留下制作软件的名字，攒够真实的盘再看能不能用来提示。ISO 解包后的临时目录不保留，读不到。
- **长片放在一张 DVD5 上**：主片 110 分钟以上、整张盘是 DVD5 的，在任务结果和日志中提示“可能是从 DVD9 压缩过的，请对照正版 DVD 的信息确认”（说明性提醒，不是警告）。2026 年 10 月 rutor 上 110 分钟以上的单张 DVD5 电影：标题写明压缩过（сжатый）的 67 个，普通的 29 个；俄罗斯正版盘也常把长片放在单层盘上，所以只作提醒。110 分钟先用着，积累了真实的盘再调。
- **只列候选，点了“下载”才推送**：种子文件经 Jackett 取得（站点只给磁力链接时用磁力链接），添加到 qB 的 `category` 分类并打上 `whatdvd` 标签。
- **过滤**：标题中没有 DVD5 / DVD9 的、Custom（改制过的盘）、`сжатый`（压缩过的盘）、`Реставрация`（修复版）、各种 Rip / Remux / 高清格式直接排除。以下情况保留但加提示，由你判断：
  - 带俄语配音标记（`| D, P, A, L1` 等）：可能加过俄语音轨，不一定是原盘。
  - 体积超出盘数容量：可能是合集或标错了。
  - 全屏 / Pan & Scan 版本、没有做种者。
- **正面标记**（绿色，不算“提示”，勾选“没有提示”时不会被排除）：`РУ`（kinozal：俄语原版片，原声就是俄语，没有后加配音）、`БП`（kinozal：原声，没有翻译）、`Лицензия`（俄罗斯正版盘）。
- **自动处理**：定时检查 qB 中这个分类的种子，下载完成后按 `path_map` 换成本机路径，在 `roots` 之内才处理。在 qB 中手动添加到这个分类的种子也会自动处理。
- **qB 不在 Docker 里、whatdvd 在 Docker 里**（或者反过来、两者在不同的容器里）：两边看到的下载目录路径不同，需要：
  1. 把 qB 的下载目录挂载进 whatdvd 容器，放在 `roots` 之内（Docker 版默认 `roots = ["/media"]`），例如 `- /home/me/downloads:/media:ro`；
  2. 在设置页面的“路径映射”中填写 `qB 中的目录 = whatdvd 中的目录`，例如 `/home/me/downloads = /media`；
  3. 设置里的“qB 保存路径”要么留空（用 qB 自己的默认路径），要么填 **qB 那边**的路径（`/home/me/downloads`），不要填容器里的 `/media`：qB 会在宿主机上另建一个 `/media` 目录，whatdvd 看不到。
  下载完成后找不到路径时，结果中会写出 qB 报告的原始路径；在 `roots` 里找到同名文件夹时，直接给出路径映射该怎么填。改好后点“重新处理”，会按新的路径映射重新换算（旧版本失败的记录没有记下 qB 中的路径，重新处理时向 qB 再要一次）。
- 状态保存在 `database`（SQLite）中，重启后保留；重启时正在处理的任务会标为失败，可以点“重新处理”。
- 做种由 qB 继续负责，whatdvd 不删除、不移动下载的文件。

## 查重（Jackett，可选）

片名确定（有 IMDb 编号）后，按 IMDb 编号在站点上查这部片已有的 DVD 原盘，只读，不上传：

- 在设置页面 Jackett 一节的“查重站点”填写 Jackett 中的站点 ID，多个用空格分隔，例如 `blutopia-api`（站点要先在 Jackett 中添加好）。配置文件写 `dupe_indexers = ["blutopia-api"]`。
- 处理完成后，任务结果中列出每个站点已有的 DVD 原盘：标题（链接到站点上的种子页）、大小、做种数；格式（DVD5、DVD9、2xDVD9……）和制式（PAL、NTSC）都和这张盘相同的排在前面并标出。来源页选好片名后也可以点“查重”。
- 只看 DVD 原盘：Blu-ray、Remux、WEB-DL、压制和单个视频文件都不算。站点上的 DVD 标题按 Upload-Assistant 的写法（`Come and See AKA Idi i smotri 1985 PAL 2xDVD9 DD 5.1`）识别格式和制式。
- 只列出来，不判断是否重复：能不能发、能不能替换请按站点规则判断。同一站点同一部片的结果缓存一小时，不会反复请求。
- 已适配 Blutopia（Jackett 的 `blutopia-api`）。

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
