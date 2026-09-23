#!/bin/bash
# 萌龍下載器 NAS Cron 版
# 適用：Synology Task Scheduler / QNAP crontab / Linux crontab
#
# 安裝步驟：
# 1. 把這個目錄放到 NAS，例如 /volume1/scripts/mlong-dl/
# 2. cd /volume1/scripts/mlong-dl
# 3. pip install -r requirements.txt  (或用 venv)
# 4. 編輯 volumes / NAS 設定 + 這個檔的 API_KEY
# 5. chmod +x cron_run.sh
# 6. 在 NAS Task Scheduler 加 cron: 0 2 * * * /volume1/scripts/mlong-dl/cron_run.sh

set -e

# === 設定 ===
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 讀 API key（從 .env 或環境）
if [ -f .env ]; then
    source .env
fi
export MLONG_API_KEY="${MLONG_API_KEY:-190e8568de6f41f691012b7357572465}"

# NAS 下載目錄
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/volume1/video/mlong-dl}"
mkdir -p "$DOWNLOAD_DIR"

# log
mkdir -p logs
LOG_FILE="logs/cron_$(date +%Y%m%d_%H%M).log"

echo "=== 萌龍下載器 cron 開始 $(date) ===" | tee -a "$LOG_FILE"

# 確保 DB 最新（如果超過 7 天沒更新就跑）
DB_AGE_DAYS=0
if [ -f db.json ]; then
    DB_AGE_DAYS=$(( ( $(date +%s) - $(stat -c %Y db.json) ) / 86400 ))
fi

if [ $DB_AGE_DAYS -gt 7 ]; then
    echo "DB 已 $DB_AGE_DAYS 天沒更新，先跑 update" | tee -a "$LOG_FILE"
    python3 mlong-dl.py update 2>&1 | tee -a "$LOG_FILE"
else
    echo "DB 年齡 $DB_AGE_DAYS 天，跳過 update" | tee -a "$LOG_FILE"
fi

# 讀 urls.txt（每行一個 series ID 或 movie 名稱）
URL_FILE="urls.txt"
if [ ! -f "$URL_FILE" ]; then
    echo "建立範例 urls.txt..." | tee -a "$LOG_FILE"
    cat > "$URL_FILE" <<'EOF'
# 萌龍下載器排程清單
# 格式：每行一個命令
#   series 375871          # 蘭香如故整個 series
#   dl "阿凡達"            # 搜尋後下載
#   id 375871              # 直接 ID
#   series 206906          # 愛回家
EOF
    exit 0
fi

# 跑 urls.txt 裡的命令
while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    echo "▶ $line" | tee -a "$LOG_FILE"
    python3 mlong-dl.py $line -o "$DOWNLOAD_DIR" 2>&1 | tee -a "$LOG_FILE"
done < "$URL_FILE"

echo "=== cron 完成 $(date) ===" | tee -a "$LOG_FILE"
# 保留最近 30 個 log
ls -t logs/cron_*.log | tail -n +31 | xargs -r rm --