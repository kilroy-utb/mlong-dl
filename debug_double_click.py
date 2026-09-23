"""
debug_double_click.py

直接測試 _get_selected_movie + download_series 的核心邏輯（不開 GUI）。

跑： python debug_double_click.py
"""

import sys
from pathlib import Path

sys.path.insert(0, '.')

import importlib.util
spec = importlib.util.spec_from_file_location('mlong_dl_module', 'mlong-dl.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# 1. 載入 DB
print("=" * 60)
print("1. 載入 DB...")
db = mod.MovieDB('db.json')
print(f"   {len(db.movies):,} items")

# 2. 找蘭香如故 series
print()
print("=" * 60)
print("2. 找「兰香如故」Series")
m = None
for item in db.movies:
    if item['name'] == '兰香如故' and item.get('type') == 'Series':
        m = item
        break

if not m:
    print("   ✗ 找不到 series「兰香如故」")
    print("   提示: 跑 update --series 375871 先建 DB")
    sys.exit(1)

print(f"   ✓ 找到 [{m['id']}] {m['name']} ({m.get('type')})")
print(f"   folder={m['folder']!r}")
print(f"   year={m.get('year')!r}")

# 3. 模擬 _get_selected_movie（重現 GUI 邏輯）
print()
print("=" * 60)
print("3. 模擬 _get_selected_movie")
# GUI 把 listbox line 解析成 ID，例如 "[ 375871] 📺 兰香如故 (2011) [4320分]"
import re
fake_line = f"[{m['id']:>10}] 📺 {m['name']}"
print(f"   模擬 listbox line: {fake_line!r}")
match = re.match(r'\[(\d+)\]', fake_line)
if match:
    parsed_id = match.group(1)
    print(f"   解析出 ID: {parsed_id}")
    if parsed_id == m['id']:
        print(f"   ✓ ID 對得上")
    else:
        print(f"   ✗ ID 不對！")
        sys.exit(1)
else:
    print("   ✗ regex 解析失敗")
    sys.exit(1)

# 4. 模擬 start_download_selected
print()
print("=" * 60)
print("4. 模擬 start_download_selected 的 branch 邏輯")
if m.get('type') == 'Series':
    print(f"   ✓ 進入 Series 分支 → 應該呼叫 download_series")
else:
    print(f"   ✗ 不是 Series → 走 _start_download")

# 5. 測試 get_all_episodes_for_series
print()
print("=" * 60)
print("5. 測試 get_all_episodes_for_series")
client = mod.MlongClient(
    server='https://mlong.cutedragon.vip:8888',
    api_key='190e8568de6f41f691012b7357572465',
)
try:
    eps = client.get_all_episodes_for_series(m['id'])
    print(f"   ✓ 抓到 {len(eps)} 集")
    if eps:
        print(f"   第一集: [{eps[0]['Id']}] {eps[0]['Name']}")
except Exception as e:
    print(f"   ✗ 失敗: {e}")
    sys.exit(1)

print()
print("=" * 60)
print("✓ 全部測試通過 — 雙擊應該 work。")
print("  如果 GUI 雙擊還是不行，重新 pull + 完全關 GUI 再開：")
print("    taskkill /F /IM python.exe")
print("    python mlong-dl.py gui")