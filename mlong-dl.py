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


# ── Config 管理 ─────────────────────────────────────────────
class Config:
    """持久化設定（GUI 用）。"""

    def __init__(self, path: Path = None):
        self.path = path or Path(__file__).parent / "config.json"
        self.data = {
            'download_dir': str(DEFAULT_DOWNLOAD_DIR),
            'window_size': (900, 600),
            'concurrent_downloads': 1,
            'last_query': '',
        }
        self.load()

    def load(self):
        if self.path.exists():
            try:
                with open(self.path, encoding='utf-8') as f:
                    saved = json.load(f)
                self.data.update(saved)
            except Exception:
                pass

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ config 儲存失敗：{e}")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


# ── 下載 worker（thread） ──────────────────────────────────────
class DownloadWorker:
    """背景 thread，跑單部下載，回呼進度給 GUI。"""

    def __init__(self, movie: dict, url: str, output_dir: Path,
                 on_progress=None, on_done=None, on_error=None):
        self.movie = movie
        self.url = url
        self.output_dir = output_dir
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_done = on_done or (lambda *a, **k: None)
        self.on_error = on_error or (lambda *a, **k: None)
        self.process = None
        self.thread = None
        self.cancelled = False
        self.output_path = None

    def start(self):
        import threading
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled = True
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _run(self):
        try:
            ytdlp = find_ytdlp()
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', self.movie['name'])[:200]
            year = self.movie.get('year', '')
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.output_path = self.output_dir / f"{safe_name}{' ('+year+')' if year else ''}.mp4"

            cmd = [
                ytdlp,
                '-o', str(self.output_path.with_suffix('.%(ext)s')),
                '--no-mtime',
                '--no-part',
                '--newline',
                '--no-warnings',
                '--concurrent-fragments', '8',
                '--retries', '10',
                '--fragment-retries', '10',
                self.url,
            ]

            import subprocess
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )

            last_pct = 0
            for line in self.process.stdout:
                if self.cancelled:
                    break
                # yt-dlp newline 格式：[download]  45.2% of  2.45GiB at 4.2MiB/s ETA 02:30
                line = line.strip()
                m = re.search(r'\[download\]\s+(\d+\.?\d*)%\s+of\s+~?(\S+)\s+at\s+(\S+)\s+ETA\s+(\S+)', line)
                if m:
                    pct = float(m.group(1))
                    if pct != last_pct:
                        last_pct = pct
                        self.on_progress(self, pct, m.group(2), m.group(3), m.group(4))
                else:
                    # 其他訊息也回報（讓 GUI 顯示 log）
                    self.on_progress(self, last_pct, None, None, None, log_line=line)

            self.process.wait()
            rc = self.process.returncode
            if self.cancelled or rc != 0:
                self.on_error(self, f"下載失敗（rc={rc}）")
            else:
                self.on_done(self, self.output_path)
        except Exception as e:
            self.on_error(self, str(e))


