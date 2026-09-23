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


def detect_nas_dir() -> Optional[Path]:
    """v1.5.4：自動偵測 NAS 共享資料夾（給用戶下載用）。
    平台特定：
      Windows: 掃常見 drive letter 找大容量 / UNC 路徑
      macOS:   /Volumes/*
      Linux:   /mnt/*, /media/*
    找不到回傳 None。
    """
    import platform
    candidates = []
    system = platform.system()

    try:
        if system == 'Windows':
            # 先用 net use 找出所有 UNC mount，建立 drive -> unc 路徑 mapping
            unc_map = {}  # drive letter -> UNC path
            try:
                import subprocess
                # Windows 10/11 語系不同，輸出格式有差。用寬鬆解析。
                out = subprocess.run(['net', 'use'], capture_output=True, text=True, timeout=3,
                                     encoding='mbcs', errors='replace')
                # 找 UNC path + drive letter
                # UNC 格式: \\server\share[\subpath...]
                # 例: "\\192.168.213.60\video"
                # regex 拆解：
                #   r'\\\\' = literal \\\
                #   r'([^\\s]+(?:\\[^\\s]+)*)' = 路徑部分（server\share\sub）
                #   r'\s+([A-Z]):' = 空白 + drive letter
                unc_re = re.compile(r'\\\\([^\\\s]+(?:\\[^\\\s]+)*)\s+([A-Z]):')
                for m in unc_re.finditer(out.stdout):
                    unc_path = '\\\\' + m.group(1)  # 重建完整路徑（含 \\）
                    drive_letter = m.group(2) + ':'
                    unc_map[drive_letter] = unc_path
            except Exception:
                pass

            # 掃 drive letter A-Z
            import string
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:")
                if not drive.exists():
                    continue
                try:
                    drive_key = f"{letter}:"
                    unc_path = unc_map.get(drive_key, '')

                    if unc_path:
                        # 只有真正有 UNC mount 的才視為 NAS
                        # 排除本機硬碟（C:/ D:/ 等沒掛 UNC 的）
                        candidates.append((drive, unc_path))
                except (OSError, ImportError):
                    pass
        elif system == 'Darwin':  # macOS
            for p in Path('/Volumes').iterdir():
                if p.is_dir() and not p.name.startswith('.'):
                    candidates.append((p, p.name))
        else:  # Linux
            for base in ['/mnt', '/media', '/run/media']:
                base_p = Path(base)
                if not base_p.exists():
                    continue
                for p in base_p.iterdir():
                    if p.is_dir() and not p.name.startswith('.'):
                        candidates.append((p, p.name))
    except Exception:
        pass

    # 優先順序：UNC（NAS） > 名稱含 nas/diskstation/synology > 大容量 > 第一個
    def score_candidate(c):
        path, name = c
        name_lower = name.lower()
        if '\\\\' in name:
            return (0, name)
        if any(k in name_lower for k in ('nas', 'diskstation', 'synology', 'qnap', 'truenas')):
            return (1, name)
        # 大容量 > 1TB 優先
        try:
            import shutil
            gb = shutil.disk_usage(str(path)).total / (1024**3)
            if gb >= 1000:
                return (2, name)
        except:
            pass
        return (3, name)

    if not candidates:
        return None
    candidates.sort(key=score_candidate)
    return candidates[0][0]

# 萌龍雅軒的 library folder ID（從 /Items?ParentId=2 取得 17 個 folder）
# 電影 6 個 + 劇集 9 個 + 特殊 2 個
KNOWN_FOLDERS = {
    # ── 電影 ──────────────────────────────
    "chinese":       3,    # 华语电影 (movies)
    "foreign":       515,  # 外语电影 (movies)
    "anime_movie":   3211, # 动画电影 (movies)
    "horror":        16018, # 恐怖电影 (movies)
    "art":           42629, # 艺术电影 (movies)
    "concert":       65159, # 演唱会 (movies)
    # ── 劇集 (TV) ──────────────────────────
    "chinese_tv":    3359, # 国产剧集 (tvshows)
    "jp_kr_tv":      5345, # 日韩剧集 (tvshows)
    "western_tv":    5510, # 欧美剧集 (tvshows)
    "jp_anime":      5604, # 日番动漫 (tvshows)
    "western_anime": 32397, # 欧美动漫 (tvshows)
    "chinese_anime": 46752, # 国产动漫 (tvshows)
    "kids":          48072, # 儿童节目 (tvshows)
    "variety":       65332, # 综艺节目 (tvshows)
    "documentary":   65336, # 纪录片 (tvshows)
    # ── 特殊 ──────────────────────────────
    "collections":   89934, # 合集 (boxsets)
    "playlists":     105645, # 播放列表 (playlists)
}

# 每個 folder 要抓的 Item 類型（劇集 folder 預設遞迴抓到 Episode）
FOLDER_TYPES = {
    # folder_key: [Type 列表]
    "chinese":       ['Movie'],
    "foreign":       ['Movie'],
    "anime_movie":   ['Movie'],
    "horror":        ['Movie'],
    "art":           ['Movie'],
    "concert":      ['Series', 'Season', 'Episode'],  # 演唱會可能是 series
    "chinese_tv":    ['Series', 'Season', 'Episode'],
    "jp_kr_tv":      ['Series', 'Season', 'Episode'],
    "western_tv":    ['Series', 'Season', 'Episode'],
    "jp_anime":      ['Series', 'Season', 'Episode'],
    "western_anime": ['Series', 'Season', 'Episode'],
    "chinese_anime": ['Series', 'Season', 'Episode'],
    "kids":          ['Series', 'Season', 'Episode'],
    "variety":       ['Series', 'Season', 'Episode'],
    "documentary":   ['Series', 'Season', 'Episode'],
    "collections":   ['Episode'],
    "playlists":     ['Episode'],
}


# ── DB 管理 ─────────────────────────────────────────────────
# ── 繁簡對照 ────────────────────────────────────
# v1.4.5：優先用 zhconv（完整 11,000+ 字對應，~4MB 純 Python）
# 沒裝 zhconv 時 fallback 到手動 dict（60 字，覆蓋 90% 常見字）

try:
    from zhconv import convert as _zhconv_convert
    def _t2s(s: str) -> str:
        """繁 → 簡（用 zhconv）"""
        return _zhconv_convert(s, 'zh-cn')
    def _s2t(s: str) -> str:
        """簡 → 繁（用 zhconv）"""
        return _zhconv_convert(s, 'zh-tw')
    _CONVERTER = 'zhconv'
