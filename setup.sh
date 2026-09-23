#!/bin/bash
# 萌龍下載器 setup 腳本（macOS / Linux）
# 用法：bash setup.sh

# 不要 set -e，我們要一步一步 debug
set +e

trap 'echo "  [exit trap] line=$LINENO exit=$?"' ERR

echo "╔══════════════════════════════════════╗"
echo "║  萌龍下載器 mlong-dl 安裝              ║"
echo "╚══════════════════════════════════════╝"
echo

# 1. Python check
if ! command -v python3 &> /dev/null; then
    echo "✗ 找不到 python3"
    echo "  macOS: brew install python3 或裝 python.org 版本"
    echo "  Linux: sudo apt install python3"
    exit 1
fi
echo "✓ Python: $(python3 --version)"

# 2. pip install
echo ""
echo "▶ 安裝依賴 (requests + yt-dlp)..."
python3 -m pip install --user --upgrade -r requirements.txt

# 3. tkinter check (GUI 才需要)
echo ""
if python3 -c "import tkinter" 2>/dev/null; then
    echo "✓ tkinter: 可用 (GUI 模式)"
else
    echo "⚠ tkinter: 不可用（只能 CLI）"
    echo "  macOS: brew install python-tk"
    echo "  Linux: sudo apt install python3-tk"
fi

# 4. yt-dlp check
echo ""
if command -v yt-dlp &> /dev/null; then
    echo "✓ yt-dlp: $(yt-dlp --version)"
else
    echo "✗ 找不到 yt-dlp"
    echo "  macOS: brew install yt-dlp"
    echo "  Linux: pip install yt-dlp"
fi

# 5. api_key
echo ""
if [ -z "$MLONG_API_KEY" ]; then
    echo "⚠ MLONG_API_KEY 環境變數未設定"
    echo ""
    echo "請到萌龍 devtools 抓 api_key："
    echo "  1. 開瀏覽器登入萌龍雅軒"
    echo "  2. F12 → Network → 篩 m3u8 或 mp4"
    echo "  3. 找 api_key= 後面的 32 字元"
    echo ""
    read -p "貼上你的 api_key（Enter 跳過）: " KEY
    if [ -n "$KEY" ]; then
        echo "export MLONG_API_KEY='$KEY'" >> ~/.zshrc
        [ -f ~/.bashrc ] && echo "export MLONG_API_KEY='$KEY'" >> ~/.bashrc
        echo "✓ 已加到 ~/.zshrc 和 ~/.bashrc（新 terminal 才生效）"
        export MLONG_API_KEY="$KEY"
    else
        echo "跳過。記得之後手動 export。"
    fi
else
    echo "✓ MLONG_API_KEY 已設定"
fi

