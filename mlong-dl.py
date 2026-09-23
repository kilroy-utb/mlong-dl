#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mlong-dl v2.0 — 萌龍雅軒下載器 (簡潔重構版)

設計原則：
  - DB = 唯一真理來源（本地端 12 萬筆電影資料）
  - yt-dlp = 唯一下載執行者
  - Python 自己不再 call 萌龍（避免 GUI 卡住）
  - 萌龍 API 只在「更新 DB」按鈕使用

GUI 設計：
  - 搜尋 bar → 有結果才顯示 result block (lazy)
  - 雙擊下載 → yt-dlp 跑背景 thread
  - 更新 DB → 兩種選擇（全部 / 單 series）
  - 下載目錄 = 程式同目錄/downloads (預設)
"""
import os
import re
import sys
import json
import time
import shutil
import threading
import subprocess
from pathlib import Path
from typing import Optional

# ── 嘗試 import requests ────────────────────────────────
try:
    import requests
except ImportError:
    print('請先安裝 requests: pip install requests')
    sys.exit(1)

# ── 常數 ────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / 'db.json'
DEFAULT_DOWNLOAD_DIR = SCRIPT_DIR / 'downloads'  # 預設值（GUI 可改、CLI 可 --output 覆蓋）
DEFAULT_SERVER = 'https://mlong.cutedragon.vip:8888'
DEFAULT_DEVICE_ID = '4068e636-c8e6-4a84-80aa-24dd4a40aefa'

# GUI 用的全域 mutable 下載目錄（GUI 啟動時 init，cmd_gui 也會讀 env 變數）
_current_download_dir: Optional[Path] = None


def get_download_dir() -> Path:
    """取得目前下載目錄（GUI 可改，CLI 用 env 或 --output）。"""
    if _current_download_dir is not None:
        return _current_download_dir
    return DEFAULT_DOWNLOAD_DIR


def set_download_dir(path) -> Path:
    """設定下載目錄。回傳 normalized Path。"""
    global _current_download_dir
    _current_download_dir = Path(path).expanduser().resolve()
    _current_download_dir.mkdir(parents=True, exist_ok=True)
    return _current_download_dir

# 17 個萌龍 folder
KNOWN_FOLDERS = {
    'chinese':       3,
    'foreign':       515,
    'anime_movie':   3211,
    'horror':        16018,
    'art':           42629,
    'concert':       65159,
    'chinese_tv':    3359,
    'jp_kr_tv':      5345,
    'western_tv':    5510,
    'jp_anime':      5604,
    'western_anime': 32397,
    'chinese_anime': 46752,
    'kids':          48072,
    'variety':       65332,
    'documentary':   65336,
    'collections':   89934,
    'playlists':     105645,
}
FOLDER_TYPES = {
    'chinese':       ['Movie'],
    'foreign':       ['Movie'],
    'anime_movie':   ['Movie'],
    'horror':        ['Movie'],
    'art':           ['Movie'],
    'concert':       ['Series', 'Season', 'Episode'],
    'chinese_tv':    ['Series', 'Season', 'Episode'],
    'jp_kr_tv':      ['Series', 'Season', 'Episode'],
    'western_tv':    ['Series', 'Season', 'Episode'],
    'jp_anime':      ['Series', 'Season', 'Episode'],
    'western_anime': ['Series', 'Season', 'Episode'],
    'chinese_anime': ['Series', 'Season', 'Episode'],
    'kids':          ['Series', 'Season', 'Episode'],
    'variety':       ['Series', 'Season', 'Episode'],
    'documentary':   ['Series', 'Season', 'Episode'],
    'collections':   ['Episode'],
    'playlists':     ['Episode'],
}

# ── 繁簡對照（用 zhconv 或 fallback 手動字典）───────────
try:
    from zhconv import convert as _zhconv_convert
    def _t2s(s): return _zhconv_convert(s, 'zh-cn')
    def _s2t(s): return _zhconv_convert(s, 'zh-tw')
except ImportError:
    _MANUAL = {
        '達':'达','馬':'马','龍':'龙','鳳':'凤','鳥':'鸟','貓':'猫','豬':'猪',
        '獸':'兽','魚':'鱼','蝸':'蜗','鴨':'鸭','車':'车','飛':'飞','長':'长',
        '門':'门','開':'开','關':'关','時':'时','當':'当','個':'个','們':'们','過':'过',
        '這':'这','裡':'里','為':'为','會':'会','來':'来','說':'说','話':'话','語':'语',
        '電':'电','腦':'脑','機':'机','聲':'声','聽':'听','頭':'头','臉':'脸','麵':'面',
        '麥':'麦','黨':'党','國':'国','園':'园','圖':'图','畫':'画','寫':'写','經':'经',
        '給':'给','萬':'万','億':'亿','區':'区','號':'号','單':'单','雙':'双','幾':'几',
        '麼':'么','從':'从','進':'进','遠':'远','運':'运','遊':'游','學':'学','術':'术',
        '節':'节','葉':'叶','夢':'梦','淚':'泪','紅':'红','綠':'绿','黃':'黄','藍':'蓝','銀':'银',
        '鐵':'铁','鋼':'钢','風':'风','雲':'云','霧':'雾','島':'岛','嶼':'屿','飯':'饭',
        '館':'馆','驚':'惊','哀':'哀','樂':'乐','愛':'爱','恨':'恨','慾':'欲','蘭':'兰',
        '籃':'篮','襯':'衬','陳':'陈','孫':'孙','隊':'队','陣':'阵','對':'对','點':'点',
        '線':'线','處':'处','數':'数','記':'记','試':'试','證':'证','視':'视','讓':'让',
        '請':'请','誰':'谁','調':'调','變':'变','觀':'观','覺':'觉','東':'东','西':'西',
        '南':'南','北':'北','中':'中','間':'间','邊':'边','圍':'围',
    }
    _MANUAL_S2T = {v: k for k, v in _MANUAL.items()}
    def _t2s(s): return ''.join(_MANUAL.get(c, c) for c in s)
    def _s2t(s): return ''.join(_MANUAL_S2T.get(c, c) for c in s)

# ═══════════════════════════════════════════════════════════
# MovieDB — 純本地 (不 call 萌龍)
# ═══════════════════════════════════════════════════════════
class MovieDB:
    TYPE_EMOJI = {
        'Movie':   '🎬',
        'Series':  '📺',
        'Season':  '📀',
        'Episode': '🎞️',
    }

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path) if not isinstance(path, Path) else path
        self.movies = []
        self._by_id = {}
        self._index = {}      # 2-char pair -> set of ids (繁)
        self._index_simp = {} # 2-char pair -> set of ids (簡)
        self._display = {}    # id -> pre-formatted display string
        self._name_alt = {}   # id -> 簡轉繁 name (lowercase)
        self._name_simp = {}  # id -> 繁轉簡 name (lowercase)
        self.load()

    @staticmethod
    def _safe_int(v, default=0):
        try: return int(v)
        except: return default

    def load(self):
        if not self.path.exists():
            return
        with open(self.path, encoding='utf-8') as f:
            self.movies = json.load(f)
        self._build_indexes()
        print(f'  ✓ DB loaded: {len(self.movies):,} 筆 ({self.path})')

    def _build_indexes(self):
        t0 = time.time()
        self._by_id = {}
        self._index = {}
        self._index_simp = {}

        for m in self.movies:
            mid = m['id']
            name = m.get('name', '')

            # display string (預算)
            self._display[mid] = self._format_display(m)

            # name variants
            name_lower = name.lower()
            self._name_alt[mid] = _s2t(name).lower()
            self._name_simp[mid] = _t2s(name).lower()

            self._by_id[mid] = m

            # inverted index 2-char pairs
            for n in (name_lower, self._name_alt[mid]):
                for i in range(len(n) - 1):
                    self._index.setdefault(n[i:i+2], set()).add(mid)
            for n in (self._name_simp[mid],):
                for i in range(len(n) - 1):
                    self._index_simp.setdefault(n[i:i+2], set()).add(mid)

        elapsed = time.time() - t0
        print(f'  ✓ Index built: {len(self._index):,} trad pairs, '
              f'{len(self._index_simp):,} simp pairs, {elapsed:.1f}s')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 清掉 runtime 欄位
        clean = [{k: v for k, v in m.items() if not k.startswith('_')}
                 for m in self.movies]
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)

    def _format_display(self, m: dict) -> str:
        t = m.get('type', 'Movie')
        year = f" ({m['year']})" if m.get('year') else ''
        ticks = m.get('runtime_ticks', 0)
        runtime = f" [{ticks//600000000}分]" if ticks else ''
        emoji = self.TYPE_EMOJI.get(t, '❓')

        if t == 'Movie':
            return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"
        elif t == 'Series':
            return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"
        elif t == 'Season':
            sn = self._safe_int(m.get('season'))
            return f"[{m['id']:>10}] {emoji} {m.get('series_name','?')} - S{sn:02d}"
        elif t == 'Episode':
            sn = self._safe_int(m.get('season'))
            ep = self._safe_int(m.get('episode'))
            series = m.get('series_name') or ''
            return f"[{m['id']:>10}] {emoji} {series} S{sn:02d}E{ep:02d} 「{m['name']}」{runtime}"
        return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"

    def get(self, item_id: str) -> Optional[dict]:
        return self._by_id.get(item_id)

    def get_display(self, item_id: str) -> str:
        """快取 display string。"""
        return self._display.get(item_id, f"[{item_id:>10}] (unknown)")

    def search(self, query: str, limit: int = 100) -> list:
        """用 inverted index 搜尋。"""
        q = query.strip().lower()
        if not q:
            return []
        q_simp = _t2s(q)
        queries = [q]
        if q_simp != q:
            queries.append(q_simp)

        results = set()
        for sq in queries:
            pairs = [sq[i:i+2] for i in range(len(sq)-1)] if len(sq) > 1 else [sq]
            for index in (self._index, self._index_simp):
                sets = [index.get(p, set()) for p in pairs]
                sets = [s for s in sets if s]
                if not sets:
                    continue
                sets.sort(key=len)
                candidates = sets[0].copy()
                for s in sets[1:]:
                    candidates &= s
                results |= candidates

        if not results:
            # substring fallback
            for mid, m in self._by_id.items():
                name = m.get('_name_lower') if False else m.get('name','').lower()
                if q in name or q_simp in _t2s(name):
                    results.add(mid)

        movies = [self._by_id[mid] for mid in results if mid in self._by_id]

        def get_score(m):
            n = m.get('name','').lower()
            na = _s2t(m.get('name','')).lower()
            if n in queries or na in queries:
                return (0, len(n))
            for qq in queries:
                if n.startswith(qq) or na.startswith(qq):
                    return (1, len(n))
            return (2, len(n))

        movies.sort(key=get_score)
        return movies[:limit]

    def get_episodes_for_series(self, series_id: str) -> list:
        """本地查 series 的所有 episodes（不 call 萌龍）。"""
        series = self._by_id.get(series_id)
        if not series:
            return []
        sname = series['name']
        eps = [m for m in self.movies
               if m.get('type') == 'Episode' and m.get('series_name') == sname]
        eps.sort(key=lambda e: (self._safe_int(e.get('season')),
                                self._safe_int(e.get('episode'))))
        return eps

    def get_seasons_for_series(self, series_id: str) -> list:
        """本地查 series 的所有 seasons。"""
        series = self._by_id.get(series_id)
        if not series:
            return []
        sname = series['name']
        return [m for m in self.movies
                if m.get('type') == 'Season' and m.get('series_name') == sname]

    def get_series_tree_ids(self, series_id: str) -> set:
        """v2.0：給定 series_id，回傳所有相關的 ids（series + seasons + episodes）。
        用於 update 單 series 時移除舊 entry。
        """
        series = self._by_id.get(series_id)
        if not series:
            return {series_id}
        ids = {series_id}
        sname = series['name']
        for m in self.movies:
            if m['id'] == series_id:
                ids.add(m['id'])
            elif m.get('series_name') == sname:
                ids.add(m['id'])
        return ids

    def find_series_for_episode(self, episode_id: str):
        """v2.4：給定 episode id，找出 parent Series item（用 series_name 反查）。
        回傳 None 表示找不到（可能 DB 沒該 episode、或 series 已被砍）。
        """
        ep = self._by_id.get(episode_id)
        if not ep:
            return None
        if ep.get('type') == 'Series':
            return ep
        sname = ep.get('series_name', '')
        if not sname:
            return None
        for m in self.movies:
            if m.get('type') == 'Series' and m.get('name') == sname:
                return m
        return None

    def find_parent_id_for_movie(self, movie_id: str):
        """v2.4：給定 movie id，找出它在萌龍的 folder ParentId。
        用 item.folder 反查 KNOWN_FOLDERS 對應的 parent_id。
        """
        m = self._by_id.get(movie_id)
        if not m:
            return None, None
        folder = m.get('folder', '')
        parent_id = KNOWN_FOLDERS.get(folder)
        return parent_id, folder


# ═══════════════════════════════════════════════════════════
# MlongClient — 只用於「更新 DB」指令
# ═══════════════════════════════════════════════════════════
class MlongClient:
    def __init__(self, server: str, api_key: str, device_id: str = DEFAULT_DEVICE_ID):
        self.server = server.rstrip('/')
        self.api_key = api_key
        self.device_id = device_id
        self.s = requests.Session()
        self.s.headers.update({
            'User-Agent': 'Mozilla/5.0 ml-dl/2.0',
            'X-Emby-Authorization': f'MediaBrowser Client="ml-dl", Device="CLI", DeviceId="{device_id}", Version="2.0"',
        })

    def _url(self, p): return f'{self.server}{p}'

    def build_original_url(self, item_id: str) -> str:
        """組 original.mp4 URL（不需要任何 API call）。"""
        return (f'{self.server}/emby/videos/{item_id}/original.mp4'
                f'?DeviceId={self.device_id}&api_key={self.api_key}')

    def list_folder(self, parent_id: int, include_types: list, label: str = '') -> list:
        """完整抓 folder 內所有 items。"""
        movies = []
        start, page = 0, 100
        while True:
            params = {
                'api_key': self.api_key,
                'ParentId': parent_id,
                'Limit': page,
                'StartIndex': start,
                'Recursive': 'true',
                'Fields': 'Name,ProductionYear,RunTimeTicks,Type,'
                          'SeriesName,SeasonNumber,EpisodeNumber,IndexNumber,Overview,ParentId',
            }
            if include_types:
                params['IncludeItemTypes'] = ','.join(include_types)
            r = self.s.get(self._url('/Items'), params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            items = data.get('Items', [])
            if not items:
                break
            total = data.get('TotalRecordCount', 0)
            for it in items:
                t = it.get('Type', '')
                if include_types and t not in include_types:
                    continue
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
                    'type': t,
                    'parent_id': str(it.get('ParentId', '')) if it.get('ParentId') is not None else '',
                    'season': it.get('ParentIndexNumber') if t == 'Episode' else it.get('IndexNumber'),
                    'episode': it.get('IndexNumber') if t == 'Episode' else None,
                    'series_name': it.get('SeriesName', ''),
                    'runtime_ticks': it.get('RunTimeTicks', 0),
                })
            print(f'  [{label}] {start + len(items)}/{total}', end='\r')
            start += len(items)
            if start >= total or len(items) < page:
                break
        return movies

    def get_series_full_tree(self, series_id: str, label: str = '') -> list:
        """抓單 series 樹（用 ?Ids + ParentId Recursive）。"""
        items = []
        # Series 自己
        r = self.s.get(self._url('/Items'), params={
            'api_key': self.api_key,
            'Ids': series_id,
        }, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'GET Items?Ids={series_id} failed: {r.status_code}')
        results = r.json().get('Items', [])
        if not results:
            raise RuntimeError(f'Series {series_id} 不存在或無權限')
        series_item = results[0]
        sname = series_item.get('Name', '')
        # Seasons + Episodes (recursive)
        r = self.s.get(self._url('/Items'), params={
            'api_key': self.api_key,
            'ParentId': series_id,
            'Recursive': 'true',
            'Fields': 'Name,ProductionYear,RunTimeTicks,Type,'
                      'SeriesName,SeasonNumber,EpisodeNumber,IndexNumber,Overview,ParentId',
        }, timeout=30)
        r.raise_for_status()
        children = r.json().get('Items', [])
        all_raw = [series_item] + children
        for it in all_raw:
            t = it.get('Type', '')
            name = it.get('Name', '')
            year = ''
            m = re.search(r'\((\d{4})\)', name)
            if m:
                year = m.group(1)
            items.append({
                'id': str(it.get('Id')),
                'name': name,
                'year': year,
                'folder': label,
                'type': t,
                'parent_id': str(it.get('ParentId', '')) if it.get('ParentId') is not None else '',
                'season': it.get('ParentIndexNumber') if t == 'Episode' else it.get('IndexNumber'),
                'episode': it.get('IndexNumber') if t == 'Episode' else None,
                'series_name': it.get('SeriesName', '') or sname,
                'runtime_ticks': it.get('RunTimeTicks', 0),
            })
        return items


# ═══════════════════════════════════════════════════════════
# 下載（背景 thread）
# ═══════════════════════════════════════════════════════════
def find_yt_dlp():
    """找 yt-dlp。"""
    for n in ['yt-dlp', 'yt_dlp', 'yt-dlp.exe']:
        p = shutil.which(n)
        if p:
            return [p]
    # 用 python -m yt_dlp fallback
    try:
        import yt_dlp  # noqa
        return [sys.executable, '-m', 'yt_dlp']
    except ImportError:
        pass
    # Windows 常見路徑
    candidates = [
        r'C:\Python313\Scripts\yt-dlp.exe',
        r'C:\Python312\Scripts\yt-dlp.exe',
        os.path.expanduser(r'~\AppData\Local\Programs\Python\Python313\Scripts\yt-dlp.exe'),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return [p]
    raise RuntimeError('找不到 yt-dlp，請先 pip install yt-dlp')


def _parse_yt_dlp_progress(line: str):
    """Parse yt-dlp --newline 進度行，回傳 (downloaded_bytes, total_bytes, speed_bps, eta_sec) 或 None。

    範例行：
      [download]  23.4% of   1.23GiB at 5.6MiB/s ETA 02:13
      [download] 100% of   1.23GiB in 00:03:45 at 5.6MiB/s
    """
    if '[download]' not in line:
        return None

    def to_bytes(n, unit):
        n = float(n)
        u = unit.upper().replace('I', '')  # 'GiB' -> 'GB', 'MiB' -> 'MB'
        mult = {'': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4,
                'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4,
                'KIB': 1024, 'MIB': 1024**2, 'GIB': 1024**3, 'TIB': 1024**4}[u]
        return int(n * mult)

    # 1) 切出 [download]  之後的段，避免後面的速度/sizes 干擾
    m = re.search(r'\[download\]\s+(.*)', line)
    seg = m.group(1) if m else line

    # 2) 百分比
    pct_m = re.search(r'(\d+(?:\.\d+)?)%', seg)
    if not pct_m:
        return None

    # 3) "of  X.XxXB (at|in)" 之間的 size
    #    yt-dlp 進度行： "[download]  23.4% of 1.23GiB at 5.6MiB/s ETA 02:13"
    #    yt-dlp 完成行： "[download] 100% of 1.23GiB in 00:03:45 at 5.6MiB/s"
    sizes_m = re.search(r'of\s+([\d.]+)\s*([KMGT]?i?B)\s+(?:at|in)\b', seg)
    if sizes_m:
        total = to_bytes(sizes_m.group(1), sizes_m.group(2))
        # 用百分比算 downloaded（百分比是 ground truth）
        downloaded = round(total * float(pct_m.group(1)) / 100)
    else:
        downloaded = 0
        total = 0

    # 4) speed（at X.XxXB/s）
    speed_m = re.search(r'at\s+([\d.]+)\s*([KMGT]?i?B)/s', line)
    speed = to_bytes(speed_m.group(1), speed_m.group(2)) if speed_m else 0

    # 5) ETA 02:13 或 1:02:13
    eta_m = re.search(r'ETA\s+(\d+):(\d{2})(?::(\d{2}))?', line)
    if eta_m:
        h = int(eta_m.group(3) or 0)
        m_ = int(eta_m.group(1))
        s = int(eta_m.group(2))
        eta = h*3600 + m_*60 + s
    else:
        eta = 0

    return (downloaded, total, speed, eta)


def download_one(item: dict, output_dir: str, api_key: str,
                 status_callback=None, progress_callback=None,
                 tracker=None):
    """用 yt-dlp 下載單個 item。api_key 必須傳入。

    status_callback(text) — 文字狀態更新
    progress_callback(downloaded_bytes, total_bytes, speed_bps, eta_sec) — 進度更新
    tracker — optional DownloadTracker，傳入時會註冊 proc 以支援 cancel()
    """
    # Series 自己不能下載（沒 media），但可以自動展開成 episodes
    t = item.get('type', 'Movie')
    if t == 'Series':
        return ('series_expand', None)

    if not api_key:
        return ('error', 'api_key 沒設定（用 --api-key 傳）')

    item_id = item['id']
    url = build_url(item_id, api_key)

    # 組 output 檔名（v2.1：加 S/E prefix 讓影集好辨識）
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', item['name'])[:200]
    year = item.get('year', '')

    # 依 type 加 prefix
    prefix = ''
    if t == 'Episode':
        series_name = item.get('series_name', '').strip()
        season = _safe_int(item.get('season'), 1)
        episode = _safe_int(item.get('episode'), 1)
        if series_name:
            # 「權力遊戲 - S01E05 「凱特」.mp4」格式
            safe_series = re.sub(r'[\\/:*?"<>|]', '_', series_name)[:80]
            prefix = f'{safe_series} - S{season:02d}E{episode:02d} '
        else:
            prefix = f'S{season:02d}E{episode:02d} '
    elif t == 'Season':
        season = _safe_int(item.get('season'), 1)
        series_name = item.get('series_name', '').strip()
        if series_name:
            safe_series = re.sub(r'[\\/:*?"<>|]', '_', series_name)[:80]
            prefix = f'{safe_series} - '
        prefix += f'S{season:02d} '

    filename = f'{prefix}{safe_name}'
    if year and t == 'Movie':
        filename += f' ({year})'
    out_file = Path(output_dir) / f'{filename}.mp4'

    try:
        ytdlp_cmd = find_yt_dlp()
    except Exception as e:
        return ('error', str(e))

    cmd = ytdlp_cmd + [
        '-o', str(out_file.with_suffix('.%(ext)s')),
        '--no-mtime', '--no-part', '--newline',
        '--concurrent-fragments', '8',
        '--retries', '10',
        '--fragment-retries', '10',
        url,
    ]

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if status_callback:
        status_callback(f'下載中: {item["name"]}...')

    # v2.2 debug：把 yt-dlp 完整輸出寫到 debug_ytdlp.log（方便診斷進度沒更新）
    debug_log = Path(output_dir).parent / 'debug_ytdlp.log'

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        # 註冊給 tracker，cancel() 可 kill
        if tracker is not None:
            tracker.register_proc(item_id, proc)
        last_total = 0  # 用於沒拿到 total 時 fallback
        with open(debug_log, 'a', encoding='utf-8') as df:
            df.write(f'\n=== {item["name"]} ({item_id}) ===\n')
            for line in proc.stdout:
                line = line.rstrip()
                df.write(line + '\n')
                parsed = _parse_yt_dlp_progress(line)
                if parsed and progress_callback:
                    downloaded, total, speed, eta = parsed
                    if total > 0:
                        last_total = total
                    progress_callback(downloaded, last_total, speed, eta)
        proc.wait(timeout=3600)
        if tracker is not None:
            tracker.unregister_proc(item_id)
        # 查 tracker flag → 判斷是 cancel 還是 error
        if tracker is not None and tracker.was_cancelled(item_id):
            return ('cancelled', '使用者停止')
        # 負 returncode 表示被 signal 殺掉（POSIX cancel 的另一條路徑）
        if proc.returncode is not None and proc.returncode < 0:
            return ('cancelled', f'rc={proc.returncode}')
        if proc.returncode == 0 and out_file.exists():
            return ('ok', str(out_file))
        else:
            return ('error', f'rc={proc.returncode}')
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except Exception: pass
        if tracker is not None:
            tracker.unregister_proc(item_id)
        return ('error', 'timeout 1hr')
    except Exception as e:
        if tracker is not None:
            tracker.unregister_proc(item_id)
        return ('error', str(e))


def build_url(item_id: str, api_key: str) -> str:
    return (f'{DEFAULT_SERVER}/emby/videos/{item_id}/original.mp4'
            f'?DeviceId={DEFAULT_DEVICE_ID}&api_key={api_key}')


# ═══════════════════════════════════════════════════════════
# DownloadTracker — thread-safe 下載狀態 (背景 thread 寫 / GUI thread 讀)
# ═══════════════════════════════════════════════════════════
class _DownloadItem:
    """單一下載任務的狀態。"""
    __slots__ = ('id', 'name', 'kind', 'path', 'status', 'downloaded', 'total',
                 'speed', 'eta', 'error')

    def __init__(self, id_: str, name: str, kind: str, path: str = ''):
        self.id = id_
        self.name = name
        self.kind = kind           # Movie / Episode / Season
        self.path = path           # 下載到哪個資料夾（顯示用）
        self.status = 'queued'     # queued / downloading / ok / error / cancelled
        self.downloaded = 0
        self.total = 0
        self.speed = 0
        self.eta = 0
        self.error = ''


class DownloadTracker:
    """所有下載任務的集合。背景 thread 透過 callback 寫入，GUI thread 定期 poll。

    Thread-safety: 一把 lock 保護 _items 跟 _procs。GUI 不該 mutate，只讀。
    """

    def __init__(self):
        import threading as _th
        self._lock = _th.Lock()
        self._items = {}    # id -> _DownloadItem
        self._procs = {}    # id -> subprocess.Popen（背景 thread 註冊 / 移除）
        self._cancelled = set()  # id set，被 cancel() 標記，背景 thread 醒來查這個

    # ── 寫入（背景 thread） ──────────────────────────────
    def add(self, id_: str, name: str, kind: str, path: str = '') -> None:
        with self._lock:
            self._items[id_] = _DownloadItem(id_, name, kind, path)

    def register_proc(self, id_: str, proc) -> None:
        """背景 thread 在 Popen 完後註冊 proc，給 cancel() 用。"""
        with self._lock:
            self._procs[id_] = proc

    def unregister_proc(self, id_: str) -> None:
        """下載結束（成功/失敗/cancel）後移除 proc 參照。"""
        with self._lock:
            self._procs.pop(id_, None)

    def update_progress(self, id_: str, downloaded: int, total: int,
                        speed: int, eta: int) -> None:
        with self._lock:
            it = self._items.get(id_)
            if not it:
                return
            it.status = 'downloading'
            it.downloaded = downloaded
            if total > 0:
                it.total = total
            it.speed = speed
            it.eta = eta

    def mark_done(self, id_: str, ok: bool, error: str = '') -> None:
        with self._lock:
            it = self._items.get(id_)
            if not it:
                return
            it.status = 'ok' if ok else 'error'
            it.error = error
            if ok and it.total > 0:
                it.downloaded = it.total

    def mark_cancelled(self, id_: str) -> None:
        with self._lock:
            it = self._items.get(id_)
            if not it:
                return
            it.status = 'cancelled'
            it.error = '使用者停止'

    def was_cancelled(self, id_: str) -> bool:
        """背景 thread 查詢：是否被使用者 cancel。"""
        with self._lock:
            return id_ in self._cancelled

    # ── 讀取（GUI thread） ────────────────────────────────
    def snapshot(self) -> list:
        """回傳所有 items 的快照（淺拷貝 dict list）。"""
        with self._lock:
            return [
                {
                    'id': it.id, 'name': it.name, 'kind': it.kind, 'path': it.path,
                    'status': it.status, 'downloaded': it.downloaded,
                    'total': it.total, 'speed': it.speed, 'eta': it.eta,
                    'error': it.error,
                }
                for it in self._items.values()
            ]

    def clear_finished(self) -> None:
        """清掉 ok / error / cancelled 的，留下進行中的。"""
        with self._lock:
            self._items = {k: v for k, v in self._items.items()
                           if v.status in ('downloading', 'queued')}

    # ── 控制（GUI thread 呼叫） ──────────────────────────
    def cancel(self, id_: str) -> bool:
        """Kill 對應的 subprocess（如果還活著）。回傳 True 表示有東西被殺。

        標記 _cancelled[id] 給背景 thread 醒來時查詢。
        """
        import subprocess as _sp
        with self._lock:
            proc = self._procs.get(id_)
            self._cancelled.add(id_)
        if proc is None:
            return False
        try:
            if sys.platform == 'win32':
                # Windows 用 taskkill /F /T 砍整個 process tree（yt-dlp 可能 spawn ffmpeg 子進程）
                _sp.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True)
            else:
                proc.terminate()  # SIGTERM → yt-dlp 收到會 clean shutdown
                try:
                    proc.wait(timeout=5)
                except _sp.TimeoutExpired:
                    proc.kill()    # SIGKILL
            return True
        except Exception:
            return False


# ── 格式化 helper ───────────────────────────────────────
def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return '0 B'
    units = ['B','KB','MB','GB','TB']
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f'{f:.1f} {units[i]}'

def _fmt_eta(sec: int) -> str:
    if sec <= 0:
        return ''
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'

def _fmt_speed(bps: int) -> str:
    if bps <= 0:
        return ''
    return f'{_fmt_bytes(bps)}/s'


# ═══════════════════════════════════════════════════════════
# GUI — 簡潔版
# ═══════════════════════════════════════════════════════════
def run_gui(api_key: str):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title('萌龍下載器 v2.0')
    root.geometry('900x600')

    # v2.0 簡潔 GUI 結構
    db = MovieDB(DB_PATH)
    client = MlongClient(DEFAULT_SERVER, api_key)

    # v2.2：下載進度追蹤（thread-safe，背景 thread 寫、GUI thread poll）
    tracker = DownloadTracker()

    # ── 頂部：搜尋 bar + 按鈕列 ──────────────────────────
    top = ttk.Frame(root, padding=10)
    top.pack(fill='x')

    ttk.Label(top, text='🔍 搜尋:').pack(side='left')
    query_var = tk.StringVar()
    entry = ttk.Entry(top, textvariable=query_var, width=40)
    entry.pack(side='left', padx=5)

    # v2.0 修：do_search 必須在使用前定義（Python nested function late-binding）
    def do_search():
        q = query_var.get().strip()
        result_listbox.delete(0, 'end')
        if not q:
            result_frame.pack_forget()  # 隱藏結果區

    # 更新 DB 按鈕 (dropdown menu)
    update_btn = ttk.Menubutton(top, text='更新 DB ▼')
    update_menu = tk.Menu(update_btn, tearoff=0)
    update_menu.add_command(label='更新整個 DB (慢, 14分鐘)',
                           command=lambda: update_all())
    update_menu.add_command(label='更新單個 series (打 ID)',
                           command=lambda: update_single())
    update_menu.add_command(label='更新單個 series (從搜尋結果)',
                           command=lambda: update_from_selection())
    update_btn['menu'] = update_menu
    update_btn.pack(side='left', padx=20)

    # 下載目錄 Menubutton（顯示目前路徑，dropdown：改資料夾 / 打開）
    download_dir_btn = ttk.Menubutton(top, text='📂 ' + str(get_download_dir()))
    download_dir_menu = tk.Menu(download_dir_btn, tearoff=0)
    download_dir_menu.add_command(label='📂 改資料夾...', command=lambda: change_download_dir())
    download_dir_menu.add_command(label='📁 打開目前資料夾', command=lambda: open_download_dir())
    download_dir_menu.add_separator()
    download_dir_menu.add_command(label='↺ 回到預設', command=lambda: reset_download_dir())
    download_dir_btn['menu'] = download_dir_menu
    download_dir_btn.pack(side='right')

    # ── 結果區 (lazy — 沒搜尋結果不顯示) ──────────────────────
    # ── Notebook 分頁：搜尋結果 / 下載進度 ────────────────
    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True, padx=10, pady=5)

    # ── 分頁 1：搜尋結果 ─────────────────────────────────
    search_page = ttk.Frame(notebook, padding=5)
    notebook.add(search_page, text='🔍 搜尋結果')

    result_frame = ttk.LabelFrame(search_page, text='搜尋結果', padding=5)

    result_listbox = tk.Listbox(result_frame, font=('TkFixedFont', 11), height=20)
    scrollbar = ttk.Scrollbar(result_frame, orient='vertical',
                             command=result_listbox.yview)
    result_listbox.config(yscrollcommand=scrollbar.set)
    result_listbox.pack(side='left', fill='both', expand=True)
    scrollbar.pack(side='right', fill='y')

    # 雙擊 = 下載 / 右鍵 = popup menu
    result_listbox.bind('<Double-Button-1>', lambda e: on_download_selected())
    result_listbox.bind('<Button-3>', lambda e: on_right_click(e))

# v2.0 修：do_search 必須在使用前定義（Python nested function late-binding）
    def do_search():
        q = query_var.get().strip()
        result_listbox.delete(0, 'end')
        if not q:
            result_frame.pack_forget()  # 隱藏結果區
            return
        results = db.search(q, limit=100)
        if not results:
            result_frame.pack(fill='both', expand=True, padx=10, pady=5)
            result_listbox.insert('end', '(找不到結果)')
            return
        result_frame.pack(fill='both', expand=True, padx=10, pady=5)
        for m in results:
            result_listbox.insert('end', db.get_display(m['id']))
        # 記住結果給 download 用
        do_search.results = results
        do_search.query = q
    do_search.results = []
    do_search.query = ''

    # ── 分頁 2：下載進度 ─────────────────────────────────
    progress_page = ttk.Frame(notebook, padding=5)
    notebook.add(progress_page, text='📥 下載進度')

    progress_toolbar = ttk.Frame(progress_page)
    progress_toolbar.pack(fill='x', pady=(0, 5))
    ttk.Button(progress_toolbar, text='🔄 清除已完成',
               command=lambda: tracker.clear_finished()).pack(side='left', padx=2)
    ttk.Button(progress_toolbar, text='✕ 停止選中',
               command=lambda: cancel_selected()).pack(side='left', padx=2)
    progress_summary = ttk.Label(progress_toolbar, text='')
    progress_summary.pack(side='right', padx=5)

    progress_tree_container = ttk.Frame(progress_page)
    progress_tree_container.pack(fill='both', expand=True)

    progress_tree = ttk.Treeview(progress_tree_container,
                                 columns=('name', 'path', 'progress', 'size', 'speed', 'eta', 'status'),
                                 show='headings', height=18)
    progress_tree.heading('name', text='名稱')
    progress_tree.heading('path', text='位置')
    progress_tree.heading('progress', text='進度')
    progress_tree.heading('size', text='大小')
    progress_tree.heading('speed', text='速度')
    progress_tree.heading('eta', text='剩餘')
    progress_tree.heading('status', text='狀態')
    progress_tree.column('name', width=220, anchor='w')
    progress_tree.column('path', width=180, anchor='w')
    progress_tree.column('progress', width=130, anchor='w')
    progress_tree.column('size', width=130, anchor='e')
    progress_tree.column('speed', width=85, anchor='e')
    progress_tree.column('eta', width=65, anchor='e')
    progress_tree.column('status', width=55, anchor='center')

    prog_scroll = ttk.Scrollbar(progress_tree_container, orient='vertical',
                                command=progress_tree.yview)
    progress_tree.configure(yscrollcommand=prog_scroll.set)
    progress_tree.pack(side='left', fill='both', expand=True)
    prog_scroll.pack(side='right', fill='y')

    # status 圖示
    _STATUS_ICON = {'queued': '⏳', 'downloading': '⬇', 'ok': '✓', 'error': '✗', 'cancelled': '⏹'}

    def _progress_bar_text(downloaded: int, total: int, width: int = 14) -> str:
        if total <= 0:
            return '░' * width + '  ?%'
        ratio = max(0.0, min(1.0, downloaded / total))
        filled = int(ratio * width)
        return '▓' * filled + '░' * (width - filled) + f' {int(ratio * 100):3d}%'

    def refresh_progress():
        snap = tracker.snapshot()
        # 只在 items 有變化時重建（避免每 0.5s flash）
        existing = {progress_tree.set(iid, 'name'): iid for iid in progress_tree.get_children()}
        new_iids = set()
        for it in snap:
            name = it['name']
            iid = existing.get(name)
            size_text = (f'{_fmt_bytes(it["downloaded"])} / {_fmt_bytes(it["total"])}'
                         if it['total'] > 0 else f'{_fmt_bytes(it["downloaded"])} / ?')
            # 顯示 path：太長截斷中間（...）
            path_disp = it.get('path', '')
            if len(path_disp) > 30:
                path_disp = path_disp[:12] + '...' + path_disp[-15:]
            row = (name,
                   path_disp,
                   _progress_bar_text(it['downloaded'], it['total']),
                   size_text,
                   _fmt_speed(it['speed']),
                   _fmt_eta(it['eta']),
                   _STATUS_ICON.get(it['status'], ''))
            if iid is None:
                progress_tree.insert('', 'end', iid=name, values=row)
            else:
                progress_tree.item(iid, values=row)
            new_iids.add(iid or name)
        # 刪掉 tracker 已清除的
        for iid in list(progress_tree.get_children()):
            if progress_tree.set(iid, 'name') not in {it['name'] for it in snap}:
                progress_tree.delete(iid)
        # 摘要
        n_total = len(snap)
        n_active = sum(1 for it in snap if it['status'] == 'downloading')
        n_done = sum(1 for it in snap if it['status'] == 'ok')
        n_err = sum(1 for it in snap if it['status'] == 'error')
        n_cancel = sum(1 for it in snap if it['status'] == 'cancelled')
        progress_summary.config(
            text=f'總計 {n_total} · 下載中 {n_active} · 完成 {n_done} · 失敗 {n_err} · 取消 {n_cancel}')
        root.after(500, refresh_progress)

    refresh_progress()

    def cancel_selected():
        sel = progress_tree.selection()
        if not sel:
            messagebox.showinfo('提示', '請先在進度頁選一個 row')
            return
        iid = sel[0]
        # 從 iid (name) 找對應 tracker item id
        snap = tracker.snapshot()
        name_to_id = {it['name']: it['id'] for it in snap}
        item_id = name_to_id.get(iid)
        if not item_id:
            messagebox.showinfo('提示', '找不到對應的下載 task')
            return
        # 找出狀態
        target = next((it for it in snap if it['id'] == item_id), None)
        if target and target['status'] in ('ok', 'error', 'cancelled'):
            messagebox.showinfo('提示', f'{target["name"]} 已經 {target["status"]}，不用停止')
            return
        if not messagebox.askyesno('確認停止',
            f'停止下載「{target["name"] if target else item_id}」？\n\n'
            f'yt-dlp 會被 kill，已下載的部分不會保留（--no-part）。'):
            return
        ok = tracker.cancel(item_id)
        if ok:
            update_status(f'已要求停止：{item_id}')
        else:
            update_status(f'停止失敗（process 不存在）：{item_id}')

    # 右鍵 menu on progress_tree
    def on_progress_right_click(event):
        iid = progress_tree.identify_row(event.y)
        if not iid:
            return
        progress_tree.selection_set(iid)
        snap = tracker.snapshot()
        name_to_id = {it['name']: it['id'] for it in snap}
        item_id = name_to_id.get(iid)
        target = next((it for it in snap if it['id'] == item_id), None) if item_id else None
        menu = tk.Menu(root, tearoff=0)
        state = target['status'] if target else 'unknown'
        can_cancel = target and state in ('downloading', 'queued')
        menu.add_command(label=f'ℹ️ 狀態：{state}', state='disabled')
        if can_cancel:
            menu.add_command(label='✕ 停止',
                             command=lambda: do_cancel_item(item_id, target))
        else:
            menu.add_command(label='✕ 停止（不可用）', state='disabled')
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def do_cancel_item(item_id, target):
        if not messagebox.askyesno('確認停止',
            f'停止下載「{target["name"]}」？'):
            return
        ok = tracker.cancel(item_id)
        update_status(f'已要求停止：{target["name"]}' if ok else f'停止失敗：{target["name"]}')

    progress_tree.bind('<Button-3>', lambda e: on_progress_right_click(e))

    # 綁定 Enter 鍵和按鈕（在 do_search 定義後才能綁）
    entry.bind('<Return>', lambda e: do_search())
    ttk.Button(top, text='搜尋', command=do_search).pack(side='left', padx=2)

    # 更新 DB 按鈕 (dropdown menu)

    # ── 下載 ──────────────────────────────────────────────
    def on_download_selected():
        sel = result_listbox.curselection()
        if not sel:
            messagebox.showinfo('提示', '請先選一筆')
            return
        if not do_search.results:
            messagebox.showinfo('提示', '請先搜尋')
            return
        idx = sel[0]
        if idx >= len(do_search.results):
            return
        item = do_search.results[idx]

        # Series 自動展開成 episodes — 開 Detail Dialog 讓用戶選
        if item.get('type') == 'Series':
            episodes = db.get_episodes_for_series(item['id'])
            if not episodes:
                messagebox.showinfo('提示', '這個 series 沒有 episodes（DB 沒資料）')
                return
            # v2.0：開 Detail Dialog（顯示每集 checkbox + 全選/全不選）
            open_series_detail_dialog(item, episodes)
        else:
            # 單部下載 (Movie 或 Episode)
            threading.Thread(target=download_one_thread,
                           args=(item,), daemon=True).start()

    # v2.0：Series Detail Dialog（列出每集 checkbox + 全選/全不選/反選）
    def open_series_detail_dialog(series, episodes):
        """v2.0：彈出 dialog 顯示 series 所有 episodes，每集一個 checkbox。
        用戶可選要下載哪些。確認後呼叫 download_episodes_series。
        """
        dialog = tk.Toplevel(root)
        dialog.title(f'📺 {series["name"]} — 選擇集數')
        dialog.geometry('700x600')
        dialog.transient(root)
        dialog.grab_set()

        # ── 標題 ──
        title_frame = ttk.Frame(dialog, padding=10)
        title_frame.pack(fill='x')
        ttk.Label(title_frame,
                  text=f'📺 {series["name"]}  ({len(episodes)} 集)',
                  font=('TkDefaultFont', 12, 'bold')).pack(side='left')

        # ── 全選 / 全不選 / 反選 按鈕 ──
        # checkboxes 存在 dialog.check_vars (dict: ep_id -> BooleanVar)
        dialog.check_vars = {}
        btn_frame = ttk.Frame(dialog, padding=(10, 0))
        btn_frame.pack(fill='x')

        def select_all():
            for v in dialog.check_vars.values():
                v.set(True)
            update_count()

        def select_none():
            for v in dialog.check_vars.values():
                v.set(False)
            update_count()

        def invert_selection():
            for v in dialog.check_vars.values():
                v.set(not v.get())
            update_count()

        ttk.Button(btn_frame, text='✓ 全選', command=select_all, width=8).pack(side='left', padx=2)
        ttk.Button(btn_frame, text='✗ 全不選', command=select_none, width=8).pack(side='left', padx=2)
        ttk.Button(btn_frame, text='↔ 反選', command=invert_selection, width=8).pack(side='left', padx=2)

        # 計數顯示
        dialog.count_label = ttk.Label(btn_frame, text='')
        dialog.count_label.pack(side='right', padx=10)

        # ── 集數列表（用 Canvas + Frame 達成可滾動） ──
        list_container = ttk.Frame(dialog, padding=10)
        list_container.pack(fill='both', expand=True)

        canvas = tk.Canvas(list_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        def on_mouse_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        # Windows / Linux 的滾輪事件
        canvas.bind_all('<MouseWheel>', on_mouse_wheel)
        # Linux 的滾輪 (Button-4/5)
        canvas.bind_all('<Button-4>', lambda e: canvas.yview_scroll(-1, 'units'))
        canvas.bind_all('<Button-5>', lambda e: canvas.yview_scroll(1, 'units'))

        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 產生每集的 checkbox
        def update_count():
            sel = sum(1 for v in dialog.check_vars.values() if v.get())
            dialog.count_label.config(text=f'已選 {sel}/{len(episodes)}')

        for ep in episodes:
            sn = ep.get('season') or 1
            en = ep.get('episode') or 0
            runtime_min = ep.get('runtime_ticks', 0) // 600000000
            runtime_str = f'{runtime_min}分' if runtime_min else ''
            label_text = f"S{int(sn):02d}E{int(en):02d}  {ep['name']}  ({runtime_str})"

            var = tk.BooleanVar(value=True)  # 預設全選
            dialog.check_vars[ep['id']] = var
            cb = ttk.Checkbutton(scroll_frame, text=label_text, variable=var,
                                command=update_count)
            cb.pack(anchor='w', padx=5, pady=2)

        update_count()

        # ── 底部按鈕 ──
        bottom_frame = ttk.Frame(dialog, padding=10)
        bottom_frame.pack(fill='x')

        def on_confirm():
            selected = [ep for ep in episodes if dialog.check_vars.get(ep['id'], tk.BooleanVar(value=False)).get()]
            if not selected:
                messagebox.showinfo('提示', '請至少勾選一集', parent=dialog)
                return
            dialog.destroy()
            # 用新 thread 跑下載
            threading.Thread(target=download_episodes_series,
                            args=(series, selected), daemon=True).start()

        def on_cancel():
            dialog.destroy()

        ttk.Button(bottom_frame, text=f'▶ 下載勾選', command=on_confirm).pack(side='left', padx=5)
        ttk.Button(bottom_frame, text='取消', command=on_cancel).pack(side='right', padx=5)

        # Dialog 關閉時清 canvas mousewheel bind
        def on_dialog_close():
            canvas.unbind_all('<MouseWheel>')
            dialog.destroy()

        dialog.protocol('WM_DELETE_WINDOW', on_dialog_close)

    def download_episodes_series(series, episodes):
        # 切到進度頁
        notebook.select(1)
        cur_dir = get_download_dir()
        for i, ep in enumerate(episodes, 1):
            update_status(f'[{i}/{len(episodes)}] 下載: {ep["name"]}')
            result_listbox.selection_clear(0, 'end')
            result_listbox.selection_set(i - 1 if i - 1 < result_listbox.size() else 0)
            root.update_idletasks()
            tracker.add(ep['id'], ep['name'], ep.get('type', 'Episode'), str(cur_dir))
            def _cb(d, t, s, e, _id=ep['id']):
                tracker.update_progress(_id, d, t, s, e)
            status, info = download_one(ep, str(cur_dir), api_key,
                                        progress_callback=_cb, tracker=tracker)
            if status == 'cancelled':
                tracker.mark_cancelled(ep['id'])
                update_status(f'⏹ {ep["name"]} 已停止')
                return
            elif status == 'error':
                tracker.mark_done(ep['id'], ok=False, error=str(info))
                update_status(f'✗ {ep["name"]}: {info}')
                messagebox.showerror('失敗', f'{ep["name"]}: {info}')
                return
            elif status == 'ok':
                tracker.mark_done(ep['id'], ok=True)
                update_status(f'✓ {i}/{len(episodes)} {ep["name"]} 完成')
            root.update_idletasks()
        update_status(f'✓✓✓ {series["name"]} 全部 {len(episodes)} 集完成！')
        messagebox.showinfo('完成', f'下載完成：{len(episodes)} 集')

    def download_one_thread(item):
        # 切到進度頁
        notebook.select(1)
        cur_dir = get_download_dir()
        tracker.add(item['id'], item['name'], item.get('type', 'Movie'), str(cur_dir))
        def _cb(d, t, s, e, _id=item['id']):
            tracker.update_progress(_id, d, t, s, e)
        status, info = download_one(item, str(cur_dir), api_key,
                                    progress_callback=_cb, tracker=tracker)
        if status == 'cancelled':
            tracker.mark_cancelled(item['id'])
            update_status(f'⏹ {item["name"]} 已停止')
        elif status == 'error':
            tracker.mark_done(item['id'], ok=False, error=str(info))
            update_status(f'✗ {item["name"]}: {info}')
            messagebox.showerror('失敗', f'{item["name"]}: {info}')
        elif status == 'ok':
            tracker.mark_done(item['id'], ok=True)
            update_status(f'✓ {item["name"]} → {info}')

    # ── 更新 DB ────────────────────────────────────────────
    def update_all():
        if not messagebox.askyesno('更新整個 DB',
            '這會花 14 分鐘從萌龍抓所有 17 個 folder 的電影。\n\n繼續？'):
            return
        threading.Thread(target=run_update_all, daemon=True).start()

    def run_update_all():
        try:
            update_status('更新整個 DB 中... 這可能要 14 分鐘')
            all_movies = []
            for label, parent_id in KNOWN_FOLDERS.items():
                if parent_id is None:
                    continue
                include_types = FOLDER_TYPES.get(label, ['Movie'])
                update_status(f'  抓 {label} (ParentId={parent_id})...')
                movies = client.list_folder(parent_id, label, include_types=include_types)
                all_movies.extend(movies)
                update_status(f'  ✓ {label}: {len(movies)} 項')
            # 去重
            seen = set()
            unique = []
            for m in all_movies:
                if m['id'] not in seen:
                    seen.add(m['id'])
                    unique.append(m)
            db.movies = unique
            db.save()
            db._build_indexes()
            update_status(f'✓ DB 更新完成：{len(unique):,} 部')
            messagebox.showinfo('完成', f'更新完成：{len(unique):,} 部')
        except Exception as e:
            update_status(f'✗ 更新失敗: {e}')
            messagebox.showerror('失敗', str(e))

    def update_single():
        sid = tk.simpledialog.askstring('更新單個 series',
            '打 series ID (例：375871 = 蘭香如故):', parent=root)
        if not sid:
            return
        sid = sid.strip()
        threading.Thread(target=run_update_single,
                        args=(sid,), daemon=True).start()

    def run_update_single(sid):
        try:
            update_status(f'更新 series {sid} 中...')
            # 先從 DB 找 folder
            existing = db.get(sid)
            label = existing.get('folder', '') if existing else ''
            new_items = client.get_series_full_tree(sid, label=label)
            update_status(f'fetch 到 {len(new_items)} 筆')

            # 移除舊的 + 加入新的
            ids_to_remove = db.get_series_tree_ids(sid) if existing else {sid}
            before = len(db.movies)
            db.movies = [m for m in db.movies if m['id'] not in ids_to_remove]
            after_remove = len(db.movies)
            update_status(f'移除 {before - after_remove} 筆舊 entry')

            seen = {m['id'] for m in db.movies}
            added = 0
            for it in new_items:
                if it['id'] not in seen:
                    db.movies.append(it)
                    seen.add(it['id'])
                    added += 1
            update_status(f'加入 {added} 筆新 entry')
            db.save()
            db._build_indexes()
            update_status(f'✓ series {sid} 更新完成 ({len(new_items)} 筆)')
        except Exception as e:
            update_status(f'✗ 更新失敗: {e}')
            messagebox.showerror('失敗', str(e))

    def update_from_selection():
        sel = result_listbox.curselection()
        if not sel:
            messagebox.showinfo('提示', '請先在搜尋結果選一個 series')
            return
        if not do_search.results:
            messagebox.showinfo('提示', '請先搜尋')
            return
        idx = sel[0]
        if idx >= len(do_search.results):
            return
        item = do_search.results[idx]
        if item.get('type') != 'Series':
            messagebox.showinfo('提示', '只有 series 可以更新')
            return
        threading.Thread(target=run_update_single,
                        args=(item['id'],), daemon=True).start()

    # ── v2.4 右鍵 menu handlers ────────────────────────────
    def update_item_from_right_click(item):
        """右鍵「更新」：依 item.type 重抓
        - Series:        get_series_full_tree
        - Episode/Season: 找 parent series → get_series_full_tree
        - Movie:         重抓整個 folder
        """
        t = item.get('type', 'Movie')
        if t == 'Series':
            label = item.get('folder', '')
            if not messagebox.askyesno('確認更新',
                f'重抓 series「{item["name"]}」({item["id"]}) 整個 tree？\n\n'
                f'會替換 DB 裡這個 series 的 seasons + episodes。'):
                return
            threading.Thread(target=run_update_single,
                            args=(item['id'],), daemon=True).start()
        elif t in ('Episode', 'Season'):
            parent = db.find_series_for_episode(item['id'])
            if not parent:
                messagebox.showerror('錯誤', f'找不到 episode 的 parent series\n'
                                       f'（DB 裡 series「{item.get("series_name","?")}」不存在）')
                return
            if not messagebox.askyesno('確認更新',
                f'從 episode「{item["name"]}」找到 parent series「{parent["name"]}」\n\n'
                f'重抓整個 series tree？這會替換所有 seasons + episodes。'):
                return
            threading.Thread(target=run_update_single,
                            args=(parent['id'],), daemon=True).start()
        elif t == 'Movie':
            parent_id, folder = db.find_parent_id_for_movie(item['id'])
            if not parent_id:
                messagebox.showerror('錯誤',
                    f'找不到 movie 對應的 folder\n'
                    f'（item.folder={folder or "?"} 不在 KNOWN_FOLDERS）')
                return
            if not messagebox.askyesno('確認更新',
                f'重抓整個 folder「{folder}」(ParentId={parent_id})？\n\n'
                f'⚠️ 這會用萌龍最新資料「整個替換」folder 內所有 movie。'):
                return
            threading.Thread(target=run_update_folder,
                            args=(folder, parent_id), daemon=True).start()
        else:
            messagebox.showinfo('提示', f'type={t} 不支援右鍵更新')

    def run_update_folder(folder_label, parent_id):
        try:
            update_status(f'重抓 folder {folder_label} (ParentId={parent_id})...')
            include_types = FOLDER_TYPES.get(folder_label, ['Movie'])
            new_items = client.list_folder(parent_id, folder_label, include_types=include_types)
            seen = {it['id'] for it in new_items}
            # 用 folder 標籤當 filter key
            before = len(db.movies)
            db.movies = [m for m in db.movies if m.get('folder') != folder_label]
            after_remove = len(db.movies)
            update_status(f'移除 {before - after_remove} 筆舊 {folder_label} entry')
            added = 0
            for it in new_items:
                db.movies.append(it)
                added += 1
            db.save()
            db._build_indexes()
            update_status(f'✓ folder {folder_label} 更新完成（{added} 筆）')
            messagebox.showinfo('完成', f'folder「{folder_label}」更新完成：{added} 筆')
        except Exception as e:
            update_status(f'✗ 更新失敗: {e}')
            messagebox.showerror('失敗', str(e))

    def copy_id_to_clipboard(item):
        root.clipboard_clear()
        root.clipboard_append(item['id'])
        update_status(f'已複製 ID：{item["id"]}')

    def expand_series_dialog_from_right(item):
        """對 Series 開 Detail Dialog（雙擊效果）"""
        if item.get('type') != 'Series':
            messagebox.showinfo('提示', '只有 series 可以展開')
            return
        episodes = db.get_episodes_for_series(item['id'])
        if not episodes:
            messagebox.showinfo('提示', '這個 series 沒有 episodes（DB 沒資料）')
            return
        open_series_detail_dialog(item, episodes)

    def download_from_right(item):
        """對 Movie/Episode 直接下載，Series 走 dialog"""
        if item.get('type') == 'Series':
            expand_series_dialog_from_right(item)
        else:
            threading.Thread(target=download_one_thread,
                           args=(item,), daemon=True).start()

    def on_right_click(event):
        # 找出點到哪個 row
        idx = result_listbox.nearest(event.y)
        if idx < 0:
            return
        # 先選起來
        result_listbox.selection_clear(0, 'end')
        result_listbox.selection_set(idx)
        result_listbox.activate(idx)
        if idx >= len(do_search.results):
            return
        item = do_search.results[idx]

        menu = tk.Menu(root, tearoff=0)
        t = item.get('type', 'Movie')
        menu.add_command(label='📥 下載',
                         command=lambda: download_from_right(item))
        if t == 'Series':
            menu.add_command(label='🔍 展開 episodes',
                             command=lambda: expand_series_dialog_from_right(item))
        menu.add_command(label='🔄 更新該 episodes list',
                         command=lambda: update_item_from_right_click(item))
        menu.add_separator()
        menu.add_command(label='📋 複製 ID',
                         command=lambda: copy_id_to_clipboard(item))
        menu.add_command(label=f'ℹ️ 類型：{t} · folder：{item.get("folder","?")}',
                         state='disabled')
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ── 開下載資料夾 ──────────────────────────────────────
    def open_download_dir():
        try:
            cur = get_download_dir()
            cur.mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(str(cur))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(cur)])
            else:
                subprocess.Popen(['xdg-open', str(cur)])
        except Exception as e:
            messagebox.showerror('錯誤', str(e))

    def change_download_dir():
        from tkinter import filedialog
        new = filedialog.askdirectory(title='選擇下載資料夾',
                                      initialdir=str(get_download_dir()))
        if not new:
            return
        set_download_dir(new)
        download_dir_btn.config(text='📂 ' + str(get_download_dir()))
        update_status(f'下載目錄已改為 {get_download_dir()}')

    def reset_download_dir():
        global _current_download_dir
        _current_download_dir = None
        download_dir_btn.config(text='📂 ' + str(get_download_dir()))
        update_status(f'下載目錄已回到預設 {get_download_dir()}')

    # ── 底部 status bar ─────────────────────────────────────
    status_var = tk.StringVar(value=f'就緒 · {len(db.movies):,} 筆')
    ttk.Label(root, textvariable=status_var, relief='sunken', anchor='w',
              padding=5).pack(fill='x', side='bottom')

    def update_status(msg):
        status_var.set(msg)
        root.update_idletasks()

    # 初始 focus
    entry.focus()
    root.mainloop()


# ═══════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════
def cmd_gui(args):
    api_key = args.api_key or os.environ.get('MLONG_API_KEY', '')
    if not api_key:
        print('✗ 需要 api_key（--api-key 或 MLONG_API_KEY env）')
        sys.exit(1)
    run_gui(api_key)


def cmd_update(args):
    api_key = args.api_key or os.environ.get('MLONG_API_KEY', '')
    if not api_key:
        print('✗ 需要 api_key')
        sys.exit(1)
    client = MlongClient(DEFAULT_SERVER, api_key)
    db = MovieDB()

    if getattr(args, 'series_id', None):
        # 單 series
        sid = args.series_id
        print(f'▶ 更新 series {sid}...')
        existing = db.get(sid)
        label = existing.get('folder', '') if existing else ''
        new_items = client.get_series_full_tree(sid, label=label)
        print(f'  fetch 到 {len(new_items)} 筆')
        ids_to_remove = db.get_series_tree_ids(sid) if existing else {sid}
        db.movies = [m for m in db.movies if m['id'] not in ids_to_remove]
        seen = {m['id'] for m in db.movies}
        added = 0
        for it in new_items:
            if it['id'] not in seen:
                db.movies.append(it)
                seen.add(it['id'])
                added += 1
        print(f'  加入 {added} 筆新 entry')
        db.save()
        db._build_indexes()
        print(f'✓ series {sid} 更新完成')
        return

    print('▶ 從萌龍同步所有電影清單...')
    all_movies = []
    for label, parent_id in KNOWN_FOLDERS.items():
        if parent_id is None:
            continue
        include_types = FOLDER_TYPES.get(label, ['Movie'])
        print(f'  抓 {label}...')
        movies = client.list_folder(parent_id, label, include_types=include_types)
        print(f'  ✓ {label}: {len(movies)} 項')
        all_movies.extend(movies)
    seen = set()
    unique = []
    for m in all_movies:
        if m['id'] not in seen:
            seen.add(m['id'])
            unique.append(m)
    db.movies = unique
    db.save()
    print(f'✓ DB 更新完成：{len(unique)} 部 → {db.path}')


def cmd_download(args):
    """直接下載一個 ID（CLI 模式）。"""
    api_key = args.api_key or os.environ.get('MLONG_API_KEY', '')
    if not api_key:
        print('✗ 需要 api_key')
        sys.exit(1)
    db = MovieDB()
    item = db.get(args.item_id)
    if not item:
        item = {'id': args.item_id, 'name': f'item_{args.item_id}', 'type': 'Movie'}
    out_dir = args.output or str(get_download_dir())
    os.environ['MLONG_API_KEY'] = api_key  # 給 download_one 用

    if item.get('type') == 'Series':
        eps = db.get_episodes_for_series(item['id'])
        print(f'▶ Series「{item["name"]}」展開 {len(eps)} 集')
        for i, ep in enumerate(eps, 1):
            status, info = download_one(ep, out_dir, api_key)
            print(f'  [{i}/{len(eps)}] {ep["name"]}: {status} {info if info else ""}')
    else:
        status, info = download_one(item, out_dir, api_key)
        print(f'{item["name"]}: {status} {info if info else ""}')


def cmd_search(args):
    db = MovieDB()
    results = db.search(args.query, limit=50)
    for m in results:
        print(db.get_display(m['id']))


def main():
    import argparse
    p = argparse.ArgumentParser(
        description='萌龍下載器 v2.0',
        epilog='用法：python mlong-dl.py [gui|update|download|search] [args]',
    )
    p.add_argument('--api-key', help='萌龍 api key（也可 MLONG_API_KEY env）')

    sub = p.add_subparsers(dest='cmd')

    # gui
    p_gui = sub.add_parser('gui', help='開 GUI')
    p_gui.set_defaults(func=cmd_gui)

    # update
    p_upd = sub.add_parser('update', help='更新 DB')
    p_upd.add_argument('--series', dest='series_id', help='只更新某個 series')
    p_upd.set_defaults(func=cmd_update)

    # download
    p_dl = sub.add_parser('download', aliases=['dl'], help='下載一個 ID')
    p_dl.add_argument('item_id')
    p_dl.add_argument('-o', '--output', help='下載目錄')
    p_dl.set_defaults(func=cmd_download)

    # search
    p_search = sub.add_parser('search', help='搜尋 DB')
    p_search.add_argument('query')
    p_search.set_defaults(func=cmd_search)

    args = p.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        # 預設開 GUI
        args.api_key = args.api_key or os.environ.get('MLONG_API_KEY', '')
        if not args.api_key:
            print('請提供 --api-key 或設 MLONG_API_KEY env')
            print('或用: python mlong-dl.py gui --api-key xxx')
            sys.exit(1)
        run_gui(args.api_key)


if __name__ == '__main__':
    main()