except ImportError:
    # Fallback：手動對照（不完整但輕量）
    _MANUAL_T2S = {
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
        # v1.4.5 補：常見字但之前漏掉
        '蘭': '兰', '蘭': '兰', '蘭': '兰',  # 重複防呆
        '籃': '篮', '襯': '衬', '陳': '陈', '孫': '孙',
        '隊': '队', '陣': '阵', '對': '对', '點': '点', '線': '线',
        '處': '处', '號': '号', '數': '数', '記': '记', '試': '试',
        '證': '证', '視': '视', '讓': '让', '記': '记', '請': '请',
        '誰': '谁', '調': '调', '變': '变', '觀': '观', '覺': '觉',
        '東': '东', '西': '西', '南': '南', '北': '北', '中': '中',
        '間': '间', '邊': '边', '圍': '围', '遠': '远',
    }
    _MANUAL_S2T = {v: k for k, v in _MANUAL_T2S.items()}

    def _t2s(s: str) -> str:
        return ''.join(_MANUAL_T2S.get(c, c) for c in s)
    def _s2t(s: str) -> str:
        return ''.join(_MANUAL_S2T.get(c, c) for c in s)
    _CONVERTER = 'manual'

# 兼容名字（給現有引用）
T2S_DICT = {c: c for c in _MANUAL_T2S} if _CONVERTER == 'manual' else {}
S2T_DICT = {c: c for c in _MANUAL_S2T} if _CONVERTER == 'manual' else {}


