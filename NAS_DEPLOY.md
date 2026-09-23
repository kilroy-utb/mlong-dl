# NAS 部署（背景 24/7 下載）

這份 v3 設計成可在 NAS 上跑，讓你 local GUI 點完 → NAS 背景慢慢抓。

## 部署方式

### 方式 1 — Docker（推薦）

```bash
# 1. 複製整包到 NAS
scp -r mlong-dl/ user@nas:/volume1/scripts/

# 2. NAS 上編輯 docker-compose.yml（設定下載路徑 + API key）

# 3. 啟動
cd /volume1/scripts/mlong-dl
docker compose up -d

# 4. 跑命令（NAS 在背景跑）
docker exec mlong-dl python mlong-dl.py update
docker exec mlong-dl python mlong-dl.py series 375871 -o /downloads
```

### 方式 2 — Watch Folder（local + NAS 共享）

把 NAS 的一個資料夾 mount 到 local 當 SMB drive。local GUI 按下載時順便把命令寫到 `\\nas\mlong\watch_queue\蘭香如故.job`。

NAS 端 cron 每 1 分鐘跑 `watch_run.sh`，看到新 .job 就執行：

```bash
# NAS Task Scheduler 加這個 cron：
* * * * * /volume1/scripts/mlong-dl/watch_run.sh
```

local 寫入 `.job` 檔格式（一行命令）：
```
series 375871
```

`watch_run.sh` 會跑 `python mlong-dl.py series 375871 -o /downloads/`。

### 方式 3 — 純 Cron（無人值守）

編輯 `urls.txt`，NAS 每天凌晨 2 點自動跑：

```bash
# NAS Task Scheduler 加這個 cron：
0 2 * * * /volume1/scripts/mlong-dl/cron_run.sh
```

`urls.txt` 格式（每行一個命令）：
```
# 喜歡的劇集定期更新
update --series 375871
update --series 206906

# 定期下載新劇
series 2816223
```

## 共同前置

無論哪種方式，NAS 都要裝：
1. **Python 3.10+**
2. **ffmpeg**（驗證或用）
3. **yt-dlp**（apt/brew install 或 pip）
4. **requests + zhconv**（pip install）

## 設定檔

**`.env`**（給方案 B/C 用）：
```
MLONG_API_KEY=190e8568de6f41f691012b7357572465
DOWNLOAD_DIR=/volume1/video/mlong-dl
QUEUE_DIR=/volume1/scripts/mlong-dl/watch_queue
```

## 磁碟配置建議

| NAS 用途 | 容量估計 | 範例 |
|---|---|---|
| 4K HDR 電影 | 50-100 GB/部 | 阿凡達 70 GB |
| 1080p 電影 | 5-15 GB/部 | 大部分 8 GB |
| 4K 影集 | 5-10 GB/集 | 一季 50 GB |
| 1080p 影集 | 1-3 GB/集 | 一季 20 GB |

愛回家 2766 集 × 2 GB ≈ **5.5 TB** 整個 series。

## 安全

1. **API key 別 commit**：放 .env，加到 .gitignore
2. **downloads 用專用 volume**：不要放在 system volume
3. **磁碟配額**：用 NAS 的 quota 限制 `/downloads/`
4. **log rotation**：cron_run.sh 內建保留 30 個 log