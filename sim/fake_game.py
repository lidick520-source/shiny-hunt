"""A fake PokeMMO built from real screenshots taken on 2026-10-18.

It reacts to the assistant's key presses and clicks the way the game did in the
real runs, so the assistant's own code can be driven end to end without the game.
"""
import random, threading, time, pathlib
from PIL import Image

FRAMES = pathlib.Path(__file__).resolve().parent / 'frames'
ESCAPE_BUTTON = (355, 537, 555, 582)          # real 逃跑 button, window coordinates
OTHER_BUTTONS = {'战斗': (145, 483, 343, 527), '背包': (355, 483, 555, 527), '宝可梦': (145, 537, 343, 582)}
K4, K5, SPACE, S, D, W, A = 0x34, 0x35, 0x20, 0x53, 0x44, 0x57, 0x41
BATTLES = [['battle_a0_intro', 'battle_a1_sendout', 'battle_a2_ability', 'battle_a3_menu', 'battle_a4_smoke'],
           ['battle_b0_intro', 'battle_b1_sendout', 'battle_b2_ability', 'battle_b3_menu', None],
           ['battle_c0_intro', 'battle_c1_sendout', 'battle_c2_ability', 'battle_c3_menu', 'battle_c4_smoke']]
MAPS = ['map_a_clean', 'map_b_toast', 'map_c_players']