class MovieDB:
    """本地電影 DB（從萌龍同步下來）。

    效能優化（v1.4.0）：
    - load() 時預先計算 display string、name variants、type，方便 GUI 直接用
    - 建 inverted index（2-char substring → item_ids），搜尋 O(k) 而非 O(n)
    - 提供 get_by_id 物件查找（用 dict 而非 list scan）
    """

    # ── 預先算好的 emoji + 預先算好的 display string 模板 ──
    TYPE_EMOJI = {
        'Movie':    '🎬',
        'Series':   '📺',
        'Season':   '📀',
        'Episode':  '🎞️',
    }

    def __init__(self, path = DB_PATH):
        self.path = Path(path) if not isinstance(path, Path) else path
        self.movies: list = []
        # Inverted index: char_pair -> set of movie_id
        # 例: "阿凡" -> {"51465", "51466", ...}
        self._index: dict = {}
        # 簡繁轉換後的 char_pair（方便查詢簡體 query）
        self._index_simp: dict = {}
        # name_lower -> item (cache)
        self._by_id: dict = {}
        # load 全部預先計算
        self.load()

    @staticmethod
    def _safe_int(value, default=0):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def _format_display(self, m: dict) -> str:
        """產生 listbox 顯示用的字串（提前算，後面直接用）。"""
        t = m.get('type', 'Movie')
        year = f" ({m['year']})" if m.get('year') else ''
        ticks = m.get('runtime_ticks', 0)
        runtime = ''
        if ticks:
            total_min = ticks // 600000000
            if total_min:
                runtime = f" [{total_min}分]"
        emoji = self.TYPE_EMOJI.get(t, '❓')

        if t == 'Movie':
            return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"
        elif t == 'Series':
            return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"
        elif t == 'Season':
            sn = self._safe_int(m.get('season'))
            return f"[{m['id']:>10}] {emoji} {m.get('series_name', '?')} - S{sn:02d}"
        elif t == 'Episode':
            sn = self._safe_int(m.get('season'))
            ep = self._safe_int(m.get('episode'))
            series = m.get('series_name') or ''
            return f"[{m['id']:>10}] {emoji} {series} S{sn:02d}E{ep:02d} 「{m['name']}」{runtime}"
        return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"

    @staticmethod
    def _gen_pairs(s: str):
        """產生 2-char sliding window pairs。
        例: '阿凡达' → ['阿凡', '凡达', '达']
        """
        if len(s) < 2:
            yield s
            return
        for i in range(len(s) - 1):
            yield s[i:i+2]

    def load(self):
        if not self.path.exists():
            return
        with open(self.path, encoding='utf-8') as f:
            self.movies = json.load(f)
        self._build_indexes()

    def _build_indexes(self):
        """預先計算：display string + name_lower + name_alt + inverted index。"""
        import time
        t = time.time()

        self._by_id = {}
        self._index = {}        # pair_lower (繁體) -> set
        self._index_simp = {}   # pair_simplified (簡體) -> set

        for m in self.movies:
            mid = m['id']

            # 預先算 display
            m['_display'] = self._format_display(m)

            # name variants
            name = m.get('name', '')
            name_lower = name.lower()
            m['_name_lower'] = name_lower
            # 簡轉繁（給「繁→簡」query 用）
            name_alt = _s2t(name)
            m['_name_alt'] = name_alt.lower()
            # 繁轉簡（給「簡→繁」query 用）— 萌龍存的是簡體所以這條少用
            name_simp = _t2s(name)
            m['_name_simp'] = name_simp.lower()

            self._by_id[mid] = m

            # 建 inverted index — 同時存繁 + 簡 pair
            for variant in (name_lower, name_alt):
                for pair in self._gen_pairs(variant):
                    self._index.setdefault(pair, set()).add(mid)
            # 簡體 pair index（query 是簡體時用）
            for pair in self._gen_pairs(m['_name_simp']):
                self._index_simp.setdefault(pair, set()).add(mid)

        elapsed = time.time() - t
        print(f"  ✓ DB indexed: {len(self.movies):,} items, "
              f"{len(self._index):,} trad pairs, "
              f"{len(self._index_simp):,} simp pairs, "
              f"{elapsed:.1f}s")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 移除 _display / _name_* 等 runtime 欄位再存
        clean = []
        for m in self.movies:
            clean.append({k: v for k, v in m.items()
                         if not k.startswith('_')})
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)

    def search(self, query: str, limit: int = 200) -> list:
        """用 inverted index 搜尋。O(query 長度 × avg pair list)。"""
        q = query.strip().lower()
        if not q:
            return []

        # 兩版本 query（繁 + 簡）
        q_simplified = _t2s(q)
        queries = [q]
        if q_simplified != q:
            queries.append(q_simplified)

        # Strategy:
        # - 第一個 query (q) 假設是 user 打的字（可能是繁或簡）
        #   - 試繁體 index → 簡體 index
        # - 第二個 query (q_simplified) 是轉換版
        #   - 試另一個 index

        results = set()
        primary_pairs = list(self._gen_pairs(queries[0]))
        if primary_pairs:
            # 試繁體 index（query 是繁體時）
            candidate_sets_trad = [self._index.get(p, set()) for p in primary_pairs]
            candidate_sets_trad = [s for s in candidate_sets_trad if s]
            if candidate_sets_trad:
                candidate_sets_trad.sort(key=len)
                candidates = candidate_sets_trad[0].copy()
                for s in candidate_sets_trad[1:]:
                    candidates &= s
                results |= candidates
            # 試簡體 index（query 是簡體時）
            candidate_sets_simp = [self._index_simp.get(p, set()) for p in primary_pairs]
            candidate_sets_simp = [s for s in candidate_sets_simp if s]
            if candidate_sets_simp:
                candidate_sets_simp.sort(key=len)
                candidates = candidate_sets_simp[0].copy()
                for s in candidate_sets_simp[1:]:
                    candidates &= s
                results |= candidates

        # 第二 query（轉換版）也走 index
        if len(queries) > 1 and queries[1] != queries[0]:
            secondary_pairs = list(self._gen_pairs(queries[1]))
            for index_dict in [self._index, self._index_simp]:
                sets = [index_dict.get(p, set()) for p in secondary_pairs]
                sets = [s for s in sets if s]
                if sets:
                    sets.sort(key=len)
                    candidates = sets[0].copy()
                    for s in sets[1:]:
                        candidates &= s
                    results |= candidates

        # 終極 fallback：如果上面都沒找到，substring 掃一次（罕見情況）
        if not results and queries:
            for mid, m in self._by_id.items():
                name = m.get('_name_lower', '')
                name_alt = m.get('_name_alt', '')
                for qq in queries:
                    if qq in name or qq in name_alt:
                        results.add(mid)
                        break

        movies = [self._by_id[mid] for mid in results if mid in self._by_id]

        # 排序：完全 match > 開頭 > 其他
        def get_score(m):
            name = m.get('_name_lower', '')
            name_alt = m.get('_name_alt', '')
            if name in queries or name_alt in queries:
                return (0, len(name))
            for qq in queries:
                if name.startswith(qq) or name_alt.startswith(qq):
                    return (1, len(name))
            return (2, len(name))

        movies.sort(key=get_score)
        return movies[:limit]

    def get(self, item_id: str) -> Optional[dict]:
        return self._by_id.get(item_id)


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

    def list_folder_all(self, parent_id: int, label: str = '',
                         include_types: list = None) -> list:
        """完整抓一個 folder 所有 items（自動翻頁）。
        include_types: list of Jellyfin Type，例如 ['Movie'] 或 ['Series', 'Season', 'Episode']
                       None = 不限類型（但會多抓 folder 物件）。
        """
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
                'Fields': 'Name,ProductionYear,RunTimeTicks,Type,'
                          'SeriesName,SeasonNumber,EpisodeNumber,IndexNumber,'
                          'Overview,ParentId',
            }
            if include_types:
                params['IncludeItemTypes'] = ','.join(include_types)
            r = self.s.get(self._url('/Items'), params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get('Items', [])
            if not items:
                break
            total = data.get('TotalRecordCount', 0)
            for it in items:
                it_type = it.get('Type', '')
                if include_types and it_type not in include_types:
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
                    'type': it_type,
                    # Season/Episode 編號在 Jellyfin 有兩種命名：
                    #   Episode: IndexNumber=ep_num, ParentIndexNumber=season_num
                    #   Season:  IndexNumber=season_num
                    'season': it.get('ParentIndexNumber') if it_type == 'Episode' else it.get('IndexNumber'),
                    'episode': it.get('IndexNumber') if it_type == 'Episode' else None,
                    'series_name': it.get('SeriesName', ''),
                    'runtime_ticks': it.get('RunTimeTicks', 0),
                })
            print(f"  [{label}] {start + len(items)}/{total}", end='\r')
            start += len(items)
            if start >= total or len(items) < page:
                break
        return movies

    def get_seasons(self, series_id: str) -> list:
        """從 Series ID 拿所有 Season 物件。"""
        params = {
            'api_key': self.api_key,
            'ParentId': series_id,
            'Fields': 'Name,IndexNumber,ChildCount,SeriesId',
        }
        r = self.s.get(self._url('/Items'), params=params)
        r.raise_for_status()
        data = r.json()
        return [it for it in data.get('Items', []) if it.get('Type') == 'Season']

    def get_episodes(self, season_id: str) -> list:
        """從 Season ID 拿所有 Episode 物件。"""
        params = {
            'api_key': self.api_key,
            'ParentId': season_id,
            'IncludeItemTypes': 'Episode',
            'Fields': 'Name,IndexNumber,ParentIndexNumber,SeriesId,SeriesName,'
                      'SeasonId,RunTimeTicks',
        }
        r = self.s.get(self._url('/Items'), params=params)
        r.raise_for_status()
        data = r.json()
        return [it for it in data.get('Items', []) if it.get('Type') == 'Episode']

    def get_all_episodes_for_series(self, series_id: str) -> list:
        """從 Series 一次拿所有 episodes（兩步：seasons → each season's episodes）。"""
        seasons = self.get_seasons(series_id)
        all_eps = []
        for s in seasons:
            eps = self.get_episodes(s['Id'])
            all_eps.extend(eps)
        return all_eps

    def get_series_full_tree(self, series_id: str, label: str = '') -> list:
        """v1.5.1：拿整個 series 樹（Series + 所有 Seasons + 所有 Episodes）。
        回傳格式同 list_folder_all 產生的格式，可直接寫入 db.movies。
        """
        items = []

        # 1. Series 自己（萌龍擋 /Items/{id}，用 ?Ids= 取）
        r = self.s.get(self._url('/Items'), params={
            'api_key': self.api_key,
            'Ids': series_id,
        })
        if r.status_code != 200:
            raise RuntimeError(f"GET Items?Ids={series_id} failed: {r.status_code}")
        results = r.json().get('Items', [])
        if not results:
            raise RuntimeError(f"Series {series_id} 不存在或無權限")
        series_item = results[0]
        series_name = series_item.get('Name', '')

        # 2. Seasons + Episodes（recursive）
        params = {
            'api_key': self.api_key,
            'ParentId': series_id,
            'Recursive': 'true',
            'Fields': 'Name,ProductionYear,RunTimeTicks,Type,'
                      'SeriesName,SeasonNumber,EpisodeNumber,IndexNumber,Overview,ParentId',
        }
        r = self.s.get(self._url('/Items'), params=params)
        r.raise_for_status()
        children = r.json().get('Items', [])

        # 3. 組合 + 轉格式
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
                'season': it.get('ParentIndexNumber') if t == 'Episode' else it.get('IndexNumber'),
                'episode': it.get('IndexNumber') if t == 'Episode' else None,
                'series_name': it.get('SeriesName', '') or series_name,
                'runtime_ticks': it.get('RunTimeTicks', 0),
            })
        return items

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
    # v1.5.1: 支援只更新單個 series
    if getattr(args, 'series_id', None):
        return _update_single_series(args.series_id, client, db)

    print("▶ 從萌龍雅軒同步電影清單...")
    client.ping()

    all_movies = []
    for label, parent_id in KNOWN_FOLDERS.items():
        if parent_id is None:
            print(f"  ⚠ {label}: ParentId 未設定，跳過")
            continue
        include_types = FOLDER_TYPES.get(label, ['Movie'])
        print(f"  抓 {label} (ParentId={parent_id}, types={','.join(include_types)})...")
        movies = client.list_folder_all(parent_id, label, include_types=include_types)
        print(f"  ✓ {label}: {len(movies)} 項")
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


