#!/bin/bash
# 萌龍下載器 setup 腳本（macOS / Linux）
# 用法：bash setup.sh

set -e

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

# 6. 提示：可從 release 下載預建 DB（28 MB，省 14 分鐘）
echo ""
echo "═══════════════════════════════════════"
echo "🚀 想要預建的 DB 嗎？（省 14 分鐘 update）"
echo ""
echo "  GitHub Release 已上傳 db.json (28 MB, 124,283 items)。"
echo "  下載後直接可用，不用跑 update。"
echo ""
read -p "要下載嗎？[y/N]: " DL
if [[ "$DL" =~ ^[Yy]$ ]]; then
    DB_URL="https://github.com/kilroy-utb/mlong-dl/releases/download/v1.2.0/db.json"
    echo "▶ 下載 db.json (28 MB)..."
    if command -v curl &> /dev/null; then
        curl -L -o db.json "$DB_URL" && echo "✓ db.json 下載完成"
    elif command -v wget &> /dev/null; then
        wget -O db.json "$DB_URL" && echo "✓ db.json 下載完成"
    else
        echo "✗ 找不到 curl 或 wget"
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