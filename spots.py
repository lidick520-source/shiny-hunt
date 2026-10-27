"""白嫖闪 spots: one entry per hunting spot. Add spots one at a time, each verified in the real game.

route: what to hold after the nurse heals you, as (key, min_seconds, max_seconds, label).
surf:  after the route, face the water and press space twice (prompt + 是) to surf back to the spot.
keyword / min_count: OCR evidence that the battle is the target horde (else stop, never escape blindly).
spot_water: share of water on screen that proves "back at the spot" (None = not measured yet; then only
            the map reference learned at start is used).
verified: True only after a real-game long run (see docs/验收记录.md).
"""
import re

SPOTS = [
    {
        'key': 'fanba', 'name': '帆巴市 · 轻飘飘', 'region': '合众', 'city': '帆巴市', 'targets': ['轻飘飘'], 'en': 'FRILLISH',
        # Vision sometimes reads the last character as 期/漂; 3 of the 5 names are enough evidence.
        'keyword': r'轻飘[飘期漂]', 'min_count': 3,
        # Verified 2026-10-18: the last D must be ~2.5 s (1.8 s stops 2-3 tiles short), plus a short nudge.
        'route': [('S', 2.0, 2.5, '走出医院'), ('D', 1.03, 1.08, '沿街向右'), ('W', .26, .30, '上一格'),
                  ('D', 2.5, 2.6, '向右走到水边'), ('D', .5, .7, '再补按D贴到岸边（已在岸边则不动）')],
        'surf': True, 'spot_water': .38, 'verified': True,
        'note': '2026-10-18 实机 227 轮、去医院 37 次通过',
    },
    {
        'key': 'lilycove', 'name': '琉璃市 · 鲤鱼王', 'region': '丰缘', 'city': '琉璃市', 'targets': ['鲤鱼王'], 'en': 'MAGIKARP',
        # 2026-10-20 real run: Vision read 3 of 5 names as traditional 鯉鱼王 -> count both forms.
        'keyword': r'[鲤鯉][鱼魚]', 'min_count': 3,
        # 2026-10-20 first real trip: the PokeLoop times ended in the door fade (step 9 frame black) and the
        # character stopped on land ~2 tiles under the door, so 5 said 不会有任何效果. Now: wait out the fade,
        # then over-hold S - the water straight below blocks the walk exactly at the shore - plus a short nudge.
        'route': [('S', 1.5, 1.8, '走出医院'), ('door', 3.0, 3.0, '等出门黑屏结束'),
                  ('S', 2.0, 2.2, '向下走到岸边'), ('S', .5, .7, '再补按S贴到岸边（已在岸边则不动）')],
        'surf': True, 'spot_water': None, 'verified': True,
        'note': '2026-10-23~27 实机长跑通过（回院路线、换频道、繁体鯉都已验证）',
    },
]

DEFAULT_SPOT = 'fanba'


def get(key):
    return next((s for s in SPOTS if s['key'] == key), next(s for s in SPOTS if s['key'] == DEFAULT_SPOT))


def target_count(text, spot):
    return len(re.findall(spot['keyword'], re.sub(r'\s+', '', text or '')))


def summary():
    return [{'key': s['key'], 'name': s['name'], 'en': s.get('en', ''), 'verified': bool(s['verified'])} for s in SPOTS]
