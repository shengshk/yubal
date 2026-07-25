<div align="center">

# yubal

**[English](#english)** · **[简体中文](#简体中文)** · **[繁體中文](#繁體中文)**

Self-hosted YouTube Music downloader, sync engine & library manager.

Fork of [guillevc/yubal](https://github.com/guillevc/yubal) → [shengshk/yubal](https://github.com/shengshk/yubal)

[![Upstream](https://img.shields.io/badge/upstream-guillevc%2Fyubal-blue)](https://github.com/guillevc/yubal)
[![Fork](https://img.shields.io/badge/fork-shengshk%2Fyubal-teal)](https://github.com/shengshk/yubal)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

> Heavily modified · **DB / layout not compatible with upstream** · 魔改较多 · 数据库与路径与原项目不通用

Thanks to **YouTube Music** for its publicly available APIs (consumed via [ytmusicapi](https://github.com/sigma67/ytmusicapi)) — this project would not exist without them.  
感谢 **YouTube Music** 开放 API（经 [ytmusicapi](https://github.com/sigma67/ytmusicapi) 使用）；本项目依赖于此。

<div align="center">

<img src="docs/demo.gif" alt="yubal demo" width="75%">

</div>

*Demo GIF from upstream [guillevc/yubal](https://github.com/guillevc/yubal) — reference only; will be updated later. · 演示 GIF 来自原项目，仅作参考。*

<br/>

# English

Self-hosted YouTube Music downloader, sync engine, and library manager.

This repo is a fork of [guillevc/yubal](https://github.com/guillevc/yubal) ([shengshk/yubal](https://github.com/shengshk/yubal)). On top of “paste a link → tagged, organized files”, it adds **system playlists, a shared sync pipeline, a three-root hardlink library, external-library ingest, built-in login, Telegram**, and more.

> [!WARNING]
> **This fork diverges heavily from upstream.** Do **not** reuse an upstream database, config layout, or `ghcr.io/guillevc/yubal` image expecting a drop-in upgrade. Paths (`/data/...` vs `/app/data`), schema, and features are incompatible — treat this as a **separate product** forked from yubal, not a compatible branch.
>
> The upstream **browser extension** is unchanged in this repo and is **not** adapted for this fork (built-in login will reject its API calls). It is not a supported feature here; use the Web UI (or disable built-in auth at your own risk). Prefer the official upstream project if you need the extension.

## Main differences vs upstream

| Area | This fork | Upstream (typical) |
| --- | --- | --- |
| **Library layout** | `/data/{download,external,wanted,cache}` + `/config`; media roots on **one mount** for hardlinks | `/app/data` + `/app/config` single library |
| **System playlists** | Download Center, Wanted, and account-bound Liked Music with stable UI identities | Regular jobs/subscriptions |
| **External library** | Raw → tag verification / YTM match → Organized; recovery/archive policies; move or link to Download Center | No full external ingest pipeline |
| **Catalog / hardlinks** | Cross-folder hardlink dedupe, inode-safe counts, quality selection, persistent source/asset state | Dedup mainly via playlist references |
| **Sync core** | One pipeline shared by playlist buttons, Sync All, and the scheduler; scope/timing are the only differences | Separate job and subscription flows |
| **Search** | YTM results first; QQ/MusicBrainz/etc. only add recordings missing from YTM results; version-aware cross-source dedupe | YTM search |
| **Preselect / wash** | Prefer local library first, then scheduled upgrades | — |
| **Metadata / assets** | Separate YTM identity match and QQ/MusicBrainz tag verification; lrclib/YTM/QQ lyrics; Apple/iTunes cover comparison | lrclib + YTM |
| **Safety** | Pre-migration DB backups, library health/repair checks, permission and hardlink validation | Basic application state |
| **Auth** | Built-in login (`YUBAL_AUTH_LOGIN`); or disable for reverse-proxy auth | Deploy-side auth |
| **UI** | **en / 简 / 繁**; system/subscription/external sections, library stats, settings drawer, health checks, PWA | English Web UI |
| **Telegram** | Bot: search / preview / download / subscribe; optional local Bot API; audio sends cache Telegram `file_id` for instant re-send | — |

Container layout:

```
/data/
├── download/
│   ├── direct/        # Download Center
│   ├── liked/         # Liked Music (one bound YTM account)
│   └── sublist/       # subscription save folders
├── wanted/            # Wanted: unresolved metadata / reusable local files
├── external/
│   ├── raw/           # external originals and scan input
│   └── organized/     # verified / matched organized library
└── cache/             # download scratch (SSD optional)
/config/               # settings, SQLite DB, backups, cookies
```

> **Hardlinks:** `download`, `wanted`, and `external` must share one filesystem. Prefer `./data:/data`. Split mounts are OK only on the same partition.

## Features

**From upstream (core ideas):** Web UI, albums/playlists/tracks, scheduled subscriptions, M3U, synced lyrics, ReplayGain, formats, [CLI](packages/yubal/src/yubal/cli/README.md), media-server ready.

**Fork extras:** system playlists, unified sync ledger/pipeline, **Wanted**, account-safe Liked Music, external-library ingest, hardlink dedupe, library statistics/health, built-in login, QQ lyrics & Apple covers, Telegram bot (optional `tgapi`), trilingual UI + PWA.

## Library workflow

The Web UI is grouped by purpose:

| Section | Role | Default path |
| --- | --- | --- |
| **Search results** | Temporary YTM preview/download results plus unique third-party metadata supplements | not part of the permanent library |
| **System playlists** | Download Center, Wanted, and Liked Music | `/data/download/direct`, `/data/wanted`, `/data/download/liked` |
| **Subscriptions** | User-created YTM playlist subscriptions | under `/data/download/sublist` |
| **External library** | Imported files, tag verification, YTM matching, archive/recovery | `/data/external/raw` → `/data/external/organized` |

Search returns up to five YTM songs first. Enabled metadata providers (QQ Music, MusicBrainz, Discogs, Last.fm) only supplement recordings not already represented by YTM. Same title/artist/version with a close duration is merged; different artists, Live, remix, cover, and remaster variants remain separate. Metadata-only hits can enter Wanted instead of pretending to have a YTM video ID.

All normal sync entry points use the same core rules:

1. Check download/external library health.
2. Queue the subscriptions in scope.
3. Reconcile or recover Download Center.
4. Scan external files, verify tags, match YTM, organize, and enrich.
5. Reuse local files for Wanted, complete covers/lyrics, then fulfill YTM matches.
6. Verify and upgrade covers and synced lyrics across the library.
7. Collapse duplicate copies into hardlinks where safe.

The top library card uses `total · with ID/no ID · verified/unverified`. The effective total is **with ID + tag-verified without ID**; files sharing the same inode through hardlinks count once.

## Quick start

Pull the published image (do **not** use `ghcr.io/guillevc/yubal` — that lacks fork features).

Use the published example (pull image by default; self-build is commented inside):

```bash
cp compose.example.yaml compose.yaml
# edit TELEGRAM_API_ID / TELEGRAM_API_HASH if you enable tgapi
docker compose -f compose.yaml up -d
# http://localhost:8000
# If built-in login is on, finish account setup within the time window
# Self-build: in compose.yaml uncomment build: / image: yubal:local, then:
# docker compose up -d --build
```

See [`compose.example.yaml`](compose.example.yaml) for the full `yubal` + optional `tgapi` stack.

> [!TIP]
> Set `PUID`/`PGID` to your host user (`id -u` / `id -g`). Ensure all existing files under `./data` and `./config` belong to that account and are writable; mixed `root` ownership can prevent cover/lyrics state from being saved.

### Telegram & `tgapi`

- Bot token / admin IDs are configured in the Web **Settings** drawer (not compose).
- **Sending audio is not a full re-upload every time.** After the first successful send, yubal stores Telegram’s cloud `file_id` (per track). Later sends reuse that id for near-instant delivery (Telegram-side “秒传”), instead of uploading the file again.
- Without `YUBAL_TG_API_URL`, the bot uses the **official** Telegram Bot API (fine for small files).
- With local **`tgapi`** ([aiogram/telegram-bot-api](https://hub.docker.com/r/aiogram/telegram-bot-api)): set `YUBAL_TG_API_URL=http://tgapi:8081`, same compose network as yubal, and mount `./data:/data:ro` so large files can be uploaded by **path**. Fill `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org); never commit them.

## Configuration

| Variable | Description | Default (Docker) |
| --- | --- | --- |
| `PUID` / `PGID` | File ownership | `1000` |
| `YUBAL_TZ` | Timezone (scheduler) | `UTC` |
| `YUBAL_AUTH_LOGIN` | Built-in login; `false` → Traefik/Authelia etc. | `true` |
| `YUBAL_AUDIO_FORMAT` | `opus` / `mp3` / `m4a` | `mp3` |
| `YUBAL_AUDIO_QUALITY` | Transcode quality (`0` = best) | `0` |
| `YUBAL_SCHEDULER_ENABLED` | Scheduler master switch | `true` |
| `YUBAL_SCHEDULER_CRON` | Cron expression | `0 * * * *` |
| `YUBAL_FETCH_LYRICS` | Fetch from lrclib | `true` |
| `YUBAL_YTMUSIC_LYRICS_FALLBACK` | YTM lyrics fallback | `true` |
| `YUBAL_QQ_LYRICS_FALLBACK` | QQ Music lyrics fallback | `true` |
| `YUBAL_DOWNLOAD_UGC` | UGC → `unofficial/` | `false` |
| `YUBAL_REPLAYGAIN` | ReplayGain | `true` |
| `YUBAL_JOB_TIMEOUT_SECONDS` | Job timeout (seconds) | `1800` |
| `YUBAL_BASE_PATH` | Reverse-proxy subpath | — |
| `YUBAL_TG_API_URL` | Local Telegram Bot API (e.g. `http://tgapi:8081`) | empty = official API |
| `YUBAL_DATA` | Download library path | `/data/download` |
| `YUBAL_CONFIG` | Config directory | `/config` |
| `YUBAL_LIBRARY_ROOT` | Shared media-library root | `/data` |

More options: `packages/api/src/yubal_api/settings.py`. Many prefs are also editable in the Web **Settings** drawer.

## Media servers

Mount `/data/download` (and `/data/external/organized` if needed).

| Server | Artists | Playlists |
| --- | --- | :---: |
| **Navidrome** | Works out of the box | ✅ |
| **Jellyfin** | Enable “Use non-standard artists tags” | ✅ |
| **Plex / Plexamp** | Works out of the box (point Plex library at the mount) | ✅ |
| **Gonic** | `GONIC_MULTI_VALUE_ARTIST=multi` | ❌ (limited M3U) |

## Cookies (optional)

For age-restricted content, private playlists, Liked Music (`list=LM`), or Premium quality:

1. Export `youtube.com` cookies per [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
2. Place at `config/ytdlp/cookies.txt` or upload in the Web UI

> [!CAUTION]
> Cookies may increase rate limits and account risk. See upstream [#3](https://github.com/guillevc/yubal/issues/3) and the [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube).

## Acknowledgments

- **YouTube Music** open / public APIs — metadata & catalog access via [ytmusicapi](https://github.com/sigma67/ytmusicapi)
- Upstream: [guillevc/yubal](https://github.com/guillevc/yubal) — support the original author via [Ko-fi](https://ko-fi.com/guillevc) / [GitHub Sponsors](https://github.com/sponsors/guillevc)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for downloads

## Disclaimer

This software is provided **as-is**, for **personal archiving and self-hosted library management** only. You are solely responsible for complying with [YouTube’s Terms of Service](https://www.youtube.com/t/terms), copyright law, and any other applicable rules. The authors and contributors are **not** liable for misuse, account restrictions, data loss, or legal consequences arising from your use of this project. Do not use it to redistribute copyrighted content.

## License

[MIT](LICENSE)

---

# 简体中文

自托管 YouTube Music 下载器、同步引擎与曲库管理器。

本仓库是 [guillevc/yubal](https://github.com/guillevc/yubal) 的 fork（[shengshk/yubal](https://github.com/shengshk/yubal)），在「粘贴链接 → 打标签整理」之上，扩展了**系统歌单、统一同步核心、三根曲库硬链接、外部曲库入库、内置登录、Telegram** 等能力。

> [!WARNING]
> **本 fork 魔改较多，与原项目不通用。** 请勿把上游数据库 / 配置目录 / `ghcr.io/guillevc/yubal` 镜像当作可原地升级。路径（`/data/...` vs `/app/data`）、库表结构与功能均不兼容——应视为基于 yubal 的**独立产品**，不是兼容分支。
>
> 仓库内仍保留上游的**浏览器扩展**源码，但**本 fork 未改、不适配**（开启内置登录时扩展调 API 会被拒）。本项目不把它当支持功能；请用 Web UI（若强行关闭内置登录自担风险）。需要扩展请用原项目。

## 与原项目的主要差异

| 方向 | 本 fork | 上游典型形态 |
| --- | --- | --- |
| **库布局** | `/data/{download,external,wanted,cache}` + `/config`；媒体根目录 **同挂载**以便硬链接 | `/app/data` + `/app/config` 单库 |
| **系统歌单** | 下载中心、心愿歌单、绑定账号的心爱歌单，界面名称稳定 | 普通任务与订阅 |
| **外部曲库** | Raw → 标签验证 / YTM 匹配 → Organized；归档与恢复策略；可迁移或硬链到下载中心 | 无完整外部入库管线 |
| **目录 / 硬链** | 跨目录硬链接去重、按 inode 真实计数、质量择优、持久化来源与刮削状态 | 以 playlist 引用为主的去重 |
| **同步核心** | 歌单按钮、立即同步、定时同步共用同一流程；仅范围和触发时机不同 | 任务与订阅流程相对独立 |
| **在线搜索** | YTM 优先；QQ/MusicBrainz 等只补充 YTM 结果没有的录音，并做版本感知的跨来源去重 | YTM 搜索 |
| **预选 / 洗版** | 本地库优先占坑，再按计划升级音质 | 无 |
| **匹配 / 刮削** | YTM 身份匹配与 QQ/MusicBrainz 标签验证分离；lrclib/YTM/QQ 歌词；Apple/iTunes 封面比较 | lrclib + YTM |
| **数据安全** | 数据库迁移前校验备份、曲库体检修复、权限与硬链接检查 | 基础应用状态 |
| **认证** | 内置登录（`YUBAL_AUTH_LOGIN`）；也可关掉改走反向代理鉴权 | 依赖部署侧鉴权 |
| **界面** | **英 / 简 / 繁**；系统歌单 / 订阅 / 外部曲库分区、曲库统计、设置抽屉、健康检查、PWA | 英文 Web UI |
| **Telegram** | Bot：搜索 / 预览 / 下载 / 订阅；可选本地 Bot API；发送后缓存云端 `file_id`，再次发送秒传 | 无 |

目录约定（容器内）：

```
/data/
├── download/
│   ├── direct/        # 下载中心
│   ├── liked/         # 心爱歌单（绑定一个 YTM 账号）
│   └── sublist/       # 订阅保存目录
├── wanted/            # 心愿歌单：待匹配资料与可复用本地文件
├── external/
│   ├── raw/           # 外部原文件与扫描入口
│   └── organized/     # 验证 / 匹配后的整理曲库
└── cache/             # 下载暂存（可挂 SSD）
/config/               # 设置、SQLite 数据库、备份、cookies
```

> **硬链接：** `download`、`wanted` 与 `external` 须同一文件系统。推荐 `./data:/data`；拆挂勿跨盘。

## 功能概览

**沿用上游（核心能力）：** Web UI、专辑/歌单/单曲、订阅同步、M3U、歌词、ReplayGain、格式可选、[CLI](packages/yubal/src/yubal/cli/README.md)、媒体库对接。

**本 fork：** 系统歌单、统一同步台账与核心、**心愿歌单**、账号安全的心爱歌单、外部曲库入库、硬链去重、曲库统计与体检、内置登录、QQ 歌词与 Apple 封面、Telegram（可选 `tgapi`）、三语界面与 PWA。

## 曲库工作方式

Web 界面按用途分为：

| 分区 | 作用 | 默认路径 |
| --- | --- | --- |
| **搜索结果** | 临时 YTM 试听 / 下载结果，以及第三方唯一补充结果 | 不属于永久曲库 |
| **系统歌单** | 下载中心、心愿歌单、心爱歌单 | `/data/download/direct`、`/data/wanted`、`/data/download/liked` |
| **订阅列表** | 用户创建的 YTM 歌单订阅 | `/data/download/sublist` 下 |
| **外部曲库** | 外来文件扫描、标签验证、YTM 匹配、归档与恢复 | `/data/external/raw` → `/data/external/organized` |

搜索先返回最多 5 条 YTM 结果；已启用的 QQ Music、MusicBrainz、Discogs、Last.fm 只补充 YTM 结果中没有的录音。同歌名、同歌手、同版本且时长接近时合并；不同歌手、Live、翻唱、Remix、重制版继续保留。只有歌曲资料、没有 YTM ID 的结果可以进入心愿歌单，不会伪装成可直接下载的 YTM 曲目。

所有正常同步入口共用同一套规则：

1. 检查下载库和外部曲库健康状态。
2. 将本次范围内的订阅加入任务。
3. 核对或补回下载中心。
4. 扫描外部文件，验证标签、匹配 YTM、整理并补齐资源。
5. 心愿歌单先复用本地文件，再补齐封面 / 歌词并兑现 YTM 匹配。
6. 全库检查并升级封面与同步歌词。
7. 在安全条件下将重复文件折叠为硬链接。

顶部曲库统计格式为 `总数 · 有 ID/无 ID · 已验证/未验证`。有效总数 = **有 ID + 无 ID 但标签验证通过**；同一 inode 的硬链接文件只计一次。

## 快速开始

拉取本 fork 发布镜像（勿用 `ghcr.io/guillevc/yubal`，缺少 fork 功能）。

使用发布示例（默认拉取镜像；自行构建在文件内注释）：

```bash
cp compose.example.yaml compose.yaml
# 若启用 tgapi，填写 TELEGRAM_API_ID / TELEGRAM_API_HASH
docker compose -f compose.yaml up -d
# 打开 http://localhost:8000
# 若开启内置登录，请在限定时间内完成账号初始化
# 自行构建：在 compose.yaml 取消注释 build: / image: yubal:local 后
# docker compose up -d --build
```

完整 `yubal` + 可选 `tgapi` 见 [`compose.example.yaml`](compose.example.yaml)。

> [!TIP]
> `PUID`/`PGID` 设为宿主机用户（`id -u` / `id -g`）。确保 `./data`、`./config` 内已有文件也属于该账号并可写；混入 `root` 属主会导致封面 / 歌词处理状态无法保存。

### Telegram 与 `tgapi`

- Bot Token / 管理员 ID 在 Web **设置**抽屉里配置（不在 compose）。
- **发送音频不是每次都重新传文件。** 首次发送成功后会记录 Telegram 云端 `file_id`（按曲目）；之后再发同一曲会用该 id **秒传**，无需再次上传。
- 不设 `YUBAL_TG_API_URL` 时，Bot 走 **官方** Telegram Bot API（小文件够用）。
- 启用本地 **`tgapi`**（[aiogram/telegram-bot-api](https://hub.docker.com/r/aiogram/telegram-bot-api)）：设 `YUBAL_TG_API_URL=http://tgapi:8081`，与 yubal **同 compose 网络**，并挂载 `./data:/data:ro`，才能按**路径**上传大文件。`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` 从 [my.telegram.org](https://my.telegram.org) 申请，**勿提交密钥**。

## 配置

| 变量 | 说明 | 默认（Docker） |
| --- | --- | --- |
| `PUID` / `PGID` | 文件属主 | `1000` |
| `YUBAL_TZ` | 时区 | `UTC` |
| `YUBAL_AUTH_LOGIN` | 内置登录；`false` 可走反向代理鉴权 | `true` |
| `YUBAL_AUDIO_FORMAT` | `opus` / `mp3` / `m4a` | `mp3` |
| `YUBAL_AUDIO_QUALITY` | 转码质量（0=最好） | `0` |
| `YUBAL_SCHEDULER_ENABLED` | 定时同步总开关 | `true` |
| `YUBAL_SCHEDULER_CRON` | 调度 cron | `0 * * * *` |
| `YUBAL_FETCH_LYRICS` | lrclib 歌词 | `true` |
| `YUBAL_YTMUSIC_LYRICS_FALLBACK` | YTM 歌词兜底 | `true` |
| `YUBAL_QQ_LYRICS_FALLBACK` | QQ Music 歌词兜底 | `true` |
| `YUBAL_DOWNLOAD_UGC` | UGC → `unofficial/` | `false` |
| `YUBAL_REPLAYGAIN` | ReplayGain | `true` |
| `YUBAL_JOB_TIMEOUT_SECONDS` | 任务超时（秒） | `1800` |
| `YUBAL_BASE_PATH` | 反向代理子路径 | — |
| `YUBAL_TG_API_URL` | 本地 Telegram Bot API | 空=官方 API |
| `YUBAL_DATA` | 下载库路径 | `/data/download` |
| `YUBAL_CONFIG` | 配置目录 | `/config` |
| `YUBAL_LIBRARY_ROOT` | 媒体曲库公共根 | `/data` |

更多见 `packages/api/src/yubal_api/settings.py`；多数也可在 Web「设置」中调整。

## 媒体库对接

挂载 `/data/download`（需要时加上 `/data/external/organized`）。

| 服务 | 艺人关联 | 播放列表 |
| --- | --- | :---: |
| **Navidrome** | 开箱可用 | ✅ |
| **Jellyfin** | 开启 “Use non-standard artists tags” | ✅ |
| **Plex / Plexamp** | 开箱可用（Plex 媒体库指向挂载目录即可） | ✅ |
| **Gonic** | `GONIC_MULTI_VALUE_ARTIST=multi` | ❌（M3U 有限） |

## Cookies（可选）

年龄限制、私有歌单、Liked Music（`list=LM`）或 Premium：

1. 按 [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) 导出 cookies  
2. 放到 `config/ytdlp/cookies.txt` 或在 Web UI 上传  

> [!CAUTION]
> Cookie 可能加剧限流与账号风险。见上游 [#3](https://github.com/guillevc/yubal/issues/3) 与 [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)。

## 致谢

- **YouTube Music** 开放 API — 经 [ytmusicapi](https://github.com/sigma67/ytmusicapi) 获取元数据与曲目信息  
- 上游：[guillevc/yubal](https://github.com/guillevc/yubal) — [Ko-fi](https://ko-fi.com/guillevc) / [Sponsors](https://github.com/sponsors/guillevc)  
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下载能力

## 免责声明

本软件按「现状」提供，**仅供个人归档与自托管曲库管理**。请自行遵守 [YouTube 服务条款](https://www.youtube.com/t/terms)、版权法及其他适用规定。作者与贡献者**不对**滥用、账号受限、数据丢失或由此产生的法律后果承担责任。请勿用于传播受版权保护的内容。

## License

[MIT](LICENSE)

---

# 繁體中文

自託管 YouTube Music 下載器、同步引擎與曲庫管理器。

本倉庫是 [guillevc/yubal](https://github.com/guillevc/yubal) 的 fork（[shengshk/yubal](https://github.com/shengshk/yubal)），在「貼上連結 → 打標籤整理」之上，擴充了**系統歌單、統一同步核心、三根曲庫硬連結、外部曲庫入庫、內建登入、Telegram** 等能力。

> [!WARNING]
> **本 fork 魔改較多，與原專案不通用。** 請勿把上游資料庫 / 設定目錄 / `ghcr.io/guillevc/yubal` 映像當作可原地升級。路徑（`/data/...` vs `/app/data`）、庫表結構與功能均不相容——應視為基於 yubal 的**獨立產品**，不是相容分支。
>
> 倉庫內仍保留上游的**瀏覽器擴充**原始碼，但**本 fork 未改、不適配**（開啟內建登入時擴充呼叫 API 會被拒）。本專案不把它當支援功能；請用 Web UI（若強行關閉內建登入自擔風險）。需要擴充請用原專案。

## 與原專案的主要差異

| 方向 | 本 fork | 上游典型形態 |
| --- | --- | --- |
| **庫佈局** | `/data/{download,external,wanted,cache}` + `/config`；媒體根目錄 **同掛載**以便硬連結 | `/app/data` + `/app/config` 單庫 |
| **系統歌單** | 下載中心、心願歌單、綁定帳號的心愛歌單，介面名稱穩定 | 一般任務與訂閱 |
| **外部曲庫** | Raw → 標籤驗證 / YTM 匹配 → Organized；歸檔與復原策略；可遷移或硬連結到下載中心 | 無完整外部入庫管線 |
| **目錄 / 硬連結** | 跨目錄硬連結去重、依 inode 真實計數、品質擇優、持久化來源與刮削狀態 | 以 playlist 引用為主的去重 |
| **同步核心** | 歌單按鈕、立即同步、排程同步共用同一流程；僅範圍與觸發時機不同 | 任務與訂閱流程相對獨立 |
| **線上搜尋** | YTM 優先；QQ/MusicBrainz 等只補充 YTM 結果沒有的錄音，並做版本感知的跨來源去重 | YTM 搜尋 |
| **預選 / 洗版** | 本地庫優先占坑，再依排程升級音質 | 無 |
| **匹配 / 刮削** | YTM 身分匹配與 QQ/MusicBrainz 標籤驗證分離；lrclib/YTM/QQ 歌詞；Apple/iTunes 封面比較 | lrclib + YTM |
| **資料安全** | 資料庫遷移前驗證備份、曲庫體檢修復、權限與硬連結檢查 | 基礎應用狀態 |
| **認證** | 內建登入（`YUBAL_AUTH_LOGIN`）；也可關掉改走反向代理鑑權 | 依賴部署側鑑權 |
| **介面** | **英 / 簡 / 繁**；系統歌單 / 訂閱 / 外部曲庫分區、曲庫統計、設定抽屜、健康檢查、PWA | 英文 Web UI |
| **Telegram** | Bot：搜尋 / 預覽 / 下載 / 訂閱；可選本地 Bot API；發送後快取雲端 `file_id`，再次發送秒傳 | 無 |

目錄約定（容器內）：

```
/data/
├── download/
│   ├── direct/        # 下載中心
│   ├── liked/         # 心愛歌單（綁定一個 YTM 帳號）
│   └── sublist/       # 訂閱儲存目錄
├── wanted/            # 心願歌單：待匹配資料與可重用本地檔案
├── external/
│   ├── raw/           # 外部原檔與掃描入口
│   └── organized/     # 驗證 / 匹配後的整理曲庫
└── cache/             # 下載暫存（可掛 SSD）
/config/               # 設定、SQLite 資料庫、備份、cookies
```

> **硬連結：** `download`、`wanted` 與 `external` 須同一檔案系統。建議 `./data:/data`；拆掛勿跨碟。

## 功能概覽

**沿用上游（核心能力）：** Web UI、專輯/歌單/單曲、訂閱同步、M3U、歌詞、ReplayGain、格式可選、[CLI](packages/yubal/src/yubal/cli/README.md)、媒體庫對接。

**本 fork：** 系統歌單、統一同步臺帳與核心、**心願歌單**、帳號安全的心愛歌單、外部曲庫入庫、硬連結去重、曲庫統計與體檢、內建登入、QQ 歌詞與 Apple 封面、Telegram（可選 `tgapi`）、三語介面與 PWA。

## 曲庫運作方式

Web 介面依用途分為：

| 分區 | 作用 | 預設路徑 |
| --- | --- | --- |
| **搜尋結果** | 暫時 YTM 試聽 / 下載結果，以及第三方唯一補充結果 | 不屬於永久曲庫 |
| **系統歌單** | 下載中心、心願歌單、心愛歌單 | `/data/download/direct`、`/data/wanted`、`/data/download/liked` |
| **訂閱列表** | 使用者建立的 YTM 歌單訂閱 | `/data/download/sublist` 下 |
| **外部曲庫** | 外來檔案掃描、標籤驗證、YTM 匹配、歸檔與復原 | `/data/external/raw` → `/data/external/organized` |

搜尋先回傳最多 5 條 YTM 結果；已啟用的 QQ Music、MusicBrainz、Discogs、Last.fm 只補充 YTM 結果中沒有的錄音。同歌名、同歌手、同版本且時長接近時合併；不同歌手、Live、翻唱、Remix、重製版繼續保留。只有歌曲資料、沒有 YTM ID 的結果可以進入心願歌單，不會偽裝成可直接下載的 YTM 曲目。

所有正常同步入口共用同一套規則：

1. 檢查下載庫與外部曲庫健康狀態。
2. 將本次範圍內的訂閱加入任務。
3. 核對或補回下載中心。
4. 掃描外部檔案，驗證標籤、匹配 YTM、整理並補齊資源。
5. 心願歌單先重用本地檔案，再補齊封面 / 歌詞並兌現 YTM 匹配。
6. 全庫檢查並升級封面與同步歌詞。
7. 在安全條件下將重複檔案摺疊為硬連結。

頂部曲庫統計格式為 `總數 · 有 ID/無 ID · 已驗證/未驗證`。有效總數 = **有 ID + 無 ID 但標籤驗證通過**；同一 inode 的硬連結檔案只計一次。

## 快速開始

拉取本 fork 發佈映像（勿用 `ghcr.io/guillevc/yubal`，缺少 fork 功能）。

使用發佈示例（預設拉取映像；自行建置在檔案內註解）：

```bash
cp compose.example.yaml compose.yaml
# 若啟用 tgapi，填寫 TELEGRAM_API_ID / TELEGRAM_API_HASH
docker compose -f compose.yaml up -d
# 開啟 http://localhost:8000
# 若開啟內建登入，請在限定時間內完成帳號初始化
# 自行建置：在 compose.yaml 取消註解 build: / image: yubal:local 後
# docker compose up -d --build
```

完整 `yubal` + 可選 `tgapi` 見 [`compose.example.yaml`](compose.example.yaml)。

> [!TIP]
> `PUID`/`PGID` 設為主機使用者（`id -u` / `id -g`）。確保 `./data`、`./config` 內既有檔案也屬於該帳號並可寫；混入 `root` 擁有者會導致封面 / 歌詞處理狀態無法儲存。

### Telegram 與 `tgapi`

- Bot Token / 管理員 ID 在 Web **設定**抽屜裡設定（不在 compose）。
- **發送音訊不是每次都重新傳檔。** 首次發送成功後會記錄 Telegram 雲端 `file_id`（依曲目）；之後再發同一曲會用該 id **秒傳**，無需再次上傳。
- 不設 `YUBAL_TG_API_URL` 時，Bot 走 **官方** Telegram Bot API（小檔案夠用）。
- 啟用本地 **`tgapi`**（[aiogram/telegram-bot-api](https://hub.docker.com/r/aiogram/telegram-bot-api)）：設 `YUBAL_TG_API_URL=http://tgapi:8081`，與 yubal **同 compose 網路**，並掛載 `./data:/data:ro`，才能依**路徑**上傳大檔案。`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` 從 [my.telegram.org](https://my.telegram.org) 申請，**勿提交密鑰**。

## 設定

| 變數 | 說明 | 預設（Docker） |
| --- | --- | --- |
| `PUID` / `PGID` | 檔案擁有者 | `1000` |
| `YUBAL_TZ` | 時區 | `UTC` |
| `YUBAL_AUTH_LOGIN` | 內建登入；`false` 可走反向代理鑑權 | `true` |
| `YUBAL_AUDIO_FORMAT` | `opus` / `mp3` / `m4a` | `mp3` |
| `YUBAL_AUDIO_QUALITY` | 轉碼品質（0=最好） | `0` |
| `YUBAL_SCHEDULER_ENABLED` | 排程總開關 | `true` |
| `YUBAL_SCHEDULER_CRON` | 排程 cron | `0 * * * *` |
| `YUBAL_FETCH_LYRICS` | lrclib 歌詞 | `true` |
| `YUBAL_YTMUSIC_LYRICS_FALLBACK` | YTM 歌詞後備 | `true` |
| `YUBAL_QQ_LYRICS_FALLBACK` | QQ Music 歌詞後備 | `true` |
| `YUBAL_DOWNLOAD_UGC` | UGC → `unofficial/` | `false` |
| `YUBAL_REPLAYGAIN` | ReplayGain | `true` |
| `YUBAL_JOB_TIMEOUT_SECONDS` | 任務逾時（秒） | `1800` |
| `YUBAL_BASE_PATH` | 反向代理子路徑 | — |
| `YUBAL_TG_API_URL` | 本地 Telegram Bot API | 空=官方 API |
| `YUBAL_DATA` | 下載庫路徑 | `/data/download` |
| `YUBAL_CONFIG` | 設定目錄 | `/config` |
| `YUBAL_LIBRARY_ROOT` | 媒體曲庫共用根 | `/data` |

更多見 `packages/api/src/yubal_api/settings.py`；多數也可在 Web「設定」調整。

## 媒體庫對接

掛載 `/data/download`（需要時加上 `/data/external/organized`）。

| 服務 | 藝人關聯 | 播放列表 |
| --- | --- | :---: |
| **Navidrome** | 開箱可用 | ✅ |
| **Jellyfin** | 開啟 “Use non-standard artists tags” | ✅ |
| **Plex / Plexamp** | 開箱可用（Plex 媒體庫指向掛載目錄即可） | ✅ |
| **Gonic** | `GONIC_MULTI_VALUE_ARTIST=multi` | ❌（M3U 有限） |

## Cookies（可選）

年齡限制、私人歌單、Liked Music（`list=LM`）或 Premium：

1. 依 [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) 匯出 cookies  
2. 放到 `config/ytdlp/cookies.txt` 或在 Web UI 上傳  

> [!CAUTION]
> Cookie 可能加劇限流與帳號風險。見上游 [#3](https://github.com/guillevc/yubal/issues/3) 與 [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)。

## 致謝

- **YouTube Music** 開放 API — 經 [ytmusicapi](https://github.com/sigma67/ytmusicapi) 取得中繼資料與曲目資訊  
- 上游：[guillevc/yubal](https://github.com/guillevc/yubal) — [Ko-fi](https://ko-fi.com/guillevc) / [Sponsors](https://github.com/sponsors/guillevc)  
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下載能力

## 免責聲明

本軟體依「現況」提供，**僅供個人歸檔與自託管曲庫管理**。請自行遵守 [YouTube 服務條款](https://www.youtube.com/t/terms)、著作權法及其他適用規定。作者與貢獻者**不對**濫用、帳號受限、資料遺失或由此產生的法律後果負責。請勿用於傳播受著作權保護的內容。

## License

[MIT](LICENSE)
