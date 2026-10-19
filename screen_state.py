"""What is on the game screen right now? Template matching against crops of real screenshots
(2026-10-18), OCR only as a helper. Used by self-recovery; the hunting loop keeps its own checks."""
import pathlib, re
import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent / 'assets' / 'screens'
# name: (box on the 1024x768 game frame, threshold) - boxes cut from real screenshots.
TEMPLATES = {
    'yes_no':       ((420, 306, 604, 560), .80),   # 是/否 panel: surf prompt and nurse "rest?" prompt
    'social_menu':  ((772, 580, 864, 734), .80),   # book icon menu: 好友/公会/更换频道/...
    'pvp_menu':     ((798, 608, 891, 732), .80),   # pokeball icon menu: 匹配赛报名/锦标赛报名/...
    'tournament':   ((164, 170, 860, 198), .80),   # 锦标赛报名 window title bar
    'channel_list': ((440, 350, 584, 462), .60),   # opened channel dropdown; real frames .68-1.0, others <=.29
    'pc_hall':      ((300, 180, 740, 350), .55),   # inside the Pokemon Center (players may stand in front)
}
LABELS = {'yes_no': '是/否选择框', 'dialog': '对话框', 'battle_menu': '战斗菜单', 'battle': '战斗中',
          'channel_window': '换频道窗口', 'channel_list': '频道下拉列表', 'social_menu': '书本菜单',
          'pvp_menu': 'PvP菜单', 'tournament': '锦标赛报名窗口', 'pc_hall': '宝可梦中心里',
          'spot': '刷怪点地图', 'outdoor': '室外地图（不在刷怪点）', 'loading': '黑屏/加载中', 'unknown': '认不出的画面'}
_cache = {}


def _template(name):
    if name not in _cache:
        try:
            _cache[name] = np.asarray(Image.open(HERE / f'{name}.png').convert('L'), dtype=float)
        except OSError:
            _cache[name] = None
    return _cache[name]


def score(im, name, shift=4):
    ref = _template(name)
    if ref is None:
        return -1.0
    (x0, y0, x1, y1), _ = TEMPLATES[name]
    g = np.asarray(im.convert('L'), dtype=float)
    b = ref - ref.mean(); nb = np.sqrt((b * b).sum()) + 1e-9; best = -1.0
    for dy in range(-shift, shift + 1, 2):
        for dx in range(-shift, shift + 1, 2):
            if y0 + dy < 0 or x0 + dx < 0: continue
            a = g[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
            if a.shape != ref.shape: continue
            a = a - a.mean(); best = max(best, float((a * b).sum() / (np.sqrt((a * a).sum()) * nb + 1e-9)))
    return best


SPOT_WATER = .38   # real frames: hunting spot .41-.47, shore .26-.33, street .04, inside the centre .05


def water_ratio(im):
    a = np.asarray(im.convert('RGB').crop((150, 120, 900, 700)), dtype=int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((b > r + 50) & (b > g + 20)).mean())


def matches(im, name):
    return score(im, name) >= TEMPLATES[name][1]


def classify(im, data, *, on_spot=False, menu=False, channel_window=0.0, dialog_white=0.0):
    """Return (kind, details). Order: most specific UI first, map last."""
    text = data.get('text', '') if isinstance(data, dict) else ''
    scores = {n: round(score(im, n), 2) for n in TEMPLATES}
    hit = lambda n: scores[n] >= TEMPLATES[n][1]
    if hit('yes_no'): return 'yes_no', scores
    if menu: return 'battle_menu', scores
    if re.search(r'Lv\.?\s*\d+|野生怪群出现|派出了|使出了|成功逃脱', text): return 'battle', scores
    if channel_window >= .8: return 'channel_window', scores
    if hit('channel_list'): return 'channel_list', scores
    if hit('tournament') or '锦标赛报名' in text: return 'tournament', scores
    if hit('social_menu'): return 'social_menu', scores
    if hit('pvp_menu'): return 'pvp_menu', scores
    if dialog_white > .2: return 'dialog', scores
    if hit('pc_hall'): return 'pc_hall', scores
    gray = np.asarray(im.convert('L'), dtype=float)
    if gray.mean() < 35: return 'loading', scores
    if on_spot: return 'spot', scores
    if re.search(r'Ch\.?\s*\d|\$\s*[\d,]{3,}', text): return 'outdoor', scores
    return 'unknown', scores
