"""
debug_download_path.py — 找 WinError 5 真正路徑

跑： python debug_download_path.py
"""
import sys, os
from pathlib import Path

sys.path.insert(0, '.')

import importlib.util
spec = importlib.util.spec_from_file_location('mlong_dl_module', 'mlong-dl.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("=" * 60)
print("下載路徑診斷")
print("=" * 60)
print()

# 1. 載入 config
cfg = mod.Config()
print(f"下載目錄 (config.download_dir): {cfg.get('download_dir')!r}")
print(f"DEFAULT_DOWNLOAD_DIR:           {mod.DEFAULT_DOWNLOAD_DIR!r}")
print(f"platform: {sys.platform}")
print()

dl = Path(cfg.get('download_dir'))
print(f"下載目錄 Path: {dl!r}")
print(f"  exists? {dl.exists()}")
print(f"  is_dir?  {dl.is_dir() if dl.exists() else 'N/A'}")
print(f"  writable? {os.access(str(dl), os.W_OK) if dl.exists() else 'N/A'}")
print()

# 2. 試寫檔案
print("=" * 60)
print("2. 試寫檔案到下載目錄...")
print("=" * 60)
test_file = dl / "test_write.txt"
try:
    test_file.write_text("hello", encoding='utf-8')
    print(f"✓ 寫檔成功：{test_file}")
    test_file.unlink()
    print("✓ 刪除成功")
except Exception as e:
    print(f"✗ 失敗：{type(e).__name__}: {e}")
    print()
    print("詳細 traceback:")
    import traceback
    traceback.print_exc()
print()

# 3. 試 mkdir
print("=" * 60)
print("3. 試 mkdir 下載目錄...")
print("=" * 60)
try:
    dl.mkdir(parents=True, exist_ok=True)
    print(f"✓ mkdir 成功（或已存在）: {dl}")
except Exception as e:
    print(f"✗ mkdir 失敗：{type(e).__name__}: {e}")
    print()
    print("常見原因：")
    print("  - 路徑不存在但父目錄也沒權限建")
    print("  - 路徑在只讀磁碟 / NAS")
    print("  - 父路徑拼錯（拼到 system 目錄）")
print()

# 4. 試父目錄
print("=" * 60)
print("4. 試寫到父目錄...")
print("=" * 60)
parent = dl.parent
print(f"父目錄: {parent!r}")
print(f"  exists?  {parent.exists()}")
print(f"  is_dir?  {parent.is_dir() if parent.exists() else 'N/A'}")
print(f"  writable? {os.access(str(parent), os.W_OK) if parent.exists() else 'N/A'}")
print()
if parent.exists():
    try:
        test = parent / "test_write_parent.txt"
        test.write_text("hi", encoding='utf-8')
        print(f"✓ 寫到父目錄成功：{test}")
        test.unlink()
    except Exception as e:
        print(f"✗ 父目錄寫入失敗：{type(e).__name__}: {e}")

# 5. 環境資訊
print()
print("=" * 60)
print("5. 環境資訊")
print("=" * 60)
print(f"Python:  {sys.executable}")
print(f"CWD:     {os.getcwd()}")
print(f"USER:    {os.environ.get('USERNAME', '?')}")
print(f"HOME:    {os.environ.get('USERPROFILE', '?')}")