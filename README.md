# 萌龍下載器 (mlong-dl)

> 萌龍雅軒 (mlong.cutedragon.vip) 影片下載工具 — CLI + GUI

## 特色
- 🎯 **名字 → 下載**：輸入電影中文名 → 自動查 Item ID → 自動抓完整檔案
- 📦 **批量**：txt 清單一次抓多部
- 🖥 **GUI 完整版**：4 個 tab (搜尋/瀏覽/佇列/設定) + 即時進度 + 批次佇列 + 取消
- 💾 **本地 DB**：第一次同步 2047 部電影後，之後本地搜尋（不用每次打萌龍）
- 🚀 **Range + multi-connection**：用 `yt-dlp` 直接抓 `original.mp4`，比 HLS 分段快 5-10 倍
- 📝 **可中斷續傳**：yt-dlp 自動 resume
- 🌏 **繁簡搜尋**：你打繁體、DB 簡體也找得到（內建對照表）
- ⚙ **記住設定**：config.json 存下載路徑/並發數/視窗大小

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

### CLI — 搜尋並下載
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
  - 📚 瀏覽全部：類別 / 排序 / 多選批次
  - ⏬ 佇列：即時進度條 + 速度 + ETA + 取消按鈕
  - ⚙ 設定：下載路徑 / 並發數 / API Key / Server / Device ID（會存到 config.json）
- **背景 thread**：下載不凍結 GUI
- **可同時下載多部**（設並發數 1-8）
- **取消按鈕**：用 subprocess.terminate 中斷 yt-dlp
- **記住狀態**：視窗大小、上次搜尋自動保存

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