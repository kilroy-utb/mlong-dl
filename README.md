# 萌龍下載器 (mlong-dl)

> 萌龍雅軒 (mlong.cutedragon.vip) 影片下載工具 — CLI + GUI

## 特色
- 🎯 **名字 → 下載**：輸入電影中文名 → 自動查 Item ID → 自動抓完整檔案
- 📦 **批量**：txt 清單一次抓多部
- 🖥 **GUI 完整版**：4 個 tab (搜尋/瀏覽/佇列/設定) + 即時進度 + 批次佇列 + 取消
- 💾 **本地 DB**：**17 個 folder × 共 12 萬 items**（電影 + 劇集，series/season/episode 全覆蓋）
- 🚀 **Range + multi-connection**：用 `yt-dlp` 直接抓 `original.mp4`，比 HLS 分段快 5-10 倍
- 📝 **可中斷續傳**：yt-dlp 自動 resume
- 🌏 **繁簡搜尋**：你打繁體、DB 簡體也找得到（內建對照表）
- ⚙ **記住設定**：config.json 存下載路徑/並發數/視窗大小

## 涵蓋內容（17 個萌龍雅軒 folder）

| 類型 | Folder | 預設抓取 |
|---|---|---|
| 電影 | 华语电影(3)、外语电影(515)、动画电影(3211)、恐怖电影(16018)、艺术电影(42629)、演唱会(65159) | Movie |
| 劇集 | 国产剧集(3359)、日韩剧集(5345)、欧美剧集(5510)、日番动漫(5604)、欧美动漫(32397)、国产动漫(46752)、儿童节目(48072)、综艺节目(65332)、纪录片(65336) | Series/Season/Episode |
| 特殊 | 合集(89934)、播放列表(105645) | Episode |

## 安裝

### macOS / Linux
```bash
# 1. 確認有 Python 3.8+
python3 --version

# 2. 安裝 yt-dlp + requests
pip3 install -r requirements.txt
# 或 macOS:
brew install yt-dlp

# 3. 設 api_key
export MLONG_API_KEY='190e8568de6f41f691012b7357572465'
```

### Windows
```powershell
# 1. 確認有 Python（建議 python.org 安裝版，已含 tkinter）
python --version

# 2. 安裝依賴
pip install -r requirements.txt

# 3. 設環境變數
setx MLONG_API_KEY "190e8568de6f41f691012b7357572465"
# 或在 PowerShell:
$env:MLONG_API_KEY = "190e8568de6f41f691012b7357572465"
```

## 第一次使用：建本地 DB

```bash
python3 mlong-dl.py update
```

會從萌龍同步所有電影到 `db.json`（約 1-2 分鐘，2047 部華語電影 + 外語電影）。

## 日常使用

### 0. 快速開始（推薦） — 直接抓預建 DB

```bash
git clone https://github.com/kilroy-utb/mlong-dl.git
cd mlong-dl
bash setup.sh    # 安裝依賴 + 提示下載 db.json (28MB)
export MLONG_API_KEY='190e8568de6f41f691012b7357572465'

# 直接可用，不必跑 14 分鐘 update：
python3 mlong-dl.py dl "阿凡達" -y
python3 mlong-dl.py gui
```

預建 db.json 在 GitHub Release v1.2.0 (124,283 items):
```
https://github.com/kilroy-utb/mlong-dl/releases/download/v1.2.0/db.json
```

### 1. CLI - 搜尋並下載
```bash
# 模糊搜尋並下載第一個匹配
python3 mlong-dl.py dl "阿凡達"

# 搜尋結果有多個時，指定 index
python3 mlong-dl.py dl "金鴨" --index 0

# 跳過確認、強制覆蓋
python3 mlong-dl.py dl "千與千尋" -y --redownload

# 純搜尋（不下載）
python3 mlong-dl.py search "金鴨"

# 用 Item ID 直接抓
python3 mlong-dl.py id 231525

# 批量（每行一個）
echo "阿凡達
千與千尋
3分钟先生" > movies.txt
python3 mlong-dl.py batch movies.txt

# 列所有電影（分頁）
python3 mlong-dl.py list
```

### GUI
```bash
python3 mlong-dl.py gui
```
- **4 個 tab**：
  - 🔍 搜尋：輸入中文（繁/簡自動轉換）→ 即時過濾 → 雙擊下載或加入佇列
  - 📚 瀏覽全部：類別（17 個 folder）/ 排序 / 多選批次
  - ⏬ 佇列：即時進度條 + 速度 + ETA + 取消按鈕
  - ⚙ 設定：下載路徑 / 並發數 / API Key / Server / Device ID（會存到 config.json）
- **背景 thread**：下載不凍結 GUI
- **可同時下載多部**（設並發數 1-8）
- **取消按鈕**：用 subprocess.terminate 中斷 yt-dlp
- **記住狀態**：視窗大小、上次搜尋自動保存
- **劇集顯示格式**：
  - Movie:    `[ 51465] 阿凡达 (2009) [179分]`
  - Series:   `[100000] 權力遊戲 (2011) [4320分] [Series]`
  - Season:   `[100100] 權力遊戲 - S01 [Season]`
  - Episode:  `[100103] 權力遊戲 S01E03 「凱特」 [52分]`

## 補齊萌龍雅軒新 Folder 的 SOP

萌龍偶爾會新增 folder。要補齊：

