"""
debug_perm.py - 最簡單的權限/路徑測試

跑： python debug_perm.py
"""
import os
import sys
from pathlib import Path

print("=" * 60)
print("Python 權限/路徑測試")
print("=" * 60)
print()

print("Python:", sys.executable)
print("USER:", os.environ.get('USERNAME', '?'))
print("CWD:", os.getcwd())
print()

test_paths = [
    r'C:\Users\USER\Downloads\mlong-dl',
    r'E:\movie',
    r'C:\Users\USER\Downloads',
r'E:' + chr(92),
    r'C:\Temp',
]

print("=" * 60)
print('測試每個路徑：')
for p in test_paths:
    path = Path(p)
    exists = path.exists()
    is_dir = path.is_dir() if exists else 'N/A'
    writable = os.access(str(path), os.W_OK) if exists else 'N/A'
    print(f'  {p}')
    print(f'    exists={exists}  is_dir={is_dir}  writable={writable}')
    if exists and path.is_dir():
        try:
            test = path / 'test_write.txt'
            test.write_text('ok', encoding='utf-8')
            test.unlink()
            print('    ✓ 寫檔成功')
        except Exception as e:
            print(f'    ✗ 寫檔失敗: {type(e).__name__}: {e}')
    print()

print("=" * 60)
print("環境變數：")
for var in ['TMP', 'TEMP', 'TMPDIR', 'PATHEXT', 'SYSTEMROOT', 'HOMEDRIVE', 'HOMEPATH']:
    val = os.environ.get(var, '(未設)')
    print(f'  {var}: {val}')
print()

print("=" * 60)
print('Python 預設 temp：')
import tempfile
temp_dir = tempfile.gettempdir()
print(f'  {temp_dir}')
try:
    test = Path(temp_dir) / 'test.txt'
    test.write_text('hi', encoding='utf-8')
    test.unlink()
    print('  ✓ temp 可寫')
except Exception as e:
    print(f'  ✗ temp 寫入失敗: {type(e).__name__}: {e}')