# 萌龍下載器 — NAS 版 Dockerfile
# 適用：Synology Container Manager / QNAP Container Station / 自架 Linux Docker

FROM python:3.13-slim

# 系統依賴（NAS 通常 ARM/x86 Linux）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案
COPY mlong-dl.py .
COPY db.json ./
COPY config.json ./

# 預設下載目錄（用 NAS 的 shared volume 掛載覆蓋）
ENV MLONG_DOWNLOAD_DIR=/downloads
ENV MONG_API_KEY=""

# 預設命令：show help
CMD ["python", "mlong-dl.py", "--help"]