class FakeGame:
    def __init__(self, faults=(), seed=1):
        self.rand = random.Random(seed)
        self.faults = set(faults)
        self.lock = threading.RLock()
        self.cache = {p.stem: Image.open(p).convert('RGB') for p in FRAMES.glob('*.png')}
        self.state = 'map'; self.timeline = []; self.frame = 'map_a_clean'
        self.held = {}; self.log = []; self.problems = []; self.clicks = []
        self.battle = None; self.escapes = 0; self.heals = 0; self.surfs = 0
        self.focus_depth = 0; self.dropped_space = False; self.dropped_surf = False; self.channel = 2; self.switches = 0
        self.front = True; self.steals = 0; self.next_steal = time.monotonic() + 20; self.lost_inputs = 0; self.popped = set()

    # ---- what the assistant sees
    def foreground(self):
        return 77 if self.front else 999

    def activate(self):
        if not self.front: self.note('游戏被切回前台')
        self.front = True

    def capture(self):
        with self.lock:
            now = time.monotonic()
            if 'focus_steal' in self.faults and now >= self.next_steal and self.steals < 4:
                self.front = False; self.steals += 1; self.next_steal = now + 25
                self.note(f'（故障注入）你点了别的程序，游戏到了后台（第{self.steals}次）')
            while self.timeline and self.timeline[0][0] <= now:
                _, state, frame = self.timeline.pop(0)
                self.state, self.frame = state, frame
            im = self.cache[self.frame].copy(); im.info['sim'] = self.frame + '.png'
            return im

    def _schedule(self, steps):
        t = time.monotonic(); self.timeline = []
        for delay, state, frame in steps:
            t += delay; self.timeline.append((t, state, frame))

    def _set(self, state, frame):
        self.timeline = []; self.state = state; self.frame = frame

    def note(self, text):
        self.log.append(f'{time.strftime("%H:%M:%S")} [游戏] {text}')

    def problem(self, text):
        self.problems.append(text); self.note('问题：' + text)

    # ---- input from the assistant (Swift native driver operations)
    def native(self, op, **v):
        with self.lock:
            self.capture()
            if op in ('key', 'repeat', 'mouse', 'move') and not self.front and self.focus_depth <= 0:
                # Real macOS: input goes to whatever app is in front, the game never sees it.
                self.lost_inputs += 1
                if op == 'key' and v.get('down') is False: self.held.pop(v['key'], None)
                return
            if op == 'focusBegin': self.focus_depth += 1
            elif op == 'focusEnd': self.focus_depth -= 1
            elif op == 'key': self._key(v['key'], v['down'])
            elif op == 'repeat':
                if v['key'] not in self.held: self.problem('重复了一个没按住的键')
            elif op == 'focusClick': self._click(v['x'], v['y'])
            elif op in ('mouse', 'move'):
                # Plain mouse events only reach PokeMMO while it is in front (focusBegin).
                if op == 'mouse' and v.get('down'): self._click(v['x'], v['y'])
            elif op == 'release': self.held.clear()

    def _key(self, key, down):
        if down:
            self.held[key] = time.monotonic(); return
        start = self.held.pop(key, None)
        if start is None: return
        held = time.monotonic() - start
        s = self.state
        if key == K5:
            if s == 'map':
                self.battle = self.rand.choice(BATTLES)
                self.note('按5 → 进入轻飘飘群战斗')
                self._schedule([(0.0, 'battle', self.battle[0]), (0.8, 'battle', self.battle[1]),
                                (1.0, 'battle', self.battle[2]), (1.0, 'menu', self.battle[3])])
            else:
                self.problem(f'在「{s}」状态按了5')
        elif key == K4:
            if s in ('map', 'street1', 'street2', 'street3', 'shore', 'short'):
                self.note('按4 → 瞬移回宝可梦中心')
                self._schedule([(0.0, 'teleport', 'hosp_01_leaving_map'), (1.0, 'teleport', 'hosp_02_arrive'),
                                (1.2, 'pc_idle', 'hosp_03_arrived')])
            else:
                self.problem(f'在「{s}」状态按了4')
        elif key == SPACE:
            self._space(s)
        elif key in (S, D, W, A):
            self._walk(s, key, held)

    def _space(self, s):
        if s == 'pc_idle':
            self.note('空格 → 护士：要让精灵休息一下吗？'); self._set('pc_ask', 'hosp_04_ask_heal')
        elif s == 'pc_ask':
            self.note('空格 → 确认治疗'); self.heals += 1
            self._schedule([(0.0, 'pc_healing', 'hosp_05_healing'), (2.5, 'pc_healed', 'hosp_06_healed')])
        elif s == 'pc_healed':
            self.note('空格 → 欢迎下次再来！'); self._set('pc_bye', 'hosp_07_bye')
        elif s == 'pc_bye':
            if 'drop_space_after_heal' in self.faults and not self.dropped_space:
                self.dropped_space = True; self.note('（故障注入）这下空格游戏没收到，气泡还在'); return
            self.dropped_space = False
            self.note('空格 → 对话结束'); self._set('pc_done', 'hosp_03_arrived')
        elif s == 'pc_done':
            self.problem('对话已结束后又按空格，又和护士搭上话了'); self._set('pc_ask', 'hosp_04_ask_heal')
        elif s == 'shore':
            self.note('空格 → 要使用冲浪吗？'); self._set('surf_ask', 'surf_prompt')
        elif s == 'surf_ask':
            if 'drop_surf_space' in self.faults and not self.dropped_surf:
                self.dropped_surf = True; self.note('（故障注入，20:05 实测）确认冲浪的空格游戏没收到，提示还开着'); return
            self.dropped_surf = False
            self.surfs += 1
            if 'interruptions' in self.faults and 'off_route' not in self.popped and self.heals == 1:
                self.popped.add('off_route'); self.note('（故障注入）这次路线没走对：停在了街上，不在刷怪点')
                self._schedule([(0.0, 'surfing', 'walk_16_near_shore'), (1.0, 'street2', 'walk_13_street2')]); return
            self.note('空格 → 冲浪下水，回到刷怪点')
            self._schedule([(0.0, 'surfing', 'walk_16_near_shore'), (1.0, 'map', self.rand.choice(MAPS))])
        elif s in ('pc_healing', 'teleport', 'surfing'):
            self.note(f'空格（{s} 中，无效）')
        else:
            self.problem(f'在「{s}」状态按了空格')

    def _walk(self, s, key, held):
        name = {S: 'S', D: 'D', W: 'W', A: 'A'}[key]
        if s in ('pc_bye', 'pc_ask', 'pc_healed', 'pc_healing'):
            self.note(f'长按{name} {held:.2f}秒，但对话框开着，人物没动'); return
        if s == 'pc_done' and key == S and held > 1.5:
            self.note(f'长按S {held:.2f}秒 → 走出医院'); self._set('street1', 'walk_11_street')
        elif s == 'street1' and key == D:
            self.note(f'长按D {held:.2f}秒 → 沿街向右'); self._set('street2', 'walk_13_street2')
        elif s == 'street2' and key == W:
            self.note(f'长按W {held:.2f}秒 → 上一格'); self._set('street3', 'walk_13_street2')
        elif s == 'shore' and key == D:
            self.note(f'长按D {held:.2f}秒 → 已在岸边，水挡住了，没动')
        elif s == 'short' and key == D:
            self.note(f'长按D {held:.2f}秒 → 补走到岸边'); self._set('shore', 'walk_16_near_shore')
        elif s == 'street3' and key == D:
            short = held < 2.4 or 'short_walk' in self.faults
            self.note(f'长按D {held:.2f}秒 → ' + ('没走到岸边，差两三格' if short else '停在岸边'))
            self._set('short' if short else 'shore', 'walk_20_near_shore_b' if short else 'walk_16_near_shore')
        else:
            self.problem(f'在「{s}」状态长按了{name}')

    @staticmethod
    def _inside(x, y, box):
        x0, y0, x1, y1 = box; return x0 <= x <= x1 and y0 <= y <= y1

    def _channel_click(self, s, x, y):
        # Real UI geometry from the 2026-10-18 18:14-18:19 screenshots.
        if s == 'tournament':
            if self._inside(x, y, (840, 176, 858, 194)):
                self.note('点 × → 锦标赛窗口关闭'); self._set('map', 'map_a_clean'); return True
            self.problem(f'锦标赛窗口里点错了 ({x:.0f},{y:.0f})'); return True
        if s == 'map' and self._inside(x, y, (806, 736, 830, 760)):
            self.note('点书本图标 → 好友/公会/更换频道 菜单'); self._set('ch_menu', 'ch_menu_social'); return True
        if s == 'map' and self._inside(x, y, (833, 736, 857, 760)):
            self.problem('点到了 PvP 图标（精灵球）'); self._set('pvp', 'ch_menu_pvp'); return True
        if s == 'ch_menu':
            if self._inside(x, y, (772, 645, 865, 672)):
                self.note('点「更换频道」→ 换频道窗口'); self._set('ch_window', 'ch_window'); return True
            self.problem(f'菜单里点错了项 ({x:.0f},{y:.0f})'); return True
        if s in ('ch_window', 'ch_window_sel') and self._inside(x, y, (438, 290, 586, 315)):
            self.note('点下拉框 → 展开频道列表'); self._set('ch_list', 'ch_list'); return True
        if s == 'ch_list':
            row = round((y - 338) / 27) + 1
            want = 2 if 'blind_list' in self.faults else 3   # blind: only the HUD (Ch.1 map frames) is readable
            if self._inside(x, y, (440, 325, 585, 460)) and row == want:
                self.note(f'在列表里选了频道{want}'); self._set('ch_window_sel', 'ch_window' if want == 2 else 'ch_window_sel3'); return True
            self.problem(f'列表里点了第 {row} 行 ({x:.0f},{y:.0f})，不是下一个频道{want}'); return True
        if s == 'ch_window_sel' and self._inside(x, y, (438, 357, 586, 413)):
            want = 2 if 'blind_list' in self.faults else 3
            self.note(f'点「变更」→ 切到频道{want}'); self.switches += 1; self.channel = want
            self._schedule([(0.0, 'loading', 'walk_09_door'), (1.5, 'map', f'map_after_ch{want}')]); return True
        if s in ('ch_window', 'ch_window_sel', 'ch_list', 'pvp'):
            self.problem(f'换频道窗口里点了不该点的地方 ({x:.0f},{y:.0f})'); return True
        return False

    def _click(self, x, y):
        s = self.state
        self.clicks.append((round(x), round(y), s))
        if self._channel_click(s, x, y): return
        if s != 'menu':
            self.problem(f'在「{s}」状态点击了 ({x:.0f},{y:.0f})'); return
        x0, y0, x1, y1 = ESCAPE_BUTTON
        if x0 <= x <= x1 and y0 <= y <= y1:
            self.escapes += 1; smoke = self.battle[4]
            self.note(f'点击 ({x:.0f},{y:.0f}) 在逃跑按钮内 → 逃跑')
            if 'missed_escape_msg' in self.faults:   # bubble too short for OCR, other players on the map
                steps = ([(0.0, 'escaping', smoke)] if smoke else []) + [(0.6, 'escaping', 'escape_success'), (0.05, 'map', 'map_c_players' if self.escapes % 2 else 'map_b_toast')]
            elif 'interruptions' in self.faults and self.escapes == 3 and 'tournament' not in self.popped:
                self.popped.add('tournament'); self.note('（故障注入）逃跑后弹出了锦标赛报名窗口')
                steps = [(0.6, 'escaping', 'escape_success'), (1.2, 'tournament', 'ch_tournament')]
            elif 'interruptions' in self.faults and self.escapes == 5 and 'yes_no' not in self.popped:
                self.popped.add('yes_no'); self.note('（故障注入）逃跑后冒出了冲浪是/否提示')
                steps = [(0.6, 'escaping', 'escape_success'), (1.2, 'surf_ask', 'surf_prompt')]
            else:
                steps = ([(0.0, 'escaping', smoke)] if smoke else []) + [(0.6, 'escaping', 'escape_success'), (1.2, 'map', self.rand.choice(MAPS))]
            self._schedule(steps)
        else:
            hit = next((n for n, (a, b, c, d) in OTHER_BUTTONS.items() if a <= x <= c and b <= y <= d), '空白处')
            self.problem(f'点击 ({x:.0f},{y:.0f}) 落在「{hit}」，不是逃跑')