# ── GUI 主程式 ────────────────────────────────────────────────
def cmd_gui(args, client: MlongClient, db: MovieDB):
    """開 Tkinter GUI。"""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog
    except ImportError:
        print("✗ tkinter 不可用")
        print("  macOS: brew install python-tk")
        print("  Linux: sudo apt install python3-tk")
        print("  Windows: 重裝 Python 時勾選 tcl/tk")
        sys.exit(1)

    config = Config()
    download_dir = Path(config.get('download_dir'))

    class App:
        def __init__(self, root):
            self.root = root
            self.db = db
            self.client = client
            self.config = config
            self.workers = []  # active DownloadWorker
            self.queue_items = []  # 佇列中等待的 movie dicts

            root.title("萌龍下載器 v3")
            ws = config.get('window_size', (900, 600))
            root.geometry(f"{ws[0]}x{ws[1]}")

            # 視窗關閉時存設定
            root.protocol("WM_DELETE_WINDOW", self.on_close)

            # Style
            style = ttk.Style()
            try:
                style.theme_use('clam')
            except Exception:
                pass

            # ============ 選單列 ============
            menubar = tk.Menu(root)
            file_m = tk.Menu(menubar, tearoff=0)
            file_m.add_command(label="更新電影清單", command=self.update_db)
            file_m.add_separator()
            file_m.add_command(label="離開", command=self.on_close)
            menubar.add_cascade(label="檔案", menu=file_m)

            help_m = tk.Menu(menubar, tearoff=0)
            help_m.add_command(label="關於", command=self.show_about)
            menubar.add_cascade(label="說明", menu=help_m)
            root.config(menu=menubar)

            # ============ 主體 Notebook ============
            self.notebook = ttk.Notebook(root)
            self.notebook.pack(fill='both', expand=True, padx=10, pady=5)

            self._build_search_tab()
            self._build_browse_tab()
            self._build_queue_tab()
            self._build_settings_tab()

            # ============ 底部 status bar ============
            status_frame = tk.Frame(root, relief='sunken', bd=1)
            status_frame.pack(fill='x', side='bottom')
            self.status_label = tk.Label(status_frame, text=f"就緒 · {len(db.movies)} 部電影", anchor='w')
            self.status_label.pack(fill='x', padx=5, pady=2)

        # ── Tab 1: 搜尋 ─────────────────────
        def _build_search_tab(self):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text="🔍 搜尋")

            # 搜尋框
            top = ttk.Frame(tab)
            top.pack(fill='x', padx=5, pady=5)
            ttk.Label(top, text="搜尋:").pack(side='left')
            self.query_var = tk.StringVar(value=self.config.get('last_query', ''))
            self.query_var.trace('w', self.on_search)
            entry = ttk.Entry(top, textvariable=self.query_var, width=60)
            entry.pack(side='left', padx=5)
            entry.bind('<Return>', lambda e: self.start_download_selected())
            ttk.Button(top, text="▶ 立即下載", command=self.start_download_selected).pack(side='left', padx=2)
            ttk.Button(top, text="+ 加入佇列", command=self.add_to_queue).pack(side='left', padx=2)

            # Listbox
            mid = ttk.Frame(tab)
            mid.pack(fill='both', expand=True, padx=5, pady=5)
            self.search_listbox = tk.Listbox(mid, font=('TkFixedFont', 11), selectmode='single')
            sb = ttk.Scrollbar(mid, orient='vertical', command=self.search_listbox.yview)
            self.search_listbox.config(yscrollcommand=sb.set)
            self.search_listbox.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.search_listbox.bind('<Double-Button-1>', lambda e: self.start_download_selected())
            self.search_listbox.bind('<Return>', lambda e: self.start_download_selected())

            # 預設顯示全部
            self.search_results = list(db.movies)
            self._refresh_search_list()

        def _refresh_search_list(self):
            self.search_listbox.delete(0, 'end')
            for m in self.search_results:
                year = f" ({m['year']})" if m.get('year') else ''
                runtime = f" [{m['runtime_ticks']//600000000}分]" if m.get('runtime_ticks') else ''
                self.search_listbox.insert('end', f"[{m['id']:>10}] {m['name']}{year}{runtime}")

        def on_search(self, *args):
            q = self.query_var.get().strip()
            self.config.set('last_query', q)
            if not q:
                self.search_results = list(self.db.movies)
            else:
                self.search_results = self.db.search(q, limit=200)
            self._refresh_search_list()
            self.status_label.config(text=f"搜尋 '{q}' → {len(self.search_results)} 筆")

        def _get_selected_movie(self):
            sel = self.search_listbox.curselection()
            if not sel:
                messagebox.showinfo("提示", "請先選一部電影")
                return None
            line = self.search_listbox.get(sel[0])
            m = re.match(r'\[(\d+)\]', line)
            if not m:
                return None
            item_id = m.group(1)
            movie = self.db.get(item_id) or {'id': item_id, 'name': f'movie_{item_id}', 'year': ''}
            return movie

        def start_download_selected(self):
            movie = self._get_selected_movie()
            if not movie:
                return
            self._start_download(movie)

        def add_to_queue(self):
            movie = self._get_selected_movie()
            if not movie:
                return
            self.queue_items.append(movie)
            self._refresh_queue_list()
            self.status_label.config(text=f"已加入佇列：{movie['name']}")

        # ── Tab 2: 瀏覽全部 ────────────────
        def _build_browse_tab(self):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text="📚 瀏覽全部")

            top = ttk.Frame(tab)
            top.pack(fill='x', padx=5, pady=5)
            ttk.Label(top, text="類別:").pack(side='left')
            self.browse_folder = tk.StringVar(value='全部')
            folder_combo = ttk.Combobox(top, textvariable=self.browse_folder, state='readonly', width=15)
            folders = ['全部'] + sorted({m['folder'] for m in db.movies if m.get('folder')})
            folder_combo['values'] = folders
            folder_combo.pack(side='left', padx=5)
            folder_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_browse_list())

            ttk.Label(top, text="排序:").pack(side='left', padx=(20, 0))
            self.browse_sort = tk.StringVar(value='名稱')
            sort_combo = ttk.Combobox(top, textvariable=self.browse_sort, state='readonly', width=15)
            sort_combo['values'] = ['名稱', '年份（新→舊）', '年份（舊→新）', '時長（長→短）']
            sort_combo.pack(side='left', padx=5)
            sort_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_browse_list())

            mid = ttk.Frame(tab)
            mid.pack(fill='both', expand=True, padx=5, pady=5)
            self.browse_listbox = tk.Listbox(mid, font=('TkFixedFont', 10), selectmode='extended')
            sb = ttk.Scrollbar(mid, orient='vertical', command=self.browse_listbox.yview)
            self.browse_listbox.config(yscrollcommand=sb.set)
            self.browse_listbox.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.browse_listbox.bind('<Double-Button-1>', lambda e: self.browse_double_click())

            bottom = ttk.Frame(tab)
            bottom.pack(fill='x', padx=5, pady=5)
            ttk.Button(bottom, text="▶ 下載選中", command=self.browse_download_selected).pack(side='left', padx=2)
            ttk.Button(bottom, text="+ 全部加入佇列", command=self.browse_add_all_to_queue).pack(side='left', padx=2)
            self.browse_count_label = ttk.Label(bottom, text="")
            self.browse_count_label.pack(side='right', padx=5)

            self._refresh_browse_list()

        def _refresh_browse_list(self):
            movies = list(self.db.movies)
            folder = self.browse_folder.get()
            if folder != '全部':
                movies = [m for m in movies if m.get('folder') == folder]

            sort = self.browse_sort.get()
            if sort == '名稱':
                movies.sort(key=lambda m: m['name'])
            elif sort == '年份（新→舊）':
                movies.sort(key=lambda m: -int(m.get('year') or 0))
            elif sort == '年份（舊→新）':
                movies.sort(key=lambda m: int(m.get('year') or 0))
            elif sort == '時長（長→短）':
                movies.sort(key=lambda m: -m.get('runtime_ticks', 0))

            self.browse_listbox.delete(0, 'end')
            for m in movies:
                year = f" ({m['year']})" if m.get('year') else ''
                runtime = f" [{m['runtime_ticks']//600000000}分]" if m.get('runtime_ticks') else ''
                folder_tag = f" [{m['folder']}]" if m.get('folder') else ''
                self.browse_listbox.insert('end', f"[{m['id']:>10}] {m['name']}{year}{runtime}{folder_tag}")

            self.browse_count_label.config(text=f"顯示 {len(movies)} / {len(self.db.movies)} 部")
            self.browse_movies = movies

        def _get_browse_selected(self):
            sels = self.browse_listbox.curselection()
            if not sels:
                return []
            return [self.browse_movies[i] for i in sels]

        def browse_double_click(self):
            sel = self._get_browse_selected()
            if sel:
                self._start_download(sel[0])

        def browse_download_selected(self):
            sel = self._get_browse_selected()
            if not sel:
                messagebox.showinfo("提示", "請先選電影")
                return
            for m in sel:
                self._start_download(m)

        def browse_add_all_to_queue(self):
            self.queue_items.extend(self.browse_movies)
            self._refresh_queue_list()
            self.status_label.config(text=f"已加入 {len(self.browse_movies)} 部到佇列")

        # ── Tab 3: 下載佇列 ────────────────
        def _build_queue_tab(self):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text="⏬ 佇列")

            # 上：正在下載
            ttk.Label(tab, text="正在下載:").pack(anchor='w', padx=5, pady=(5, 0))
            self.active_frame = ttk.Frame(tab)
            self.active_frame.pack(fill='x', padx=5)
            self.active_labels = {}  # worker_id -> dict of widgets

            # 中：佇列中
            ttk.Label(tab, text="等待中:").pack(anchor='w', padx=5, pady=(10, 0))
            mid = ttk.Frame(tab)
            mid.pack(fill='both', expand=True, padx=5, pady=5)
            self.queue_listbox = tk.Listbox(mid, font=('TkFixedFont', 11))
            sb = ttk.Scrollbar(mid, orient='vertical', command=self.queue_listbox.yview)
            self.queue_listbox.config(yscrollcommand=sb.set)
            self.queue_listbox.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')

            # 下：按鈕
            bottom = ttk.Frame(tab)
            bottom.pack(fill='x', padx=5, pady=5)
            ttk.Button(bottom, text="▶ 開始佇列", command=self.process_queue).pack(side='left', padx=2)
            ttk.Button(bottom, text="清空已完成", command=self.clear_finished).pack(side='left', padx=2)
            ttk.Button(bottom, text="清空佇列", command=self.clear_queue).pack(side='left', padx=2)

            self._refresh_queue_list()

        def _refresh_queue_list(self):
            self.queue_listbox.delete(0, 'end')
            for i, m in enumerate(self.queue_items, 1):
                year = f" ({m['year']})" if m.get('year') else ''
                self.queue_listbox.insert('end', f"[{i}] [{m['id']}] {m['name']}{year}")

        def process_queue(self):
            """啟動佇列裡的下一批下載。"""
            if not self.queue_items:
                messagebox.showinfo("提示", "佇列是空的")
                return
            max_concurrent = self.config.get('concurrent_downloads', 1)
            active = len(self.workers)
            while self.queue_items and active < max_concurrent:
                movie = self.queue_items.pop(0)
                self._refresh_queue_list()
                self._start_download(movie)
                active += 1

        def clear_queue(self):
            self.queue_items.clear()
            self._refresh_queue_list()

        def clear_finished(self):
            self.workers = [w for w in self.workers if w.process and w.process.poll() is None]

        # ── Tab 4: 設定 ────────────────────
        def _build_settings_tab(self):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text="⚙ 設定")

            row = 0

            # 下載目錄
            ttk.Label(tab, text="下載目錄:").grid(row=row, column=0, sticky='e', padx=5, pady=5)
            self.dir_var = tk.StringVar(value=str(download_dir))
            ttk.Entry(tab, textvariable=self.dir_var, width=50).grid(row=row, column=1, padx=5)
            ttk.Button(tab, text="瀏覽...", command=self.browse_dir).grid(row=row, column=2, padx=5)
            row += 1

            # 並發數
            ttk.Label(tab, text="同時下載數:").grid(row=row, column=0, sticky='e', padx=5, pady=5)
            self.concurrent_var = tk.IntVar(value=self.config.get('concurrent_downloads', 1))
            ttk.Spinbox(tab, from_=1, to=8, textvariable=self.concurrent_var, width=10).grid(row=row, column=1, sticky='w', padx=5)
            row += 1

            # API key
            ttk.Label(tab, text="API Key:").grid(row=row, column=0, sticky='e', padx=5, pady=5)
            self.api_key_var = tk.StringVar(value=args.api_key or '')
            ttk.Entry(tab, textvariable=self.api_key_var, width=50, show='*').grid(row=row, column=1, padx=5)
            row += 1

            # Server
            ttk.Label(tab, text="Server:").grid(row=row, column=0, sticky='e', padx=5, pady=5)
            self.server_var = tk.StringVar(value=args.server or DEFAULT_SERVER)
            ttk.Entry(tab, textvariable=self.server_var, width=50).grid(row=row, column=1, padx=5)
            row += 1

            # Device ID
            ttk.Label(tab, text="Device ID:").grid(row=row, column=0, sticky='e', padx=5, pady=5)
            self.device_var = tk.StringVar(value=args.device_id or DEFAULT_DEVICE_ID)
            ttk.Entry(tab, textvariable=self.device_var, width=50).grid(row=row, column=1, padx=5)
            row += 1

            # 儲存按鈕
            ttk.Button(tab, text="💾 儲存", command=self.save_settings).grid(row=row, column=1, sticky='w', padx=5, pady=10)

            # 狀態
            self.settings_status = ttk.Label(tab, text="")
            self.settings_status.grid(row=row+1, column=1, sticky='w', padx=5)

            # DB 統計
            ttk.Separator(tab, orient='horizontal').grid(row=row+2, column=0, columnspan=3, sticky='we', pady=20)
            ttk.Label(tab, text=f"DB 統計:").grid(row=row+3, column=0, sticky='e', padx=5)
            stats = f"{len(db.movies)} 部電影，{len({m.get('folder') for m in db.movies})} 個類別"
            ttk.Label(tab, text=stats).grid(row=row+3, column=1, sticky='w', padx=5)

        def browse_dir(self):
            d = filedialog.askdirectory(initialdir=self.dir_var.get())
            if d:
                self.dir_var.set(d)

        def save_settings(self):
            self.config.set('download_dir', self.dir_var.get())
            self.config.set('concurrent_downloads', self.concurrent_var.get())
            self.config.save()
            self.settings_status.config(text="✓ 已儲存", foreground='green')
            self.root.after(3000, lambda: self.settings_status.config(text=""))

        # ── 共通：下載 ─────────────────────
        def _start_download(self, movie):
            item_id = movie['id']
            url = self.client.build_original_url(item_id)
            out_dir = Path(self.dir_var.get())
            out_dir.mkdir(parents=True, exist_ok=True)

            # 建立 worker
            def on_progress(worker, pct, total, speed, eta, log_line=None):
                if log_line and not total:
                    return  # 跳過非進度訊息（避免洗版）
                wid = id(worker)
                if wid in self.active_labels:
                    widgets = self.active_labels[wid]
                    widgets['bar']['value'] = pct
                    widgets['text'].config(
                        text=f"{worker.movie['name']} · {pct:.1f}% · {speed or ''} · {eta or ''}")

            def on_done(worker, path):
                wid = id(worker)
                if wid in self.active_labels:
                    self.active_labels[wid]['text'].config(
                        text=f"✓ {worker.movie['name']} → {path.name}", foreground='green')
                self.status_label.config(text=f"✓ 完成：{worker.movie['name']}")
                # 自動啟動佇列下一個
                self.process_queue()

            def on_error(worker, err):
                wid = id(worker)
                if wid in self.active_labels:
                    self.active_labels[wid]['text'].config(
                        text=f"✗ {worker.movie['name']} · {err}", foreground='red')
                self.status_label.config(text=f"✗ 失敗：{worker.movie['name']}")
                self.process_queue()

            worker = DownloadWorker(movie, url, out_dir, on_progress, on_done, on_error)
            self.workers.append(worker)

            # 加到 active_frame
            row_frame = ttk.Frame(self.active_frame)
            row_frame.pack(fill='x', pady=2)
            bar = ttk.Progressbar(row_frame, length=300, mode='determinate', maximum=100)
            bar.pack(side='left', padx=(0, 10))
            text = ttk.Label(row_frame, text=f"⏳ {movie['name']} 開始中...", width=60)
            text.pack(side='left')
            cancel_btn = ttk.Button(row_frame, text="✗", width=3,
                                    command=lambda: self.cancel_download(worker))
            cancel_btn.pack(side='left', padx=5)

            self.active_labels[id(worker)] = {'frame': row_frame, 'bar': bar, 'text': text}

            worker.start()
            self.status_label.config(text=f"⏳ 開始下載：{movie['name']}")

        def cancel_download(self, worker):
            worker.cancel()
            wid = id(worker)
            if wid in self.active_labels:
                self.active_labels[wid]['text'].config(
                    text=f"✗ {worker.movie['name']} · 已取消", foreground='orange')

        def on_close(self):
            # 存設定
            try:
                ws = (self.root.winfo_width(), self.root.winfo_height())
                self.config.set('window_size', ws)
                self.config.save()
            except Exception:
                pass
            self.root.destroy()

        # ── 工具命令 ─────────────────────────
        def update_db(self):
            if messagebox.askyesno("更新", "從萌龍重新同步所有電影？這會跑 1-2 分鐘。"):
                try:
                    cmd_update(args, self.client, self.db)
                    messagebox.showinfo("完成", f"已更新 {len(self.db.movies)} 部電影")
                    self.status_label.config(text=f"已更新 DB · {len(self.db.movies)} 部電影")
                    self._refresh_search_list()
                    self._refresh_browse_list()
                except Exception as e:
                    messagebox.showerror("失敗", str(e))

        def show_about(self):
            messagebox.showinfo("關於",
                "萌龍下載器 v3\n\n"
                "萌龍雅軒 (mlong.cutedragon.vip) 影片下載工具\n\n"
                "GitHub: https://github.com/kilroy-utb/mlong-dl")

    root = tk.Tk()
    app = App(root)
    # 預設填入搜尋框 focus
    root.after(100, lambda: root.focus_force())
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