def _update_single_series(series_id: str, client: MlongClient, db: MovieDB):
    """v1.5.1：只更新某個 series，不重抓全部。"""
    if not db.movies:
        print("✗ DB 是空的，請先跑 `update`")
        return

    # 找現有 entry 知道 folder
    existing = db.get(series_id)
    if not existing:
        print(f"⚠ DB 沒有 series_id={series_id}，跑 `update` 先建 DB")
        # 但還是 fetch 一次給使用者看
        try:
            items = client.get_series_full_tree(series_id)
            print(f"  萌龍上找到 {len(items)} 筆（Series + Seasons + Episodes）")
            for it in items[:5]:
                print(f"    [{it['id']}] {it['type']:8s} {it['name']}")
        except Exception as e:
            print(f"  ✗ 從萌龍 fetch 也失敗：{e}")
        return

    label = existing.get('folder', '')
    sname = existing.get('name', f'series_{series_id}')
    print(f"▶ 更新 series「{sname}」({series_id}) [folder={label}]")
    print(f"  從萌龍 fetch...")

    try:
        new_items = client.get_series_full_tree(series_id, label=label)
    except Exception as e:
        print(f"  ✗ fetch 失敗：{e}")
        return
    print(f"  ✓ fetch 到 {len(new_items)} 筆")

    # 移除舊 entry：所有跟這個 series 有關的（series 自己 + 所有 descendants）
    # 從 db.movies 找出所有 parent chain = series_id 或 season_under_series 的
    series_descendant_ids = {series_id}
    for m in db.movies:
        # 從 Season/Episode 反查（它們的 ParentId 邏輯上會在 Series 樹下）
        # 但 db 存的沒有 ParentId，只有 season/episode 編號
        # 所以我們靠「series_name 對應」+「同 folder」
        if m.get('folder') == label and m.get('series_name') == sname:
            series_descendant_ids.add(m['id'])

    before_count = len(db.movies)
    db.movies = [m for m in db.movies if m['id'] not in series_descendant_ids]
    after_remove = len(db.movies)
    print(f"  移除 {before_count - after_remove} 筆舊 entry")

    # 加新 entry
    added_count = 0
    seen = {m['id'] for m in db.movies}
    for it in new_items:
        if it['id'] not in seen:
            db.movies.append(it)
            seen.add(it['id'])
            added_count += 1
    print(f"  加入 {added_count} 筆新 entry")

    db.save()
    print(f"\n✓ DB 已存 ({len(db.movies):,} 筆)")

    # 重新建 index
    print("  重建 in-memory index...")
    db._build_indexes()


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


