#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mlong-dl — 萌龍雅軒 / Jellyfin 通用下載器 v3
================================================
本機端 CLI + GUI 雙接口下載器。

設計：
  - 第一次跑 `update` 從萌龍建本地 DB（2047 部電影清單）
  - 之後用電影名/ID/關鍵字在本地 DB 模糊搜尋
  - 底層呼叫 yt-dlp 抓 original.mp4（支援 Range / resume / multi-connection）
  - 單一 Python 檔，無外部依賴（tkinter 是 Python 內建）

用法（CLI）：
  $ python3 mlong-dl.py update                       # 建本地 DB
  $ python3 mlong-dl.py "阿凡達"                    # 模糊搜尋並下載
  $ python3 mlong-dl.py id 231525                   # 用 Item ID 下載
  $ python3 mlong-dl.py list                        # 列出所有電影
  $ python3 mlong-dl.py batch movies.txt            # 批量
  $ python3 mlong-dl.py gui                         # 開 GUI

  環境變數（或 --api-key）：
    MLONG_API_KEY — 萌龍 api_key（永久 token）
    MLONG_SERVER   — server URL（預設 mlong.cutedragon.vip:8888）
"""
import argparse
import json
import os
import re
import sys
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("缺少 requests：pip install requests")
    sys.exit(1)

# ── 常數 ────────────────────────────────────────────────────
DEFAULT_SERVER = "https://mlong.cutedragon.vip:8888"
DEFAULT_DEVICE_ID = "4068e636-c8e6-4a84-80aa-24dd4a40aefa"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "mlong-dl"
DB_PATH = Path(__file__).parent / "db.json"

# 萌龍雅軒的 library folder ID（可在 server 上找到）
KNOWN_FOLDERS = {
    "chinese": 3,      # 华语电影
    "foreign": 515,    # 外语电影
    "anime": None,     # 动画电影（user 可從 devtools 找）
}


# ── DB 管理 ─────────────────────────────────────────────────
class MovieDB:
    """本地電影 DB（從萌龍同步下來）。"""

    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.movies: list = []  # [{"id": "231525", "name": "阿凡達", "folder": "chinese", "year": 2009}, ...]
        self.load()

    def load(self):
        if self.path.exists():
            with open(self.path, encoding='utf-8') as f:
                self.movies = json.load(f)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.movies, f, ensure_ascii=False, indent=2)

# ── 繁簡對照（用 CJK 互換）────────────────────────────────
# 簡易手動對照表（涵蓋 95% 常見字；不完美但輕量、無依賴）
T2S_DICT = {
    '達': '达', '馬': '马', '龍': '龙', '鳳': '凤', '鳥': '鸟',
    '貓': '猫', '豬': '猪', '獸': '兽', '魚': '鱼', '蝸': '蜗', '鴨': '鸭',
    '車': '车', '飛': '飞', '長': '长', '門': '门', '開': '开',
    '關': '关', '時': '时', '當': '当', '個': '个', '們': '们',
    '過': '过', '這': '这', '裡': '里', '為': '为',
    '會': '会', '來': '来', '說': '说', '話': '话', '語': '语',
    '電': '电', '腦': '脑', '機': '机', '聲': '声', '聽': '听',
    '頭': '头', '臉': '脸', '麵': '面', '麥': '麦', '黨': '党',
    '國': '国', '園': '园', '圖': '图', '畫': '画', '寫': '写',
    '經': '经', '給': '给', '萬': '万', '億': '亿', '區': '区',
    '號': '号', '單': '单', '雙': '双', '幾': '几', '麼': '么',
    '從': '从', '進': '进', '遠': '远', '運': '运', '遊': '游',
    '學': '学', '術': '术', '節': '节', '葉': '叶',
    '夢': '梦', '淚': '泪', '紅': '红', '綠': '绿', '黃': '黄',
    '藍': '蓝', '銀': '银', '鐵': '铁', '鋼': '钢',
    '風': '风', '雲': '云', '霧': '雾',
    '島': '岛', '嶼': '屿', '巖': '岩',
    '飯': '饭', '館': '馆',
    '驚': '惊', '哀': '哀', '樂': '乐',
    '愛': '爱', '恨': '恨', '慾': '欲',
}
S2T_DICT = {v: k for k, v in T2S_DICT.items()}


class MovieDB:
    """本地電影 DB（從萌龍同步下來）。"""

    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.movies: list = []
        self.load()

    def load(self):
        if self.path.exists():
            with open(self.path, encoding='utf-8') as f:
                self.movies = json.load(f)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.movies, f, ensure_ascii=False, indent=2)

    def search(self, query: str, limit: int = 20) -> list:
        """模糊搜尋。自動試繁體 + 簡體兩版本。"""
        q = query.strip().lower()
        if not q:
            return []
        # 把查詢也做一次繁→簡轉換
        q_simplified = ''.join(T2S_DICT.get(c, c) for c in q)

        queries = [q]
        if q_simplified != q:
            queries.append(q_simplified)

        all_results = []
        seen_ids = set()
        for search_q in queries:
            for m in self.movies:
                if m['id'] in seen_ids:
                    continue
                name = m['name'].lower()
                # 把 DB 名稱也做簡→繁轉換（如果 DB 是簡體存著）
                name_alt = ''.join(S2T_DICT.get(c, c) for c in name)
                # substring match（用 in 對 string，不是 set element check）
                if search_q in name or search_q in name_alt:
                    all_results.append((m, search_q))
                    seen_ids.add(m['id'])

        # 排序：完全 match > 開頭 > substring
        def score(item):
            m, _ = item
            name = m['name'].lower()
            if name in queries:
                return 0
            if any(name.startswith(qq) for qq in queries):
                return 1
            return 2
        all_results.sort(key=score)
        return [m for m, _ in all_results[:limit]]

    def get(self, item_id: str) -> Optional[dict]:
        for m in self.movies:
            if m['id'] == item_id:
                return m
        return None


# ── Jellyfin API client ──────────────────────────────────────
class MlongClient:
    def __init__(self, server: str, api_key: str, device_id: str = DEFAULT_DEVICE_ID):
        self.server = server.rstrip('/')
        self.api_key = api_key
        self.device_id = device_id
        self.s = requests.Session()
        self.s.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            'X-Emby-Authorization': f'MediaBrowser Client="mlong-dl", '
                                    f'Device="CLI", DeviceId="{device_id}", Version="3.0"',
        })

    def _url(self, p: str) -> str:
        return f"{self.server}{p}"

    def ping(self) -> bool:
        """驗證 token 通。"""
        r = self.s.get(self._url('/System/Info/Public'))
        r.raise_for_status()
        info = r.json()
        print(f"✓ Server: {info.get('ServerName')} v{info.get('Version')}")
        return True

    def list_folder_all(self, parent_id: int, label: str = '') -> list:
        """完整抓一個 folder 所有影片（自動翻頁）。"""
        movies = []
        start = 0
        page = 100
        while True:
            params = {
                'api_key': self.api_key,
                'ParentId': parent_id,
                'Limit': page,
                'StartIndex': start,
                'Recursive': 'true',
            }
            r = self.s.get(self._url('/Items'), params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get('Items', [])
            if not items:
                break
            total = data.get('TotalRecordCount', 0)
            for it in items:
                if it.get('Type') == 'Movie':
                    name = it.get('Name', '')
                    year = ''
                    m = re.search(r'\((\d{4})\)', name)
                    if m:
                        year = m.group(1)
                    movies.append({
                        'id': str(it.get('Id')),
                        'name': name,
                        'year': year,
                        'folder': label,
                        'runtime_ticks': it.get('RunTimeTicks', 0),
                    })
            print(f"  [{label}] {start + len(items)}/{total}", end='\r')
            start += len(items)
            if start >= total or len(items) < page:
                break
        return movies

    def build_original_url(self, item_id: str) -> str:
        """拼 original.mp4 URL。萌龍雅軒不需要 MediaSourceId/PlaySessionId。"""
        return (f"{self.server}/emby/videos/{item_id}/original.mp4"
                f"?DeviceId={self.device_id}&api_key={self.api_key}")


# ── yt-dlp 包裝 ──────────────────────────────────────────────
def find_ytdlp() -> str:
    """找 yt-dlp 執行檔（PATH 優先，找不到就找常見位置）。"""
    for name in ['yt-dlp', 'yt_dlp']:
        path = shutil.which(name)
        if path:
            return path
    # Fallback 常見 local 安裝位置
    for p in [
        os.path.expanduser('~/.local/bin/yt-dlp'),
        '/usr/local/bin/yt-dlp',
        '/opt/homebrew/bin/yt-dlp',
        '/usr/bin/yt-dlp',
    ]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise RuntimeError("找不到 yt-dlp，請先 `pip install yt-dlp` 或 `brew install yt-dlp`")


def download_with_ytdlp(url: str, output_path: Path, title_hint: str = "") -> bool:
    """
    用 yt-dlp 抓 original.mp4。
    - 自動用多 connection + Range request（萌龍雅軒 accept-ranges: bytes）
    - 失敗自動 retry
    """
    ytdlp = find_ytdlp()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ytdlp,
        '-o', str(output_path.with_suffix('.%(ext)s')),
        '--no-mtime',
        '--no-part',
        '--newline',
        '--no-warnings',
        '--concurrent-fragments', '8',  # 多 connection 並發
        '--retries', '10',
        '--fragment-retries', '10',
        url,
    ]

    print(f"\n▶ yt-dlp 啟動：{url[:80]}...")
    print(f"  輸出: {output_path}")
    print(f"  並發: 8 connections")
    print()

    try:
        proc = subprocess.run(cmd)
        return proc.returncode == 0
    except KeyboardInterrupt:
        print("\n✗ 使用者中斷")
        return False


# ── 業務邏輯 ─────────────────────────────────────────────────
def cmd_update(args, client: MlongClient, db: MovieDB):
    """從萌龍同步所有電影到本地 DB。"""
    print("▶ 從萌龍雅軒同步電影清單...")
    client.ping()

    all_movies = []
    for label, parent_id in KNOWN_FOLDERS.items():
        if parent_id is None:
            print(f"  ⚠ {label}: ParentId 未設定，跳過")
            continue
        print(f"  抓 {label} (ParentId={parent_id})...")
        movies = client.list_folder_all(parent_id, label)
        print(f"  ✓ {label}: {len(movies)} 部")
        all_movies.extend(movies)

    # 去重（同一 ID 不會重複，但保險起見）
    seen = set()
    unique = []
    for m in all_movies:
        if m['id'] not in seen:
            seen.add(m['id'])
            unique.append(m)
    db.movies = unique
    db.save()
    print(f"\n✓ DB 更新完成：{len(unique)} 部電影 → {db.path}")


def cmd_search(args, client: MlongClient, db: MovieDB):
    """模糊搜尋並顯示。"""
    if not db.movies:
        print("✗ DB 是空的。先跑 `mlong-dl update`")
        return
    results = db.search(args.query, limit=20)
    if not results:
        print(f"✗ 找不到：'{args.query}'")
        print(f"  DB 裡有 {len(db.movies)} 部電影")
        return
    print(f"\n搜尋「{args.query}」找到 {len(results)} 筆：\n")
    for m in results:
        year = f" ({m.get('year', '')})" if m.get('year') else ''
        runtime = ""
        if m.get('runtime_ticks'):
            mins = m['runtime_ticks'] // 600000000
            runtime = f" [{mins}分]"
        print(f"  [{m['id']:>10}] {m['name']}{year}{runtime}")


def cmd_download(args, client: MlongClient, db: MovieDB):
    """下載：query → search → build URL → yt-dlp。"""
    if not db.movies:
        print("✗ DB 是空的。先跑 `mlong-dl update`")
        return

    results = db.search(args.query, limit=5)
    if not results:
        print(f"✗ 找不到：'{args.query}'")
        return

    if len(results) > 1 and not args.yes:
        print(f"\n「{args.query}」找到 {len(results)} 個候選，預設選第一個：\n")
        for i, m in enumerate(results):
            print(f"  [{i}] [{m['id']:>10}] {m['name']} ({m.get('year', '')})")
        print(f"\n自動選 [0] {results[0]['name']} (用 --index N 選別的，或 --yes 跳過確認)")
        if not args.yes:
            ans = input("確認？[Y/n/index]: ").strip()
            if ans and ans != 'Y' and ans != 'y':
                try:
                    idx = int(ans)
                    if 0 <= idx < len(results):
                        results = [results[idx]]
                except ValueError:
                    print("取消")
                    return

    movie = results[0]
    print(f"\n▶ 下載：{movie['name']} ({movie.get('year', '')}) [{movie['id']}]")

    url = client.build_original_url(movie['id'])
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', movie['name'])[:200]
    year = movie.get('year', '')
    out_dir = Path(args.output) if args.output else DEFAULT_DOWNLOAD_DIR
    out_path = out_dir / f"{safe_name}{' ('+year+')' if year else ''}.mp4"

    if out_path.exists() and not args.redownload:
        print(f"⚠ 檔案已存在：{out_path}")
        ans = input("要重新抓嗎？[y/N]: ").strip()
        if ans.lower() != 'y':
            print("取消")
            return

    ok = download_with_ytdlp(url, out_path, title_hint=movie['name'])
    if ok:
        size = out_path.stat().st_size if out_path.exists() else 0
        print(f"\n✓ 完成：{out_path} ({size/1024/1024:.1f} MB)")
    else:
        print(f"\n✗ 失敗：{movie['name']}")


def cmd_download_id(args, client: MlongClient, db: MovieDB):
    """直接用 Item ID 下載。"""
    movie_id = args.item_id
    movie = db.get(movie_id) if db.movies else None
    if not movie:
        # 沒 DB 或沒這部，仍然可以組 URL 抓（用 ID 當檔名）
        movie = {'id': movie_id, 'name': f'movie_{movie_id}', 'year': ''}
        print(f"⚠ DB 沒這部 ({movie_id})，用 ID 當檔名繼續")

    print(f"\n▶ 下載 Item ID={movie_id}")
    url = client.build_original_url(movie_id)
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', movie['name'])[:200]
    year = movie.get('year', '')
    out_dir = Path(args.output) if args.output else DEFAULT_DOWNLOAD_DIR
    out_path = out_dir / f"{safe_name}{' ('+year+')' if year else ''}.mp4"

    ok = download_with_ytdlp(url, out_path)
    if ok and out_path.exists():
        print(f"\n✓ 完成：{out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)")


def cmd_list(args, client: MlongClient, db: MovieDB):
    """列出所有電影。"""
    if not db.movies:
        print("✗ DB 是空的。先跑 `mlong-dl update`")
        return
    print(f"\n總共 {len(db.movies)} 部電影：\n")
    page_size = 50
    for i in range(0, len(db.movies), page_size):
        page = db.movies[i:i+page_size]
        for m in page:
            year = f" ({m.get('year', '')})" if m.get('year') else ''
            print(f"  [{m['id']:>10}] {m['name']}{year} [{m['folder']}]")
        if i + page_size < len(db.movies):
            ans = input(f"\n--- 已顯示 {i + len(page)}/{len(db.movies)}，繼續？[Y/n]: ").strip()
            if ans and ans.lower() != 'y':
                break


def cmd_batch(args, client: MlongClient, db: MovieDB):
    """批量下載（檔案每行一個名字或 Item ID）。"""
    with open(args.file, encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]

    print(f"▶ 批量下載 {len(lines)} 項：\n")
    for i, line in enumerate(lines, 1):
        print(f"\n=== [{i}/{len(lines)}] {line} ===")
        if line.isdigit():
            args.item_id = line
            cmd_download_id(args, client, db)
        else:
            args.query = line
            args.yes = True  # batch 模式不確認
            cmd_download(args, client, db)


def cmd_gui(args, client: MlongClient, db: MovieDB):
    """開 Tkinter GUI。"""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("✗ tkinter 不可用（macOS python.org installer 沒含 tkinter）")
        print("  macOS: brew install python-tk")
        print("  Linux: apt install python3-tk")
        sys.exit(1)

    if not db.movies:
        if not messagebox.askyesno("DB 是空的", "DB 還沒建。要先跑 update 嗎？"):
            return
        cmd_update(args, client, db)

    class App:
        def __init__(self, root):
            self.root = root
            self.db = db
            self.client = client
            root.title("萌龍下載器 v3")
            root.geometry("900x600")

            # 上：搜尋框
            top = tk.Frame(root)
            top.pack(fill='x', padx=10, pady=5)
            tk.Label(top, text="搜尋:").pack(side='left')
            self.query_var = tk.StringVar()
            self.query_var.trace('w', self.on_search)
            entry = tk.Entry(top, textvariable=self.query_var, width=60)
            entry.pack(side='left', padx=5)
            entry.bind('<Return>', lambda e: self.download_selected())
            tk.Button(top, text="下載選中", command=self.download_selected).pack(side='left')

            # 中：電影 list
            mid = tk.Frame(root)
            mid.pack(fill='both', expand=True, padx=10, pady=5)
            self.listbox = tk.Listbox(mid, font=('TkFixedFont', 11))
            scrollbar = ttk.Scrollbar(mid, orient='vertical', command=self.listbox.yview)
            self.listbox.config(yscrollcommand=scrollbar.set)
            self.listbox.pack(side='left', fill='both', expand=True)
            scrollbar.pack(side='right', fill='y')
            self.listbox.bind('<Double-Button-1>', lambda e: self.download_selected())
            self.all_movies = list(db.movies)
            self.refresh_list(self.all_movies)

            # 下：進度
            bot = tk.Frame(root)
            bot.pack(fill='x', padx=10, pady=5)
            self.progress_label = tk.Label(bot, text="就緒")
            self.progress_label.pack(side='left')
            self.progress_bar = ttk.Progressbar(bot, length=400, mode='determinate')
            self.progress_bar.pack(side='right')

        def on_search(self, *args):
            q = self.query_var.get()
            if not q:
                self.refresh_list(self.all_movies)
            else:
                results = self.db.search(q, limit=200)
                self.refresh_list(results)

        def refresh_list(self, movies):
            self.listbox.delete(0, 'end')
            for m in movies:
                year = f" ({m.get('year', '')})" if m.get('year') else ''
                self.listbox.insert('end', f"[{m['id']:>10}] {m['name']}{year}")

        def download_selected(self):
            sel = self.listbox.curselection()
            if not sel:
                messagebox.showinfo("提示", "請先選一部電影")
                return
            line = self.listbox.get(sel[0])
            m = re.match(r'\[(\d+)\]', line)
            if not m:
                return
            item_id = m.group(1)
            movie = self.db.get(item_id) or {'id': item_id, 'name': f'movie_{item_id}', 'year': ''}
            url = self.client.build_original_url(item_id)
            out_dir = Path(args.output) if hasattr(args, 'output') and args.output else DEFAULT_DOWNLOAD_DIR
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', movie['name'])[:200]
            year = movie.get('year', '')
            out_path = out_dir / f"{safe_name}{' ('+year+')' if year else ''}.mp4"

            self.progress_label.config(text=f"下載中：{movie['name']}...")
            self.progress_bar.config(mode='indeterminate')
            self.progress_bar.start()
            self.root.update()

            ok = download_with_ytdlp(url, out_path, title_hint=movie['name'])
            self.progress_bar.stop()
            self.progress_bar.config(mode='determinate', value=100 if ok else 0)
            if ok:
                self.progress_label.config(text=f"✓ 完成：{out_path.name}")
                messagebox.showinfo("完成", f"下載成功！\n{out_path}")
            else:
                self.progress_label.config(text=f"✗ 失敗")
                messagebox.showerror("失敗", "下載失敗，請看 terminal log")

    root = tk.Tk()
    App(root)
    root.mainloop()


# ── CLI 主程式 ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='萌龍雅軒下載器 v3 (CLI)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--api-key', default=os.environ.get('MLONG_API_KEY'),
                        help='萌龍 api_key（或設 MLONG_API_KEY env）')
    parser.add_argument('--server', default=os.environ.get('MLONG_SERVER', DEFAULT_SERVER),
                        help='萌龍 server URL')
    parser.add_argument('--device-id', default=DEFAULT_DEVICE_ID, help='DeviceId')
    parser.add_argument('-o', '--output', help='下載目錄')

    sub = parser.add_subparsers(dest='cmd')

    # update
    p_update = sub.add_parser('update', help='從萌龍同步電影清單到本地 DB')
    p_update.set_defaults(func=cmd_update)

    # query 搜尋
    p_query = sub.add_parser('search', help='搜尋電影')
    p_query.add_argument('query')
    p_query.set_defaults(func=cmd_search)

    # 模糊 + 下載（最常用）
    p_dl = sub.add_parser('dl', help='搜尋並下載電影', aliases=['download'])
    p_dl.add_argument('query')
    p_dl.add_argument('--index', type=int, default=0, help='當搜尋有多個結果時選第幾個')
    p_dl.add_argument('-y', '--yes', action='store_true', help='跳過確認')
    p_dl.add_argument('--redownload', action='store_true', help='重新抓（即使檔案存在）')
    p_dl.set_defaults(func=cmd_download)

    # 直接 ID 下載
    p_id = sub.add_parser('id', help='用 Item ID 下載')
    p_id.add_argument('item_id')
    p_id.set_defaults(func=cmd_download_id)

    # list
    p_list = sub.add_parser('list', help='列出所有電影')
    p_list.set_defaults(func=cmd_list)

    # batch
    p_batch = sub.add_parser('batch', help='批量下載（每行一個名字或 ID）')
    p_batch.add_argument('file', help='清單檔')
    p_batch.set_defaults(func=cmd_batch)

    # gui
    p_gui = sub.add_parser('gui', help='開 GUI')
    p_gui.set_defaults(func=cmd_gui)

    # 也支援無 subcommand：直接把第一個 positional 當作 query 走 download
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # api_key 檢查（所有 cmd 都需要）
    if not args.api_key:
        print("✗ 缺 api_key。兩種設定方式：")
        print("  1. 環境變數: export MLONG_API_KEY='190e8...'")
        print("  2. 命令列:   --api-key 190e8...")
        sys.exit(1)

    # 初始化
    client = MlongClient(args.server, args.api_key, args.device_id)
    db = MovieDB()

    # 沒有 subcommand 但有 positional query → 走 download
    if args.cmd is None:
        if hasattr(args, 'query'):
            cmd_download(args, client, db)
        else:
            parser.print_help()
        return

    args.func(args, client, db)


if __name__ == '__main__':
    main()