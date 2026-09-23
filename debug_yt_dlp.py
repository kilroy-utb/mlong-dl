"""
debug_yt_dlp.py — 測 yt-dlp 在 local 端能不能找到

跑： python debug_yt_dlp.py
"""
import sys
from pathlib import Path

sys.path.insert(0, '.')

import importlib.util
spec = importlib.util.spec_from_file_location('mlong_dl_module', 'mlong-dl.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("=" * 60)
print("YT-DLP 偵錯工具 v1.5.7")
print("=" * 60)
print()

print(f"Python: {sys.executable}")
print(f"版本: {sys.version.split()[0]}")
print(f"平台: {sys.platform}")
print()

print("=" * 60)
print("1. 嘗試找 yt-dlp...")
print("=" * 60)
try:
    ytdlp = mod.find_ytdlp()
    print(f"✓ 找到 yt-dlp: {ytdlp}")

    # 真的能執行嗎？
    print()
    print("2. 測試能不能真的跑起來...")
    import subprocess
    if ytdlp.endswith('-m yt_dlp') or '-m yt_dlp' in ytdlp:
        cmd = ytdlp.replace('"', '').split()
    else:
        cmd = [ytdlp, '--version']
    print(f"  command: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        print(f"✓ 跑得動：{result.stdout.strip()}")
    else:
        print(f"✗ 跑失敗：{result.stderr.strip()}")
except RuntimeError as e:
    print(f"✗ 找不到 yt-dlp：")
    print()
    print(str(e))
    print()
    print("=" * 60)
    print("怎麼修：")
    print("=" * 60)
    print()
    print(f"  {sys.executable} -m pip install yt-dlp")
    print()
    print("或 重新跑 setup.sh：")
    print("  bash setup.sh")

print()
print("=" * 60)
print("3. 看 PATH 環境裡有沒有 yt-dlp...")
print("=" * 60)
import os
path_env = os.environ.get('PATH', '')
print(f"PATH dirs 數: {len(path_env.split(os.pathsep))}")

# 看 PATH 每個目錄裡有沒有 yt-dlp
print()
print("搜尋 PATH 裡的 yt-dlp:")
import shutil
for d in path_env.split(os.pathsep):
    if not d:
        continue
    candidate = Path(d) / ('yt-dlp.exe' if sys.platform == 'win32' else 'yt-dlp')
    if candidate.exists():
        print(f"  ✓ {candidate}")