# NAS DS214 Play 部署 SOP

DS214 Play 規格：
- Marvell Armada 370 ARMv7 1.6 GHz
- **RAM 512 MB（吃緊）**
- DSM 6.x（無 Docker）
- 沒 GUI（不能用 mlong-dl 的 GUI）

**結論：能跑，但跑得辛苦。建議輕度使用。**

---

## 步驟 1：安裝 Python（DSM 6.x 用 synocommunity）

DSM → **Package Center** → **Settings** → **Trust Level** 設 **Any publisher**

裝以下套件（社群來源 synocommunity）：
1. **Python 3.8**（不是 3.5，太舊的 yt-dlp 不支援）
2. **ffmpeg**

---

## 步驟 2：SSH 進 NAS

DSM → **Control Panel** → **Terminal & SNMP** → Enable SSH

macOS/Linux：
```
ssh admin@your-nas-ip
sudo -i   # 拿 root
```

DSM 路徑：
```
/volume1/   ← 主要 volume
/volume2/   ← 第二顆硬碟（如果有）
/share/     ← 共享根（=volume1 內容）
```

---

## 步驟 3：建立工作目錄

```bash
mkdir -p /volume1/scripts/mlong-dl
cd /volume1/scripts/mlong-dl

# 放 Python 依賴
python3 -m pip install --user requests yt-dlp zhconv

# 上傳 mlong-dl.py（從你 local clone 下來）
# 用 scp 或 DSM File Station 拖進去
```

**DSM 6.x 預設 python3 是 python 3.5，要確認裝的 3.8 路徑**：
```bash
which python3
ls /usr/local/bin/python*   # synocommunity 通常裝在這
# 應該是 /usr/local/bin/python3.8 或類似
```

---

## 步驟 4：建立目錄結構

```bash
mkdir -p /volume1/video/mlong-dl     # 下載目錄（最終 mp4 放這）
mkdir -p /volume1/scripts/mlong-dl/logs
mkdir -p /volume1/scripts/mlong-dl/watch_queue
mkdir -p /volume1/scripts/mlong-dl/watch_done
```

---

## 步驟 5：部署 cron_run.sh（最重要）

```bash
cp cron_run.sh /volume1/scripts/mlong-dl/
chmod +x /volume1/scripts/mlong-dl/cron_run.sh
```

---

## 步驟 6：DS214 Play 記憶體優化

⚠️ **512MB RAM 很容易 OOM killer 殺掉 mlong-dl**。要這樣做：

### 6.1 — 關閉其他吃 RAM 的服務

DSM → **Package Center** → **Installed** → 停用：
- Antivirus Essential
- Cloud Sync（如果你不需要）
- Hyper Backup（如果不跑備份）
- Synology Photos AI（吃很多 RAM）

### 6.2 — 加 swap（如果還沒開）

DSM → **Control Panel** → **HDD/Storage** → **HDD/SSD Storage Pool** → 該 Storage Pool → **Manage** → **Swap** → 設 2GB

### 6.3 — cron 用 nice 降低優先度（避免搶 RAM）

在 `/etc/crontab` 加：
```
0 2 * * * root cd /volume1/scripts/mlong-dl && nice -n 19 ionice -c 3 ./cron_run.sh
```

---

## 步驟 7：加 Task Scheduler

DSM → **Control Panel** → **Task Scheduler** → **Create → Scheduled Task → User-defined script**

**General**：
- Task name: `mlong-dl-cron`
- User: root
- Schedule: Daily, 02:00

**Script**：
```
cd /volume1/scripts/mlong-dl
./cron_run.sh >> logs/cron_run.log 2>&1
```

---

## 步驟 8（選用）：本地 GUI 把任務送到 NAS

DSM → **Control Panel** → **File Services** → **SMB** → 啟用

Local 把 `\\your-nas\mlong-dl\watch_queue\` mount 成網路磁碟機

`watch_run.sh` cron 每 1 分鐘掃一次（**不要**頻繁，DS214 Play 處理不來）：
```
* * * * * root cd /volume1/scripts/mlong-dl && ./watch_run.sh >> logs/watch.log 2>&1
```

local GUI patch 我可以加「寫 .job 到 NAS 路徑」按鈕。

---

## ⚠️ DS214 Play 跑 mlong-dl 的限制

1. **不能 GUI 跑**：tkinter 吃 300MB，會 OOM
2. **不能同時跑 2 個 task**：load db.json 就 120MB，剩 380MB 不夠 OS 緩衝
3. **更新整個 DB（14 分鐘）會吃滿 RAM**：建議先用 local 端的 29MB db.json
4. **下載大檔案（10GB 4K）時**：yt-dlp 緩衝 1GB 會 OOM
5. **萌龍連線超時**：timeout 設長一點（建議 60 秒）

---

## 替代方案：mini PC 做下載機（推薦！）

DS214 Play 跑下載是「勉強」，**如果認真要 NAS 背景抓**，建議加一台 **$3000-5000 台幣的 mini PC**：

- **N100 / N305 mini PC**（16GB RAM，512GB SSD）— 一年電費 < $50
- 裝 **Ubuntu Server**（免費）
- 跑 mlong-dl **完整版**（GUI + Docker + Plex + 其他服務全開）

**投資報酬率**：DS214 Play 二手約 $1500-2500，但 RAM 512MB 無解。新 mini PC $3000-5000 解決所有問題。

或**雲端方案**：
- **Oracle Cloud Free Tier**：4 CPU + 24GB RAM，永久免費（ARM，萌龍不限速）
- **Hetzner Cloud**：€4/個月 (~NTD 130) 就有 4GB RAM
- **AWS Lightsail**：$3.5/月 ~NTD 110

---

## 給你的最後建議

1. **先試 DS214 Play 跑 cron**：看看實際體驗
3. **如果常 OOM 或太慢**：mini PC 或雲端二選一
2. **如果只是偶爾抓幾部**：local 端跑 GUI 就夠，不用 NAS

要不要我幫你：
- A) 加 local GUI 的「送到 NAS」按鈕？
- B) 寫個最小的 NAS-only build（精簡 db.json 記憶體用量）？
- C) 給你雲端部署的 quickstart？