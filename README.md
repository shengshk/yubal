<div align="center">

# yubal

**[English](#english)** · **[简体中文](#简体中文)** · **[繁體中文](#繁體中文)**

Self-hosted YouTube Music downloader & library manager.

Fork of [guillevc/yubal](https://github.com/guillevc/yubal) → [shengshk/yubal](https://github.com/shengshk/yubal)

[![Upstream](https://img.shields.io/badge/upstream-guillevc%2Fyubal-blue)](https://github.com/guillevc/yubal)
[![Fork](https://img.shields.io/badge/fork-shengshk%2Fyubal-teal)](https://github.com/shengshk/yubal)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<picture>
  <img src="docs/demo.gif" alt="yubal demo" width="75%">
</picture>

</div>

<br/>

# English

Self-hosted YouTube Music downloader and library manager.

This repo is a fork of [guillevc/yubal](https://github.com/guillevc/yubal) ([shengshk/yubal](https://github.com/shengshk/yubal)). On top of “paste a link → tagged, organized files”, it adds a **dual-root hardlink library, external library, Sync Center, built-in login, Telegram**, and more.

## Main differences vs upstream

| Area | This fork | Upstream (typical) |
| --- | --- | --- |
| **Library layout** | `/data/{download,external,cache}` + `/config`; Download & External on **one mount** for hardlinks | `/app/data` + `/app/config` single library |
| **External library** | Raw → match YTM → Organized; hukou/sync policy; migrate ↔ Direct | No full external ingest pipeline |
| **Catalog / hardlinks** | Cross-folder hardlink dedupe, shared counts, quality pick | Dedup mainly via playlist references |
| **Sync Center** | Unified ledger for Direct / subscriptions / External | Job queue + subscription list |
| **Preselect / wash** | Prefer local library first, then scheduled upgrades | — |
| **Scheduler** | Jitter, Direct recover, cover/lyrics enrichment, Raw match, hardlink collapse | Subscription cron sync |
| **Lyrics / covers** | lrclib → YTM → **QQ Music**; Apple/iTunes cover search | lrclib + YTM |
| **Auth** | Built-in login (`YUBAL_AUTH_LOGIN`); or disable for reverse-proxy auth | Deploy-side auth |
| **UI** | **en / 简 / 繁** (default **English**); settings drawer, search, library health, PWA | English Web UI |
| **Telegram** | Bot: search / preview / download / subscribe; optional local Bot API | — |

Container layout:

```
/data/
├── download/          # direct downloads & subscriptions
├── external/
│   ├── raw/           # external originals
│   └── organized/     # after match
└── cache/             # download scratch (SSD optional)
/config/               # settings, DB, cookies
```

> **Hardlinks:** `download` and `external` must share one filesystem. Prefer `./data:/data`. Split mounts are OK only on the same partition.

## Features

**From upstream:** Web UI, albums/playlists/tracks, scheduled subscriptions, M3U, synced lyrics, ReplayGain, formats, [browser extension](extension/README.md), [CLI](packages/yubal/src/yubal/cli/README.md), media-server ready.

**Fork extras:** Sync Center ledger, external library + hardlink dedupe, built-in login, QQ lyrics & Apple covers, Telegram bot (optional `tgapi`), trilingual UI + PWA.

## Browser extension

<p>
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-track.png" alt="Track view" width="32%">
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-playlist.png" alt="Playlist view" width="32%">
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-settings.png" alt="Settings view" width="32%">
</p>
<p>
  <a href="https://addons.mozilla.org/addon/yubal/"><img src="https://img.shields.io/badge/Firefox-get_add--on-FF7139?logo=firefox&logoColor=white&style=for-the-badge" alt="Get the add-on for Firefox"></a>
  <a href="https://github.com/guillevc/yubal/releases?q=🧩"><img src="https://img.shields.io/badge/Chrome-manual_install-4285F4?logo=googlechrome&logoColor=white&style=for-the-badge" alt="Chrome manual install"></a>
</p>

See [extension/README.md](extension/README.md).

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
> Set `PUID`/`PGID` to your host user (`id`). Ensure `./data` and `./config` are writable.

### Telegram & `tgapi`

- Bot token / admin IDs are configured in the Web **Settings** drawer (not compose).
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
| `YUBAL_LIBRARY_ROOT` | Dual-library root | `/data` |

More options: `packages/api/src/yubal_api/settings.py`. Many prefs are also editable in the Web **Settings** drawer.

## Media servers

Mount `/data/download` (and `external/organized` if needed).

| Server | Artists | Playlists |
| --- | --- | :---: |
| **Navidrome** | Works out of the box | ✅ |
| **Jellyfin** | Enable “Use non-standard artists tags” | ✅ |
| **Gonic** | `GONIC_MULTI_VALUE_ARTIST=multi` | ❌ (limited M3U) |

## Cookies (optional)

For age-restricted content, private playlists, Liked Music (`list=LM`), or Premium quality:

1. Export `youtube.com` cookies per [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
2. Place at `config/ytdlp/cookies.txt` or upload in the Web UI

> [!CAUTION]
> Cookies may increase rate limits and account risk. See upstream [#3](https://github.com/guillevc/yubal/issues/3) and the [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube).

## Acknowledgments

- Upstream: [guillevc/yubal](https://github.com/guillevc/yubal) — support the original author via [Ko-fi](https://ko-fi.com/guillevc) / [GitHub Sponsors](https://github.com/sponsors/guillevc)
- Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ytmusicapi](https://github.com/sigma67/ytmusicapi)

## License

[MIT](LICENSE)

<sub>For personal archiving only. Comply with YouTube’s Terms of Service and applicable copyright law.</sub>

---

# 简体中文

自托管 YouTube Music 下载器与曲库管理器。

本仓库是 [guillevc/yubal](https://github.com/guillevc/yubal) 的 fork（[shengshk/yubal](https://github.com/shengshk/yubal)），在「粘贴链接 → 打标签整理」之上，扩展了**双库硬链接、外部曲库、同步中心、内置登录、Telegram** 等能力。

## 与原项目的主要差异

| 方向 | 本 fork | 上游典型形态 |
| --- | --- | --- |
| **库布局** | `/data/{download,external,cache}` + `/config`；Download 与 External **同挂载**以便硬链接 | `/app/data` + `/app/config` 单库 |
| **外部曲库** | Raw → 匹配 YTM → Organized；户口/同步策略；可与 Direct 互迁 | 无完整外部入库管线 |
| **曲目目录 / 硬链** | 跨目录硬链接去重、共享计数、质量择优 | 以 playlist 引用为主的去重 |
| **同步中心** | Direct / 订阅 / External 统一台账 | 任务队列 + 订阅列表 |
| **预选 / 洗版** | 本地库优先占坑，再按计划升级音质 | 无 |
| **调度** | 抖动错峰、Direct 恢复、封面/歌词 enrichment、Raw 匹配、硬链折叠 | 订阅 cron 同步为主 |
| **歌词 / 封面** | lrclib → YTM → **QQ Music**；Apple/iTunes 封面检索 | lrclib + YTM |
| **认证** | 内置登录（`YUBAL_AUTH_LOGIN`）；也可关掉改走反向代理鉴权 | 依赖部署侧鉴权 |
| **界面** | **英 / 简 / 繁**（默认 **English**）；设置抽屉、在线搜索、库健康、PWA | 英文 Web UI |
| **Telegram** | Bot：搜索 / 预览 / 下载 / 订阅；可选本地 Bot API | 无 |

目录约定（容器内）：

```
/data/
├── download/          # 直接下载、订阅列表
├── external/
│   ├── raw/           # 外部原文件
│   └── organized/     # 匹配整理后
└── cache/             # 下载暂存（可挂 SSD）
/config/               # 设置、数据库、cookies
```

> **硬链接：** `download` 与 `external` 须同一文件系统。推荐 `./data:/data`；拆挂勿跨盘。

## 功能概览

**沿用上游：** Web UI、专辑/歌单/单曲、订阅同步、M3U、歌词、ReplayGain、格式可选、[浏览器扩展](extension/README.md)、[CLI](packages/yubal/src/yubal/cli/README.md)、媒体库对接。

**本 fork：** 同步中心、外部曲库与硬链去重、内置登录、QQ 歌词与 Apple 封面、Telegram（可选 `tgapi`）、三语界面与 PWA。

## 浏览器扩展

<p>
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-track.png" alt="Track view" width="32%">
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-playlist.png" alt="Playlist view" width="32%">
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-settings.png" alt="Settings view" width="32%">
</p>
<p>
  <a href="https://addons.mozilla.org/addon/yubal/"><img src="https://img.shields.io/badge/Firefox-get_add--on-FF7139?logo=firefox&logoColor=white&style=for-the-badge" alt="Get the add-on for Firefox"></a>
  <a href="https://github.com/guillevc/yubal/releases?q=🧩"><img src="https://img.shields.io/badge/Chrome-manual_install-4285F4?logo=googlechrome&logoColor=white&style=for-the-badge" alt="Chrome manual install"></a>
</p>

详见 [extension/README.md](extension/README.md)。

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
> `PUID`/`PGID` 设为宿主机用户（`id`）。保证 `./data`、`./config` 可写。

### Telegram 与 `tgapi`

- Bot Token / 管理员 ID 在 Web **设置**抽屉里配置（不在 compose）。
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
| `YUBAL_LIBRARY_ROOT` | 双库公共根 | `/data` |

更多见 `packages/api/src/yubal_api/settings.py`；多数也可在 Web「设置」中调整。

## 媒体库对接

挂载 `/data/download`（需要时加上 `external/organized`）。

| 服务 | 艺人关联 | 播放列表 |
| --- | --- | :---: |
| **Navidrome** | 开箱可用 | ✅ |
| **Jellyfin** | 开启 “Use non-standard artists tags” | ✅ |
| **Gonic** | `GONIC_MULTI_VALUE_ARTIST=multi` | ❌（M3U 有限） |

## Cookies（可选）

年龄限制、私有歌单、Liked Music（`list=LM`）或 Premium：

1. 按 [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) 导出 cookies  
2. 放到 `config/ytdlp/cookies.txt` 或在 Web UI 上传  

> [!CAUTION]
> Cookie 可能加剧限流与账号风险。见上游 [#3](https://github.com/guillevc/yubal/issues/3) 与 [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)。

## 致谢

- 上游：[guillevc/yubal](https://github.com/guillevc/yubal) — [Ko-fi](https://ko-fi.com/guillevc) / [Sponsors](https://github.com/sponsors/guillevc)  
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)、[ytmusicapi](https://github.com/sigma67/ytmusicapi)

## License

[MIT](LICENSE)

<sub>仅供个人归档。请遵守 YouTube 服务条款及适用版权法律。</sub>

---

# 繁體中文

自託管 YouTube Music 下載器與曲庫管理器。

本倉庫是 [guillevc/yubal](https://github.com/guillevc/yubal) 的 fork（[shengshk/yubal](https://github.com/shengshk/yubal)），在「貼上連結 → 打標籤整理」之上，擴充了**雙庫硬連結、外部曲庫、同步中心、內建登入、Telegram** 等能力。

## 與原專案的主要差異

| 方向 | 本 fork | 上游典型形態 |
| --- | --- | --- |
| **庫佈局** | `/data/{download,external,cache}` + `/config`；Download 與 External **同掛載**以便硬連結 | `/app/data` + `/app/config` 單庫 |
| **外部曲庫** | Raw → 匹配 YTM → Organized；戶口/同步策略；可與 Direct 互遷 | 無完整外部入庫管線 |
| **曲目目錄 / 硬連結** | 跨目錄硬連結去重、共享計數、品質擇優 | 以 playlist 引用為主的去重 |
| **同步中心** | Direct / 訂閱 / External 統一臺帳 | 任務佇列 + 訂閱列表 |
| **預選 / 洗版** | 本地庫優先占坑，再依排程升級音質 | 無 |
| **排程** | 抖動錯峰、Direct 恢復、封面/歌詞 enrichment、Raw 匹配、硬連結摺疊 | 訂閱 cron 同步為主 |
| **歌詞 / 封面** | lrclib → YTM → **QQ Music**；Apple/iTunes 封面檢索 | lrclib + YTM |
| **認證** | 內建登入（`YUBAL_AUTH_LOGIN`）；也可關掉改走反向代理鑑權 | 依賴部署側鑑權 |
| **介面** | **英 / 簡 / 繁**（預設 **English**）；設定抽屜、線上搜尋、庫健康、PWA | 英文 Web UI |
| **Telegram** | Bot：搜尋 / 預覽 / 下載 / 訂閱；可選本地 Bot API | 無 |

目錄約定（容器內）：

```
/data/
├── download/          # 直接下載、訂閱列表
├── external/
│   ├── raw/           # 外部原檔
│   └── organized/     # 匹配整理後
└── cache/             # 下載暫存（可掛 SSD）
/config/               # 設定、資料庫、cookies
```

> **硬連結：** `download` 與 `external` 須同一檔案系統。建議 `./data:/data`；拆掛勿跨碟。

## 功能概覽

**沿用上游：** Web UI、專輯/歌單/單曲、訂閱同步、M3U、歌詞、ReplayGain、格式可選、[瀏覽器擴充](extension/README.md)、[CLI](packages/yubal/src/yubal/cli/README.md)、媒體庫對接。

**本 fork：** 同步中心、外部曲庫與硬連結去重、內建登入、QQ 歌詞與 Apple 封面、Telegram（可選 `tgapi`）、三語介面與 PWA。

## 瀏覽器擴充

<p>
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-track.png" alt="Track view" width="32%">
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-playlist.png" alt="Playlist view" width="32%">
  <img src="https://raw.githubusercontent.com/guillevc/yubal/refs/heads/master/extension/docs/images/extension-settings.png" alt="Settings view" width="32%">
</p>
<p>
  <a href="https://addons.mozilla.org/addon/yubal/"><img src="https://img.shields.io/badge/Firefox-get_add--on-FF7139?logo=firefox&logoColor=white&style=for-the-badge" alt="Get the add-on for Firefox"></a>
  <a href="https://github.com/guillevc/yubal/releases?q=🧩"><img src="https://img.shields.io/badge/Chrome-manual_install-4285F4?logo=googlechrome&logoColor=white&style=for-the-badge" alt="Chrome manual install"></a>
</p>

詳見 [extension/README.md](extension/README.md)。

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
> `PUID`/`PGID` 設為主機使用者（`id`）。確保 `./data`、`./config` 可寫。

### Telegram 與 `tgapi`

- Bot Token / 管理員 ID 在 Web **設定**抽屜裡設定（不在 compose）。
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
| `YUBAL_LIBRARY_ROOT` | 雙庫共用根 | `/data` |

更多見 `packages/api/src/yubal_api/settings.py`；多數也可在 Web「設定」調整。

## 媒體庫對接

掛載 `/data/download`（需要時加上 `external/organized`）。

| 服務 | 藝人關聯 | 播放列表 |
| --- | --- | :---: |
| **Navidrome** | 開箱可用 | ✅ |
| **Jellyfin** | 開啟 “Use non-standard artists tags” | ✅ |
| **Gonic** | `GONIC_MULTI_VALUE_ARTIST=multi` | ❌（M3U 有限） |

## Cookies（可選）

年齡限制、私人歌單、Liked Music（`list=LM`）或 Premium：

1. 依 [yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) 匯出 cookies  
2. 放到 `config/ytdlp/cookies.txt` 或在 Web UI 上傳  

> [!CAUTION]
> Cookie 可能加劇限流與帳號風險。見上游 [#3](https://github.com/guillevc/yubal/issues/3) 與 [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)。

## 致謝

- 上游：[guillevc/yubal](https://github.com/guillevc/yubal) — [Ko-fi](https://ko-fi.com/guillevc) / [Sponsors](https://github.com/sponsors/guillevc)  
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)、[ytmusicapi](https://github.com/sigma67/ytmusicapi)

## License

[MIT](LICENSE)

<sub>僅供個人歸檔。請遵守 YouTube 服務條款及適用著作權法律。</sub>