### 步驟 1：找出新 folder 的 ParentId

```bash
curl -s "https://mlong.cutedragon.vip:8888/Items?api_key=$MLONG_API_KEY&ParentId=2" | python3 -m json.tool
```

看 `Items[].Id` 與 `Items[].Name` — `CollectionType: "movies"` 是電影 folder、`tvshows` 是劇集、`boxsets`/`playlists` 是特殊。

### 步驟 2：編輯 `mlong-dl.py`

```python
KNOWN_FOLDERS = {
    ...,
    "新 folder 名": 12345,  # ← 加這個
}

FOLDER_TYPES = {
    ...,
    "新 folder 名": ['Movie'],  # 或 ['Series', 'Season', 'Episode']
}
```

### 步驟 3：跑 update

```bash
python3 mlong-dl.py update
# 會自動跳過已抓的 folder（去重），只抓新的
```

### 步驟 4：驗證

```bash
python3 mlong-dl.py search "新 folder 某部片"  # 找得到就好
```

## 已知 Folder（萌龍雅軒實際狀態，2026-09-23）

| Folder | ParentId | Items 數 | 類型 |
|---|---|---|---|
| 华语电影 | 3 | 1169 | movies |
| 外语电影 | 515 | 1543 | movies |
| 动画电影 | 3211 | 1256 | movies |
| 恐怖电影 | 16018 | 1444 | movies |
| 艺术电影 | 42629 | 1309 | movies |
| 演唱会 | 65159 | (少) | movies/series |
| 国产剧集 | 3359 | 32714 | tvshows |
| 日韩剧集 | 5345 | 8257 | tvshows |
| 欧美剧集 | 5510 | 14956 | tvshows |
| 日番动漫 | 5604 | 28653 | tvshows |
| 欧美动漫 | 32397 | 3112 | tvshows |
| 国产动漫 | 46752 | 5869 | tvshows |
| 儿童节目 | 48072 | 9882 | tvshows |
| 综艺节目 | 65332 | 12488 | tvshows |
| 纪录片 | 65336 | 1631 | tvshows |
| 合集 | 89934 | 0 | boxsets |
| 播放列表 | 105645 | 0 | playlists |
| **總計** | | **124,283** | |

## 預設下載位置
- macOS / Linux：`~/Downloads/mlong-dl/`
- Windows：`%USERPROFILE%\Downloads\mlong-dl\`

可用 `-o` 或 `--output` 改：
```bash
python3 mlong-dl.py dl "阿凡達" -o ~/Movies/
```

## 進階設定

### 環境變數
| 變數 | 用途 | 預設 |
|------|------|------|
| `MLONG_API_KEY` | 萌龍 api_key（永久 token）| 無，必要 |
| `MLONG_SERVER` | 萌龍 server URL | `https://mlong.cutedragon.vip:8888` |

### 已知 library folder
| Label | ParentId | 內容 |
|-------|----------|------|
| `chinese` | 3 | 华语电影 |
| `foreign` | 515 | 外语电影 |
| `anime` | ? | 动画电影（從萌龍 devtools 查） |

要加 folder：編輯 `mlong-dl.py` 的 `KNOWN_FOLDERS`。

## 常見問題

### 1. `ModuleNotFoundError: No module named 'tkinter'`
macOS 上 python.org 安裝版才內含 tkinter。
- macOS: `brew install python-tk` 或重新裝 python.org 版
- Linux: `sudo apt install python3-tk`
- Windows: 重新裝 Python 時勾選 "tcl/tk and IDLE"

### 2. `找不到 yt-dlp`
```bash
pip3 install yt-dlp
# 或
brew install yt-dlp  # macOS
```

### 3. `Guid should contain 32 digits`
api_key 失效或格式錯。重新從萌龍 devtools 抓：
1. 開瀏覽器登入萌龍
2. F12 → Network → 隨便點一部片
3. 找任一 `?api_key=xxxxx` 請求，複製 api_key 值
4. 設到 `MLONG_API_KEY`

### 4. 萌龍擋 `original.mp4`（少見，但會發生）
萌龍偶爾對某些片強制轉 HLS（擋 DirectPlay）。fallback 走 master playlist + ffmpeg 合併，問 issue。

### 5. 下載很慢
- 萌龍 transcoder 慢是常態
- 用 `-y --redownload` 不會變快
- 確認本地網路沒被防火牆擋萌龍 server

## 架構

```
使用者輸入:  "阿凡達"
    ↓
本地 DB 模糊搜尋 (db.json, 2047 部)
    ↓
找到 Item ID = 231525
    ↓
構造 URL: https://mlong.cutedragon.vip:8888/emby/videos/231525/original.mp4?DeviceId=xxx&api_key=xxx
    ↓
yt-dlp (Range + 8 並發)
    ↓
~/Downloads/mlong-dl/阿凡達.mp4
```

## 開發

### 修改萌龍 server URL / DeviceId
```python
DEFAULT_SERVER = "..."
DEFAULT_DEVICE_ID = "..."
```

### 加新 folder
編輯 `KNOWN_FOLDERS`，key 是 label，value 是 ParentId。

### 跑 unit test
目前無。改完手動測：
```bash
python3 mlong-dl.py update
python3 mlong-dl.py search "3分钟"
python3 mlong-dl.py dl "3分钟先生" --max-segments 5  # 不存在，跳過
```

## License
Personal use only.