def cmd_series(args, client: MlongClient, db: MovieDB):
    """抓整個 series（從萌龍拉所有 episodes 一個個抓）。"""
    series_id = args.series_id
    # 嘗試從 DB 拿名字，沒有也沒差
    movie = db.get(series_id) if db.movies else None
    if movie:
        sname = movie.get('name', f'series_{series_id}')
    else:
        sname = f'series_{series_id}'

    print(f"\n▶ 抓 series '{sname}' ({series_id}) 的所有 episodes...")
    try:
        eps = client.get_all_episodes_for_series(series_id)
    except Exception as e:
        print(f"✗ 抓 episodes 失敗：{e}")
        return
    print(f"  找到 {len(eps)} 集")

    out_dir = Path(args.output) if args.output else DEFAULT_DOWNLOAD_DIR
    series_safe = re.sub(r'[\\/:*?"<>|]', '_', sname)[:100]

    success = 0
    for i, ep in enumerate(eps, 1):
        ep_id = ep['Id']
        ep_name = ep.get('Name', f'episode_{ep_id}')
        sn = ep.get('ParentIndexNumber') or 1
        en = ep.get('IndexNumber') or i
        ep_safe = re.sub(r'[\\/:*?"<>|]', '_', ep_name)[:100]
        out_path = out_dir / f"{series_safe} - S{sn:02d}E{en:02d} 「{ep_safe}」.mp4"

        print(f"\n  [{i}/{len(eps)}] S{sn:02d}E{en:02d} {ep_name}")
        url = client.build_original_url(ep_id)
        ok = download_with_ytdlp(url, out_path)
        if ok and out_path.exists():
            print(f"    ✓ {out_path.name} ({out_path.stat().st_size/1024/1024:.1f} MB)")
            success += 1

    print(f"\n=== 完成 {success}/{len(eps)} 集 ===")


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

    def __init__(self, path = None):
        if path is None:
            path = Path(__file__).parent / "config.json"
        self.path = Path(path) if not isinstance(path, Path) else path
        # v1.5.4：第一次啟動時偵測 NAS 路徑優先
        nas = detect_nas_dir()
        default_dl = str(nas / "mlong-dl") if nas else str(DEFAULT_DOWNLOAD_DIR)
        self.data = {
            'download_dir': default_dl,
            'window_size': (900, 600),
            'concurrent_downloads': 1,
            'last_query': '',
            'max_display': 500,  # v1.4.1: listbox 一次顯示幾筆
            'nas_detected': str(nas) if nas else '',  # 記下偵測結果給 GUI 顯示
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

    def _make_filename(self, m):
        """產生輸出檔名（依 type 分類）。
        Movie:    阿凡达 (2009).mp4
        Series:   權力遊戲 (2011).mp4
        Season:   權力遊戲 - S01.mp4
        Episode:  權力遊戲 - S01E03 「名稱」.mp4
        """
        t = m.get('type', 'Movie')
        base = re.sub(r'[\\/:*?"<>|]', '_', m['name'])[:200]
        year = m.get('year', '')

        def _to_int(v, default=1):
            try:
                return int(v)
            except (ValueError, TypeError):
                return default

        if t == 'Series':
            return self.output_dir / f"{base}{' ('+year+')' if year else ''}.mp4"
        elif t == 'Season':
            sn = _to_int(m.get('season'), 1)
            series = m.get('series_name') or base
            series_safe = re.sub(r'[\\/:*?"<>|]', '_', series)[:100]
            return self.output_dir / f"{series_safe} - S{sn:02d}.mp4"
        elif t == 'Episode':
            sn = _to_int(m.get('season'), 1)
            ep = _to_int(m.get('episode'), 1)
            series = m.get('series_name') or base
            series_safe = re.sub(r'[\\/:*?"<>|]', '_', series)[:100]
            ep_safe = re.sub(r'[\\/:*?"<>|]', '_', base)[:100]
            return self.output_dir / f"{series_safe} - S{sn:02d}E{ep:02d} 「{ep_safe}」.mp4"
        else:
            return self.output_dir / f"{base}{' ('+year+')' if year else ''}.mp4"

    def _run(self):
        try:
            ytdlp = find_ytdlp()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.output_path = self._make_filename(self.movie)

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
    print(f"  ✓ 下載目錄: {config.get('download_dir')}")
    if config.get('nas_detected'):
        print(f"  💡 偵測到 NAS: {config.get('nas_detected')}")
    print(f"  ✓ DB: {len(db.movies):,} 筆（{db.path}）")
    print(f"  ✓ 設定檔: {config.path}")
    print()
    download_dir = Path(config.get('download_dir'))

    class App:
        # v1.4.2 修正：QUICK_FILTERS 移到 class-level（之前放在 _build_search_tab 內
        # 的 method scope，其他 method 看不到 → AttributeError → 快捷/搜尋沒反應）
        QUICK_FILTERS = {
            'movie':  lambda m: m.get('type') == 'Movie',
            'tv':     lambda m: m.get('type') in ('Series', 'Season', 'Episode'),
            'anime':  lambda m: m.get('folder') in ('anime_movie', 'jp_anime', 'chinese_anime', 'western_anime'),
            'art':    lambda m: m.get('folder') == 'art',
            'doc':    lambda m: m.get('folder') == 'documentary',
            'concert': lambda m: m.get('folder') == 'concert',
        }

        def __init__(self, root):
            self.root = root
            self.db = db
            self.client = client
            self.config = config
            self.workers = []  # active DownloadWorker
            self.queue_items = []  # 佇列中等待的 movie dicts
            # v1.4.0 效能：filter cache (quick, type) → movies list
            self._filter_cache: dict = {}
            # v1.4.0 效能：debounce search 用的 after id
            self._search_after_id = None

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

            # ============ status bar（先建，因為 tab 建時會用到） ============
            status_frame = tk.Frame(root, relief='sunken', bd=1)
            status_frame.pack(fill='x', side='bottom')
            self.status_label = tk.Label(status_frame, text=f"就緒 · {len(db.movies)} 部電影", anchor='w')
            self.status_label.pack(fill='x', padx=5, pady=2)

            # ============ 主體 Notebook ============
            self.notebook = ttk.Notebook(root)
            self.notebook.pack(fill='both', expand=True, padx=10, pady=5)

            self._build_search_tab()
            self._build_browse_tab()
            self._build_queue_tab()
            self._build_settings_tab()

        # ── Tab 1: 搜尋 ─────────────────────
        def _build_search_tab(self):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text="🔍 搜尋")

            # ── 快捷分類列 ─────────────────────
            quick_frame = ttk.Frame(tab)
            quick_frame.pack(fill='x', padx=5, pady=(8, 2))
            ttk.Label(quick_frame, text="快捷:", font=('TkFixedFont', 10)).pack(side='left')

            # 統計一次（給按鈕 label 用）
            n_total = len(db.movies)
            n_movie = sum(1 for m in db.movies if m.get('type') == 'Movie')
            n_series_ep = sum(1 for m in db.movies if m.get('type') in ('Series', 'Season', 'Episode'))
            n_art = sum(1 for m in db.movies if m.get('folder') == 'art')
            n_anime = sum(1 for m in db.movies if m.get('folder') in ('anime_movie', 'jp_anime', 'chinese_anime', 'western_anime'))
            n_doc = sum(1 for m in db.movies if m.get('folder') == 'documentary')
            n_concert = sum(1 for m in db.movies if m.get('folder') == 'concert')

            def make_btn(text, command):
                b = ttk.Button(quick_frame, text=text, command=command)
                b.pack(side='left', padx=2)
                return b

            self.quick_filter = tk.StringVar(value='all')

            def set_filter(value):
                self.quick_filter.set(value)
                self._apply_search_filter()

            make_btn(f"全部 {n_total:,}", lambda: set_filter('all'))
            make_btn(f"🎬 電影 {n_movie:,}", lambda: set_filter('movie'))
            make_btn(f"📺 劇集 {n_series_ep:,}", lambda: set_filter('tv'))
            make_btn(f"🎞️ 動漫 {n_anime:,}", lambda: set_filter('anime'))
            make_btn(f"🎨 文藝 {n_art:,}", lambda: set_filter('art'))
            make_btn(f"📚 紀錄 {n_doc:,}", lambda: set_filter('doc'))
            make_btn(f"🎤 演唱 {n_concert:,}", lambda: set_filter('concert'))

            # ── 搜尋列 ────────────────────────
            top = ttk.Frame(tab)
            top.pack(fill='x', padx=5, pady=5)
            ttk.Label(top, text="搜尋:").pack(side='left')
            self.query_var = tk.StringVar(value=self.config.get('last_query', ''))
            self.query_var.trace('w', self._on_search_change)
            entry = ttk.Entry(top, textvariable=self.query_var, width=50)
            entry.pack(side='left', padx=5)
            entry.bind('<Return>', lambda e: self.start_download_selected())

            ttk.Label(top, text="類型:").pack(side='left')
            self.type_filter = tk.StringVar(value='全部')
            type_combo = ttk.Combobox(top, textvariable=self.type_filter, state='readonly', width=10)
            type_combo['values'] = ['全部', 'Movie', 'Series', 'Season', 'Episode']
            type_combo.pack(side='left', padx=2)
            type_combo.bind('<<ComboboxSelected>>', lambda e: self._apply_search_filter())

            ttk.Button(top, text="▶ 立即下載", command=self.start_download_selected).pack(side='left', padx=4)
            ttk.Button(top, text="+ 加入佇列", command=self.add_to_queue).pack(side='left', padx=2)

            # ── Listbox ───────────────────────
            mid = ttk.Frame(tab)
            mid.pack(fill='both', expand=True, padx=5, pady=5)
            self.search_listbox = tk.Listbox(mid, font=('TkFixedFont', 11), selectmode='single')
            sb = ttk.Scrollbar(mid, orient='vertical', command=self.search_listbox.yview)
            self.search_listbox.config(yscrollcommand=sb.set)
            self.search_listbox.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.search_listbox.bind('<Double-Button-1>', lambda e: self.start_download_selected())
            self.search_listbox.bind('<Return>', lambda e: self.start_download_selected())
            # v1.4.2：scroll-to-bottom 自動載入
            sb.bind('<MouseWheel>', self._on_search_scroll)
            sb.bind('<Button-4>', self._on_search_scroll)      # Linux scroll up
            sb.bind('<Button-5>', self._on_search_scroll)      # Linux scroll down

            # v1.4.1：分頁按鈕列
            ctrl = ttk.Frame(tab)
            ctrl.pack(fill='x', padx=5, pady=(0, 5))
            ttk.Button(ctrl, text="載入更多",
                       command=self._load_more_search).pack(side='left', padx=2)
            ttk.Button(ctrl, text="顯示全部（會卡）",
                       command=self._show_all_search).pack(side='left', padx=2)
            ttk.Label(ctrl, text="顯示筆數:").pack(side='left', padx=(20, 2))
            self.max_display_var = tk.IntVar(value=self.config.get('max_display', 500))
            ttk.Spinbox(ctrl, from_=100, to=10000, increment=500,
                        textvariable=self.max_display_var, width=8).pack(side='left')
            ttk.Button(ctrl, text="套用", command=self._apply_max_display).pack(side='left', padx=2)

            self.search_results = []
            self._apply_search_filter()

        def _on_search_change(self, *args):
            """debounce：打字停止 200ms 才真的跑搜尋。"""
            if self._search_after_id is not None:
                try:
                    self.root.after_cancel(self._search_after_id)
                except Exception:
                    pass
            self._search_after_id = self.root.after(200, self._apply_search_filter)

        def _apply_search_filter(self, *args):
            """套用：搜尋字串 + 快捷分類 + 類型下拉。
            v1.4.0 效能：
              - 不再只 compute 124k list comprehension 兩次
              - (quick, type) 組合 cache hit → 直接用
              - 搜尋用 inverted index <20ms
              - 用預先算的 m['_display']
            """
            q = self.query_var.get().strip()
            self.config.set('last_query', q)

            quick = self.quick_filter.get()
            t = self.type_filter.get()

            # ── 步驟 1：決定 base list（cache by (quick, type)）──
            cache_key = (quick, t)
            if cache_key in self._filter_cache:
                base = self._filter_cache[cache_key]
            else:
                # 沒 cache：算一次
                if quick == 'all' and t == '全部':
                    base = list(self.db.movies)
                else:
                    base = list(self.db.movies)
                    fn = self.QUICK_FILTERS.get(quick)
                    if fn:
                        base = [m for m in base if fn(m)]
                    if t != '全部':
                        base = [m for m in base if m.get('type') == t]
                self._filter_cache[cache_key] = base

            # ── 步驟 2：套搜尋字串（用 inverted index）──
            if q:
                # search() 已做簡繁轉 + 排序
                # v1.4.2：放寬 limit 到 1000（搜尋 35ms 仍即時，給更多結果）
                candidates = self.db.search(q, limit=1000)
                # 套 quick + type filter
                if quick != 'all':
                    fn = self.QUICK_FILTERS.get(quick)
                    if fn:
                        candidates = [m for m in candidates if fn(m)]
                if t != '全部':
                    candidates = [m for m in candidates if m.get('type') == t]
                base = candidates

            self.search_results = base
            self._refresh_search_list()
            self.status_label.config(text=f"篩選 {quick}/{t} → {len(self.search_results):,} 筆")

        def _safe_int(value, default=0):
            """轉 int，失敗回傳 default。"""
            try:
                return int(value)
            except (ValueError, TypeError):
                return default

        def _format_item(self, m):
            """格式化一個 item 給 listbox 顯示。
            Movie:        [   51465] 🎬 阿凡达 (2009) [179分]
            Series:       [  100000] 📺 權力遊戲 (2011) [8季]
            Season:       [  100100] 📀 權力遊戲 - S01
            Episode:      [  100103] 🎞️ 權力遊戲 S01E03 「凱特」 [52分]
            """
            t = m.get('type', 'Movie')
            year = f" ({m['year']})" if m.get('year') else ''
            ticks = m.get('runtime_ticks', 0)
            runtime = ''
            if ticks:
                total_min = ticks // 600000000
                if total_min:
                    runtime = f" [{total_min}分]"

            # Emoji + Type 標記
            TYPE_EMOJI = {
                'Movie':    '🎬',
                'Series':   '📺',
                'Season':   '📀',
                'Episode':  '🎞️',
            }
            emoji = TYPE_EMOJI.get(t, '❓')

            if t == 'Movie':
                return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"
            elif t == 'Series':
                return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"
            elif t == 'Season':
                sn = self._safe_int(m.get('season'))
                return f"[{m['id']:>10}] {emoji} {m.get('series_name', '?')} - S{sn:02d}"
            elif t == 'Episode':
                sn = self._safe_int(m.get('season'))
                ep = self._safe_int(m.get('episode'))
                series = m.get('series_name') or ''
                return f"[{m['id']:>10}] {emoji} {series} S{sn:02d}E{ep:02d} 「{m['name']}」{runtime}"
            else:
                return f"[{m['id']:>10}] {emoji} {m['name']}{year}{runtime}"

        def _refresh_search_list(self):
            """v1.4.1：只 insert 前 MAX_DISPLAY 筆，剩餘顯示「載入更多」。
            124k 全 insert = 216ms；只插 500 = 8ms。
            """
            max_disp = self.config.get('max_display', 500)
            total = len(self.search_results)
            display_items = self.search_results[:max_disp]

            self.search_listbox.delete(0, 'end')
            for m in display_items:
                display = m.get('_display') or MovieDB._format_display(m)
                self.search_listbox.insert('end', display)

            if total > max_disp:
                more = total - max_disp
                self.status_label.config(
                    text=f"顯示 {max_disp:,} / {total:,} 筆 "
                         f"（還有 {more:,} 筆沒顯示，按 [載入更多]）")
            else:
                self.status_label.config(text=f"顯示 {total:,} 筆")

        def _apply_max_display(self):
            """套用 Spinbox 的 max_display 值，存到 config 並重新整理。"""
            new_val = self.max_display_var.get()
            self.config.set('max_display', new_val)
            self.config.save()
            self._refresh_search_list()
            self.status_label.config(text=f"max_display → {new_val}")

        def _on_search_scroll(self, event=None):
            """scrollwheel 事件：判斷是否捲到底，到底就自動載入更多。"""
            # 用 root.after 延遲執行（避免在 scroll 事件中改 listbox 衝突）
            self.root.after(50, self._maybe_load_more_search)

        def _maybe_load_more_search(self):
            """檢查 listbox 是否捲到底，是的話載入下一批。"""
            try:
                # yview 回傳 (top, bottom) 兩個 0-1 的數字
                top, bottom = self.search_listbox.yview()
                # bottom >= 0.95 視為到底
                if bottom >= 0.95:
                    self._load_more_search(silent=True)
            except Exception:
                pass

        def _load_more_search(self, silent=False):
            """按「載入更多」：append 額外 MAX_DISPLAY 筆到 listbox。
            silent=True：scroll-to-bottom 自動呼叫，仍更新 status 顯示 X / Y。
            """
            max_disp = self.config.get('max_display', 500)
            current = self.search_listbox.size()
            next_end = current + max_disp
            new_items = self.search_results[current:next_end]

            for m in new_items:
                display = m.get('_display') or MovieDB._format_display(m)
                self.search_listbox.insert('end', display)

            total = len(self.search_results)
            shown = self.search_listbox.size()
            if shown < total:
                self.status_label.config(
                    text=f"顯示 {shown:,} / {total:,} 筆 "
                         f"（還有 {total-shown:,} 筆，按 [載入更多] 或捲到底自動載入）")
            else:
                self.status_label.config(text=f"顯示全部 {total:,} 筆")

        def _show_all_search(self):
            """顯示全部（會卡，警告後執行）。"""
            total = len(self.search_results)
            if total > 5000:
                from tkinter import messagebox
                if not messagebox.askyesno(
                    "確認", f"插入 {total:,} 筆會卡 {total*1.7/1000:.1f} 秒。\n繼續？"):
                    return
            self.search_listbox.delete(0, 'end')
            for m in self.search_results:
                display = m.get('_display') or MovieDB._format_display(m)
                self.search_listbox.insert('end', display)
            self.status_label.config(text=f"顯示全部 {total:,} 筆")

        def on_search(self, *args):
            """舊版 callback — redirect 到新版。"""
            self._apply_search_filter()

        def _get_selected_movie(self):
            sel = self.search_listbox.curselection()
            if not sel:
                messagebox.showinfo("提示", "請先選一部電影")
                return None
            line = self.search_listbox.get(sel[0])
            m = re.match(r'\[\s*(\d+)\]', line)
            if not m:
                return None
            item_id = m.group(1)
            movie = self.db.get(item_id) or {'id': item_id, 'name': f'movie_{item_id}', 'year': ''}
            return movie

        def start_download_selected(self):
            movie = self._get_selected_movie()
            if not movie:
                return
            if movie.get('type') == 'Series':
                # 劇集：跳到下載整個 series 的流程
                self.download_series(movie)
            else:
                self._start_download(movie)

        def add_to_queue(self):
            movie = self._get_selected_movie()
            if not movie:
                return
            if movie.get('type') == 'Series':
                self.enqueue_series(movie)
            else:
                self.queue_items.append(movie)
                self._refresh_queue_list()
                self.status_label.config(text=f"已加入佇列：{movie['name']}")

        def download_series(self, movie):
            """抓整個 series：fetch 所有 episodes → 加進佇列 → 自動開始。"""
            sid = movie['id']
            sname = movie['name']
            from tkinter import messagebox
            if not messagebox.askyesno(
                "確認下載整個 Series",
                f"「{sname}」\n\n會自動抓所有 episodes 加進佇列後開始下載。\n\n繼續？"
            ):
                return
            self.enqueue_series(movie, start=True)

        def enqueue_series(self, movie, start=False):
            """從萌龍抓所有 episodes，轉成 movie dict 加進佇列。"""
            sid = movie['id']
            sname = movie['name']
            self.status_label.config(text=f"抓 {sname} 的 episodes 中...")
            self.root.update_idletasks()
            try:
                eps = self.client.get_all_episodes_for_series(sid)
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror("抓 episodes 失敗", str(e))
                self.status_label.config(text=f"✗ 抓 episodes 失敗")
                return
            if not eps:
                from tkinter import messagebox
                messagebox.showinfo("沒有 episodes", "這個 series 沒有任何 episodes")
                return

            # 轉成 movie dict 格式（_display 等）
            added = 0
            for ep in eps:
                m = {
                    'id': ep['Id'],
                    'name': ep['Name'],
                    'year': '',
                    'folder': movie.get('folder', ''),
                    'type': 'Episode',
                    'season': ep.get('ParentIndexNumber'),
                    'episode': ep.get('IndexNumber'),
                    'series_name': ep.get('SeriesName', sname),
                    'runtime_ticks': ep.get('RunTimeTicks', 0),
                }
                m['_display'] = MovieDB._format_display(m)
                self.queue_items.append(m)
                added += 1

            self._refresh_queue_list()
            self.status_label.config(
                text=f"✓ {sname}: 加入 {added} 集到佇列")
            if start:
                self.process_queue()

        # ── Tab 2: 瀏覽全部 ────────────────
        def _build_browse_tab(self):
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text="📚 瀏覽全部")

            top = ttk.Frame(tab)
            top.pack(fill='x', padx=5, pady=5)
            ttk.Label(top, text="Folder:").pack(side='left')
            self.browse_folder = tk.StringVar(value='全部')
            folder_combo = ttk.Combobox(top, textvariable=self.browse_folder, state='readonly', width=15)
            folders = ['全部'] + sorted({m['folder'] for m in db.movies if m.get('folder')})
            folder_combo['values'] = folders
            folder_combo.pack(side='left', padx=5)
            folder_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_browse_list())

            ttk.Label(top, text="類型:").pack(side='left', padx=(10, 0))
            self.browse_type = tk.StringVar(value='全部')
            type_combo = ttk.Combobox(top, textvariable=self.browse_type, state='readonly', width=10)
            type_combo['values'] = ['全部', 'Movie', 'Series', 'Season', 'Episode']
            type_combo.pack(side='left', padx=5)
            type_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_browse_list())

            ttk.Label(top, text="排序:").pack(side='left', padx=(10, 0))
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
            ttk.Button(bottom, text="載入更多", command=self._load_more_browse).pack(side='left', padx=2)
            self.browse_count_label = ttk.Label(bottom, text="")
            self.browse_count_label.pack(side='right', padx=5)

            self._refresh_browse_list()

        def _refresh_browse_list(self):
            movies = list(self.db.movies)
            folder = self.browse_folder.get()
            if folder != '全部':
                movies = [m for m in movies if m.get('folder') == folder]

            t = self.browse_type.get()
            if t != '全部':
                movies = [m for m in movies if m.get('type') == t]

            sort = self.browse_sort.get()
            if sort == '名稱':
                movies.sort(key=lambda m: m['name'])
            elif sort == '年份（新→舊）':
                movies.sort(key=lambda m: -int(m.get('year') or 0))
            elif sort == '年份（舊→新）':
                movies.sort(key=lambda m: int(m.get('year') or 0))
            elif sort == '時長（長→短）':
                movies.sort(key=lambda m: -m.get('runtime_ticks', 0))

            # v1.4.1：只 insert 前 MAX_DISPLAY 筆
            max_disp = self.config.get('max_display', 500)
            display_items = movies[:max_disp]
            self.browse_listbox.delete(0, 'end')
            for m in display_items:
                line = m.get('_display') or MovieDB._format_display(m)
                folder_tag = f" [{m['folder']}]" if m.get('folder') else ''
                self.browse_listbox.insert('end', line + folder_tag)

            total = len(movies)
            if total > max_disp:
                self.browse_count_label.config(
                    text=f"顯示 {max_disp:,} / {total:,} 部（按 [載入更多]）")
            else:
                self.browse_count_label.config(text=f"顯示 {total:,} / {len(self.db.movies):,} 部")
            self.browse_movies = movies

        def _load_more_browse(self):
            """Browse tab 載入更多。"""
            max_disp = self.config.get('max_display', 500)
            current = self.browse_listbox.size()
            next_end = current + max_disp
            new_items = self.browse_movies[current:next_end]
            for m in new_items:
                line = m.get('_display') or MovieDB._format_display(m)
                folder_tag = f" [{m['folder']}]" if m.get('folder') else ''
                self.browse_listbox.insert('end', line + folder_tag)
            total = len(self.browse_movies)
            shown = self.browse_listbox.size()
            if shown < total:
                self.browse_count_label.config(
                    text=f"顯示 {shown:,} / {total:,} 部（按 [載入更多]）")
            else:
                self.browse_count_label.config(text=f"顯示 {total:,} / {len(self.db.movies):,} 部")

        def _get_browse_selected(self):
            sels = self.browse_listbox.curselection()
            if not sels:
                return []
            return [self.browse_movies[i] for i in sels]

        def browse_double_click(self):
            sel = self._get_browse_selected()
            if not sel:
                return
            m = sel[0]
            if m.get('type') == 'Series':
                self.download_series(m)
            else:
                self._start_download(m)

        def browse_download_selected(self):
            sel = self._get_browse_selected()
            if not sel:
                messagebox.showinfo("提示", "請先選電影")
                return
            for m in sel:
                if m.get('type') == 'Series':
                    self.enqueue_series(m, start=False)
                else:
                    self._start_download(m)

        def browse_add_all_to_queue(self):
            # v1.5.0：對 Series 自動展開成 episodes
            series_count = 0
            for m in self.browse_movies:
                if m.get('type') == 'Series':
                    try:
                        eps = self.client.get_all_episodes_for_series(m['id'])
                        for ep in eps:
                            ep_m = {
                                'id': ep['Id'],
                                'name': ep['Name'],
                                'year': '',
                                'folder': m.get('folder', ''),
                                'type': 'Episode',
                                'season': ep.get('ParentIndexNumber'),
                                'episode': ep.get('IndexNumber'),
                                'series_name': ep.get('SeriesName', m['name']),
                                'runtime_ticks': ep.get('RunTimeTicks', 0),
                            }
                            ep_m['_display'] = MovieDB._format_display(ep_m)
                            self.queue_items.append(ep_m)
                        series_count += 1
                    except Exception as e:
                        print(f"展開 {m['name']} 失敗: {e}")
                else:
                    self.queue_items.append(m)
            self._refresh_queue_list()
            n = len(self.queue_items)
            msg = f"已加入 {n} 項到佇列"
            if series_count:
                msg += f"（展開 {series_count} 個 series）"
            self.status_label.config(text=msg)

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

            # v1.5.4：NAS 偵測狀態
            nas_detected = self.config.get('nas_detected', '')
            if nas_detected:
                ttk.Label(tab, text=f"💡 偵測到 NAS 路徑：{nas_detected}",
                          foreground='green').grid(row=row, column=0, columnspan=3,
                                                  sticky='w', padx=5, pady=2)
                row += 1

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
    p_update.add_argument('--series', dest='series_id', metavar='SERIES_ID',
                          help='只更新某個 series（不重抓全部，秒完成）')
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

    # series - 抓整個 series
    p_series = sub.add_parser('series', help='抓整個 series（自動展開所有 episodes）')
    p_series.add_argument('series_id', help='Series 的 Item ID')
    p_series.set_defaults(func=cmd_series)

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