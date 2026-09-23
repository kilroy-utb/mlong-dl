#!/bin/bash
# 萌龍下載器 NAS Watch 版
# local GUI 把命令丟到 watch/queue/，cron 每 1 分鐘掃一次執行
#
# 安裝：
#   1. 把 watch_run.sh 跟 mlong-dl.py 一起放到 NAS（/volume1/scripts/mlong-dl/）
#   2. mkdir -p watch_queue watch_done
#   3. 加 cron: * * * * * /volume1/scripts/mlong-dl/watch_run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then source .env; fi
export MLONG_API_KEY="${MLONG_API_KEY:-190e8568de6f41f691012b7357572465}"

QUEUE_DIR="${QUEUE_DIR:-$SCRIPT_DIR/watch_queue}"
DONE_DIR="${DONE_DIR:-$SCRIPT_DIR/watch_done}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/volume1/video/mlong-dl}"

mkdir -p "$QUEUE_DIR" "$DONE_DIR" "$DOWNLOAD_DIR" logs

LOG="logs/watch_$(date +%Y%m%d).log"

# 掃 queue
count=0
for job_file in "$QUEUE_DIR"/*.job; do
    [ -e "$job_file" ] || continue
    fname=$(basename "$job_file")
    echo "▶ [$fname] $(date +%H:%M:%S)" | tee -a "$LOG"

    # 讀命令內容
    cmd=$(cat "$job_file")

    # 執行
    if python3 mlong-dl.py $cmd -o "$DOWNLOAD_DIR" 2>&1 | tee -a "$LOG"; then
        mv "$job_file" "$DONE_DIR/$fname.$(date +%s).ok"
        echo "✓ done" | tee -a "$LOG"
    else
        mv "$job_file" "$DONE_DIR/$fname.$(date +%s).fail"
        echo "✗ fail" | tee -a "$LOG"
    fi
    count=$((count + 1))
done

if [ $count -gt 0 ]; then
    echo "=== 本輪處理 $count 個任務 ===" | tee -a "$LOG"
fi