# 6. 提示：可從 release 下載預建 DB（3.5 MB 壓縮版，秒完成）
echo ""
echo "═══════════════════════════════════════"
echo "🚀 想要預建的 DB 嗎？（省 14 分鐘 update）"
echo ""
echo "  GitHub Release 上有 db.json.gz (3.5 MB 壓縮版，124,283 items)。"
echo "  解壓後是 28 MB 的 db.json。"
echo "  下載完成直接可用。"
echo ""
read -p "要下載嗎？[y/N]: " DL
if [[ "$DL" =~ ^[Yy]$ ]]; then
    DB_URL="https://github.com/kilroy-utb/mlong-dl/releases/download/v1.2.0/db.json.gz"

    # 偵測 python 命令
    if command -v python3 &> /dev/null; then
        PY=python3
    elif command -v python &> /dev/null; then
        PY=python
    else
        PY=""
    fi

    TARGET_DIR="$(pwd)"
    DB_GZ="$TARGET_DIR/db.json.gz"
    DB="$TARGET_DIR/db.json"
    rm -f "$DB_GZ" "$DB"

    if [[ -z "$PY" ]]; then
        echo "✗ 找不到 python，請手動下載 + 用 gunzip 解"
    else
        echo "▶ 下載 db.json.gz (3.5 MB)..."
        echo "  CWD: $TARGET_DIR"
        echo "  PY: $PY"

        # 多種方法 try，每個都印結果
        DOWNLOADED=no
        if [[ "$DOWNLOADED" == "no" ]] && command -v curl &> /dev/null; then
            echo "  [1/4] 用 curl..."
            curl -L -o "$DB_GZ" "$DB_URL" 2>&1 | tail -3
            [[ -s "$DB_GZ" ]] && DOWNLOADED=yes
            echo "      result: $DOWNLOADED"
        fi
        if [[ "$DOWNLOADED" == "no" ]] && command -v wget &> /dev/null; then
            echo "  [2/4] 用 wget..."
            wget -O "$DB_GZ" "$DB_URL" 2>&1 | tail -3
            [[ -s "$DB_GZ" ]] && DOWNLOADED=yes
            echo "      result: $DOWNLOADED"
        fi
        if [[ "$DOWNLOADED" == "no" ]] && command -v powershell.exe &> /dev/null; then
            echo "  [3/4] 用 PowerShell..."
            powershell.exe -NoProfile -Command "Invoke-WebRequest -Uri '$DB_URL' -OutFile '$DB_GZ' -UseBasicParsing" 2>&1 | tail -5
            [[ -s "$DB_GZ" ]] && DOWNLOADED=yes
            echo "      result: $DOWNLOADED"
        fi
        if [[ "$DOWNLOADED" == "no" ]] && command -v powershell &> /dev/null; then
            echo "  [4/4] 用 PowerShell (no .exe)..."
            powershell -NoProfile -Command "Invoke-WebRequest -Uri '$DB_URL' -OutFile '$DB_GZ' -UseBasicParsing" 2>&1 | tail -5
            [[ -s "$DB_GZ" ]] && DOWNLOADED=yes
            echo "      result: $DOWNLOADED"
        fi

        # 全部失敗 → 等手動下載
        if [[ "$DOWNLOADED" == "no" ]]; then
            echo ""
            echo "╔═══════════════════════════════════════════╗"
            echo "║  自動下載失敗（curl/wget/powershell 都不行）  ║"
            echo "╚═══════════════════════════════════════════╝"
            echo ""
            echo "  請手動下載："
            echo "  1. 瀏覽器開 https://github.com/kilroy-utb/mlong-dl/releases/download/v1.2.0/db.json.gz"
            echo "  2. 存到 $TARGET_DIR/db.json.gz"
            echo "  3. 按 Enter 繼續"
            echo ""
            read -p "  按 Enter..."
            [[ -s "$DB_GZ" ]] && DOWNLOADED=yes
        fi

        # 全部失敗（包括手動也沒放）→ 教用 python urllib 抓
        if [[ "$DOWNLOADED" == "no" ]]; then
            echo ""
            echo "  還是用 Python urllib 試："
            "$PY" -c "
import urllib.request, gzip
url = '$DB_URL'
print('  下載中...')
data = urllib.request.urlopen(url, timeout=30).read()
print(f'  下載 {len(data)} bytes')
data = gzip.decompress(data)
with open('$DB', 'wb') as f:
    f.write(data)
print(f'  ✓ 存到 $DB ({len(data)} bytes)')
" 2>&1 | tail -10
            [[ -s "$DB" ]] && DOWNLOADED=yes
        fi

        if [[ "$DOWNLOADED" == "yes" ]] && [[ -s "$DB" ]]; then
            rm -f "$DB_GZ"
            DB_SIZE=$(stat -c%s "$DB" 2>/dev/null || stat -f%z "$DB" 2>/dev/null)
            echo ""
            echo "✓ db.json 完成（$DB_SIZE bytes）"
        else
            echo ""
            echo "✗ 全部下載方法都失敗"
            echo "  跑 update 重建 DB：python mlong-dl.py update（會花 14 分鐘）"
        fi
    fi
fi

# 8. 用法
echo ""
echo "═══════════════════════════════════════"
echo "現在可以跑了："
echo ""
echo "  python3 mlong-dl.py dl '電影名'     # 搜尋 + 下載"
echo "  python3 mlong-dl.py gui             # 開 GUI（4 tab）"
echo "  python3 mlong-dl.py update          # 從萌龍重建 DB（如果資料過時）"
echo ""
echo "更多：python3 mlong-dl.py --help"