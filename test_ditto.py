"""抓百变 (2026-11-14, 城都47号道路): 点到为止 (move 1) -> 蘑菇孢子 (move 3) -> key 3 (精灵球)."""
import unittest
from unittest.mock import Mock
import app
import test_safety

ESCAPE = (431, 538, 48, 26)   # escape_region() of 逃跑 on sim/frames/newui_battle_driftveil.png


def line(text, x, y, w=60, h=16):
    return {'text': text, 'x': x, 'y': y, 'w': w, 'h': h}


class DittoTests(unittest.TestCase):
    def host(self, on=True, mode='walk'):
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.task_mode = mode; a.ditto_catch = on
        return a

    def test_menu_grid_matches_the_real_screenshot(self):
        r = app.battle_menu_rects(ESCAPE)
        centre = {k: (x + w / 2, y + h / 2) for k, (x, y, w, h) in r.items()}
        # buttons on the screenshot: 战斗 143-345/483-528, 宝可梦 143-345/537-582, 逃跑 350-555/537-582
        self.assertTrue(143 < centre[1][0] < 345 and 483 < centre[1][1] < 528)
        self.assertTrue(143 < centre[3][0] < 345 and 537 < centre[3][1] < 582)
        self.assertTrue(350 < centre[4][0] < 555 and 537 < centre[4][1] < 582)

    @staticmethod
    def frame(*lines):
        return {'text': ''.join(l['text'] for l in lines), 'lines': list(lines)}

    def test_only_a_ditto_in_walk_mode_with_the_switch_on(self):
        ditto = self.frame(line("百变怪'普 Lv. 35", 178, 136, 115))
        self.assertTrue(self.host().ditto_wanted(ditto))
        self.assertTrue(self.host().ditto_wanted({}, self.frame(line('百变任”普LV.52', 178, 136, 115))))   # OCR misread 2026-11-13
        self.assertTrue(self.host().ditto_wanted(self.frame(line("百变怪'普", 178, 136), line('Lv. 35', 245, 137))))   # split plate
        self.assertFalse(self.host().ditto_wanted(self.frame(line('吼爆弹·普Lv.51', 178, 136, 115))))
        self.assertFalse(self.host(on=False).ditto_wanted(ditto))
        self.assertFalse(self.host(mode='fixed').ditto_wanted(ditto))

    def test_a_horde_and_the_chat_are_never_a_ditto(self):
        # 2026-11-14 16:29 47号道路: three 拉达 with the chat open - it was taken for a Ditto.
        horde = [line("拉达'普 Lv. 31", 147, 90, 110), line("拉达'普 Lv. 32", 407, 90, 110), line("拉达'普 Lv. 31", 667, 90, 110)]
        self.assertFalse(self.host().ditto_wanted(self.frame(*horde, line('收百变怪 私聊', 40, 600, 120))))
        self.assertFalse(self.host().ditto_wanted(self.frame(*horde[:2], line("百变怪'普 Lv. 30", 667, 143, 115))))   # Ditto inside a horde
        self.assertFalse(self.host().ditto_wanted(self.frame(line("大葱鸭'普飞 Lv. 34", 178, 136, 115), line('百变怪', 40, 600))))
        self.assertFalse(self.host().ditto_wanted(self.frame(line("百变怪'普 Lv. 35", 640, 337, 150))))   # our own plate row

    def test_move_pp_reads_the_button(self):
        rect = app.battle_menu_rects(ESCAPE)[3]
        self.assertEqual(app.move_pp([line('PP 0/24', 220, 556)], rect), 0)
        self.assertEqual(app.move_pp([line('12/24', 220, 556)], rect), 12)
        self.assertIsNone(app.move_pp([line('234/234', 700, 370)], rect))   # HP of our lead, not a button

    def move_host(self, menu_lines):
        a = self.host()
        a.check_verification = Mock(return_value={'text': '', 'lines': menu_lines})
        return a

    def test_move_is_clicked_by_name(self):
        a = self.move_host([line('点到为止', 215, 490), line('素描', 425, 490), line('蘑菇孢子', 215, 544), line('PP 20/24', 215, 562)])
        target = a.ditto_move(3, ESCAPE)
        clicks = [c[0][0] for c in a.click.call_args_list]
        self.assertEqual(clicks[0], app.battle_menu_rects(ESCAPE)[1])           # 战斗 first
        self.assertEqual(clicks[1], target)
        self.assertEqual(target, app.battle_menu_rects(ESCAPE)[3])              # the whole 蘑菇孢子 button

    def test_name_at_the_left_end_still_clicks_inside_the_button(self):
        # real layout 16:34: 蘑菇孢子 at x 148-208, 「PP: 21 / 24」 at x 260-338, button 143-345 / 537-582
        a = self.move_host([line('点到为止', 148, 488), line('蘑菇孢子', 148, 541), line('PP: 21 / 24', 260, 562, 78)])
        x, y, w, h = a.ditto_move(3, ESCAPE)
        self.assertTrue(143 <= x and x + w <= 345 and 537 <= y and y + h <= 582)
        self.assertTrue(app.inside(line('PP: 21 / 24', 260, 562, 78), (x, y, w, h)))   # 'not taken' check sees the PP

    def test_unreadable_name_uses_the_users_slot(self):
        a = self.move_host([line('？？', 215, 490), line('？？', 215, 544)])
        self.assertEqual(a.ditto_move(3, ESCAPE), app.battle_menu_rects(ESCAPE)[3])

    def test_no_pp_stops_before_clicking_the_move(self):
        a = self.move_host([line('蘑菇孢子', 215, 544), line('PP 0/24', 215, 562)])
        with self.assertRaisesRegex(RuntimeError, 'PP 用完'):
            a.ditto_move(3, ESCAPE)
        self.assertEqual(a.click.call_count, 1)                                 # only 战斗

    def test_moves_menu_that_never_opens_stops(self):
        a = self.move_host([line('逃跑', 441, 544)])
        a.check_verification = Mock(return_value={'text': '战斗 逃跑', 'lines': [line('逃跑', 441, 544, 28, 14)]})
        with self.assertRaisesRegex(RuntimeError, '技能菜单都没打开'):
            a.ditto_move(1, ESCAPE)

    def catch_host(self, turns):
        a = self.host(); a.ditto_move = Mock(side_effect=lambda slot, esc: ('move', slot))
        a.ditto_turn = Mock(side_effect=turns); a.close_info_page = Mock(return_value=('map', None))
        return a

    def actions(self, a):
        moves = [c[0][0] for c in a.ditto_move.call_args_list]
        return moves, a.press.call_args_list

    def test_swipe_spore_ball_caught(self):
        a = self.catch_host([('menu', ESCAPE, ['图图犬使出了点到为止'], {}),
                             ('menu', ESCAPE, ['野生的百变怪睡着了'], {}),
                             ('map', None, ['抓到了百变怪'], {})])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3]); self.assertEqual([p[0][0] for p in presses], [app.BALL_KEY])
        self.assertEqual(a.ditto_caught, 1)

    def test_failed_spore_means_already_asleep(self):
        # 16:56: Spore on a sleeping Ditto fails; that is not a reason to spore again (3 wasted turns).
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, ['但是失败了'], {}), ('map', None, [], {})])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3]); self.assertEqual(len(presses), 1)

    def test_sleeping_well_message_and_no_wake_in_spore_turn(self):
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, ["野生的百变怪'普睡得很香.", '百变怪醒过来了'], {}), ('map', None, [], {})])
        a.catch_ditto(ESCAPE)
        self.assertEqual(self.actions(a)[0], [1, 3])                        # a 醒 in the spore turn is ignored

    def test_low_hp_lead_stops(self):
        hp = {'lines': [line('50 / 234', 700, 340)]}
        a = self.catch_host([('menu', ESCAPE, [], hp)])
        with self.assertRaisesRegex(RuntimeError, '50/234'):
            a.catch_ditto(ESCAPE)
        self.assertEqual(app.lead_hp({'lines': [line('93 / 234', 700, 340)]}), (93, 234))

    def test_menu_back_after_a_catch_means_not_caught(self):
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, [], {}), ('page', None, [], {}),
                             ('page', None, ["收服到百变怪'普了！"], {})])
        a.close_info_page = Mock(side_effect=[('battle', ESCAPE), ('map', None)])
        a.catch_ditto(ESCAPE)
        self.assertEqual(len(a.press.call_args_list), 2); self.assertEqual(a.ditto_caught, 1)

    def test_our_sleeping_lead_does_not_count_as_spore(self):
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, ['图图犬正在呼呼大睡'], {}),
                             ('menu', ESCAPE, [], {}), ('map', None, [], {})])
        a.catch_ditto(ESCAPE)
        self.assertEqual(self.actions(a)[0], [1, 3, 3])

    def test_woken_ditto_gets_spore_again(self):
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, [], {}),
                             ('menu', ESCAPE, ['百变怪醒了'], {}), ('menu', ESCAPE, [], {}), ('map', None, [], {})])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3, 3]); self.assertEqual(len(presses), 2)

    def test_no_spore_while_it_still_sleeps(self):
        # 17:23: three balls on a Ditto that said 「睡得很香」 each turn, then spore again - wasted its last PP.
        sleeping = ["野生的百变怪'普睡得很香."]
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, [], {})] + [('menu', ESCAPE, sleeping, {})] * 5 + [('map', None, [], {})])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3]); self.assertEqual(len(presses), 6)

    def test_two_quiet_balls_then_spore(self):
        a = self.catch_host([('menu', ESCAPE, [], {})] * 4 + [('map', None, [], {})])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3, 3]); self.assertEqual(len(presses), 2)

    def test_ball_key_ignored_twice_stops(self):
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, [], {}),
                             ('ignored', ESCAPE, [], {}), ('ignored', ESCAPE, [], {})])
        with self.assertRaisesRegex(RuntimeError, '快捷键3'):
            a.catch_ditto(ESCAPE)

    def test_game_says_no_pp_stops(self):
        a = self.catch_host([('menu', ESCAPE, ['没有剩余的PP了'], {})])
        with self.assertRaisesRegex(RuntimeError, 'PP不够'):
            a.catch_ditto(ESCAPE)

    def test_catch_errors_are_not_auto_recovered(self):
        a = self.host(); a.recoveries = []; a.protection = None
        self.assertFalse(a.recoverable(RuntimeError('抓百变：蘑菇孢子 的 PP 用完了')))

    def test_catch_closes_the_info_page(self):
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, [], {}), ('page', None, ['抓到了！', '收服到百变怪了！'], {})])
        a.catch_ditto(ESCAPE)
        a.close_info_page.assert_called_once(); self.assertEqual(a.ditto_caught, 1)

    def page_host(self, frames, maps):
        a = self.host(); a._walk_base = 100
        a.check_verification = Mock(side_effect=frames); a.walk_map = Mock(side_effect=maps)
        a.caught_snapshot = Mock()
        return a

    def run_page(self, a):
        t = [0.0]
        def tick():
            t[0] += 1.0; return t[0]
        orig = app.time.monotonic; app.time.monotonic = tick
        try: a.close_info_page()
        finally: app.time.monotonic = orig

    def test_info_page_gets_esc_then_map(self):
        page = {'text': "宝可梦 资料 质子: 这只宝可梦没有质子. 百变怪'普 Lv. 35 一般 持有道具: 无", 'lines': []}   # real 16:50:46
        plain = {'text': '47号道路（Route47）道路・频道11', 'lines': []}
        a = self.page_host([page, plain, plain, plain], [False, True, True, True])
        self.run_page(a)
        self.assertEqual([c[0][0] for c in a.press.call_args_list], [app.ESC_KEY])

    def test_no_page_no_esc(self):
        plain = {'text': '47号道路（Route47）道路・频道11', 'lines': []}
        a = self.page_host([plain] * 8, [True] * 8)
        self.run_page(a)
        a.press.assert_not_called()                                         # ESC on the map would open the menu

    def test_battle_text_never_gets_esc(self):
        msg = {'text': "抓到了！收服到百变怪'普了！Lv.35", 'lines': []}
        plain = {'text': '47号道路', 'lines': []}
        a = self.page_host([msg] * 4 + [plain] * 3, [False] * 4 + [True] * 3)
        self.run_page(a)
        a.press.assert_not_called()

    def test_unknown_cover_after_six_seconds_gets_esc(self):
        cover = {'text': "百变怪'普 Lv.35", 'lines': []}
        plain = {'text': '47号道路', 'lines': []}
        a = self.page_host([cover] * 7 + [plain] * 3, [False] * 7 + [True] * 3)
        self.run_page(a)
        self.assertEqual(len(a.press.call_args_list), 1)

    def test_page_that_never_closes_stops(self):
        page = {'text': '宝可梦 资料 持有道具: 无', 'lines': []}
        a = self.page_host([page] * 60, [False] * 60)
        with self.assertRaisesRegex(RuntimeError, 'ESC'):
            self.run_page(a)
        self.assertEqual(a.press.call_count, 3)

    def test_near_miss_is_not_a_catch(self):
        self.assertFalse(app.caught_text('糟糕！差一点就抓到了！'))
        self.assertFalse(app.caught_text('可惜！就快抓到了！'))
        self.assertFalse(app.caught_text('啊！看起来就要抓到了！'))
        self.assertFalse(app.caught_text('嗨呀！都快抓到了！'))
        self.assertFalse(app.caught_text('抓到了！'))   # the first line of a catch; 收服 follows, failed balls say it too
        self.assertTrue(app.caught_text("收服到百变怪'普了！"))
        # 16:49: the ball failed, the menu came back - the catch loop must throw again, not call it caught
        a = self.catch_host([('menu', ESCAPE, [], {}), ('menu', ESCAPE, [], {}), ('menu', ESCAPE, ['糟糕！差一点就抓到了！'], {}),
                             ('page', None, ['抓到了！', "收服到百变怪'普了！"], {})])
        a.catch_ditto(ESCAPE)
        self.assertEqual(len(a.press.call_args_list), 2); self.assertEqual(a.ditto_caught, 1)

    def test_the_real_info_page_is_recognised_and_not_the_battle(self):
        self.assertTrue(app.info_page({'text': '宝可梦 资料 质子: 这只宝可梦没有质子. 持有道具: 无', 'lines': []}))
        self.assertFalse(app.info_page({'text': "47号道路（Route47）百变怪'普 Lv. 35 战斗 选择战斗技能 逃跑", 'lines': []}))

    def turn_host(self, frames):
        a = self.host(); a.walk_map = Mock(return_value=False); a.blocking_ui = Mock(return_value=None)
        a.check_verification = Mock(side_effect=frames)
        return a

    def run_turn(self, a, **kw):
        t = [0.0]
        def tick():
            t[0] += 1.0; return t[0]
        orig = app.time.monotonic; app.time.monotonic = tick
        try: return a.ditto_turn('蘑菇孢子', **kw)
        finally: app.time.monotonic = orig

    def test_battle_message_over_the_move_button_is_not_an_ignored_click(self):
        target = app.battle_menu_rects(ESCAPE)[3]
        msg = {'text': '图图犬使出了蘑菇孢子！', 'lines': [line("闪光图图犬'普使出了蘑菇孢子！", 160, 548, 300)]}
        menu = {'text': '战斗 逃跑', 'lines': [line('逃跑', 441, 544, 28, 14)]}
        a = self.turn_host([msg] * 8 + [menu])
        result = self.run_turn(a, stale=(r'蘑菇|孢子', target))
        self.assertEqual(result[0], 'menu')

    def test_open_move_menu_is_an_ignored_click(self):
        target = app.battle_menu_rects(ESCAPE)[3]
        moves = {'text': '蘑菇孢子 PP: 21 / 24', 'lines': [line('蘑菇孢子', 150, 545, 80), line('PP: 21 / 24', 260, 562, 80)]}
        a = self.turn_host([moves] * 10)
        self.assertEqual(self.run_turn(a, stale=(r'蘑菇|孢子', target))[0], 'ignored')

    def test_open_move_menu_without_readable_pp_is_still_seen(self):
        target = app.battle_menu_rects(ESCAPE)[3]
        moves = {'text': '蘑菇孢子 返回', 'lines': [line('蘑菇孢子', 150, 545, 80), line('B 返回', 795, 558, 80)]}
        a = self.turn_host([moves] * 10)
        self.assertEqual(self.run_turn(a, stale=(r'蘑菇|孢子', target))[0], 'ignored')

    def test_text_of_the_last_turn_is_not_counted(self):
        old = {'text': '战斗 逃跑 百变怪醒过来了', 'lines': [line('逃跑', 441, 544, 28, 14), line('百变怪醒过来了', 160, 500)]}
        new = {'text': '睡得很香', 'lines': [line("野生的百变怪'普睡得很香.", 160, 500)]}
        menu = {'text': '战斗 逃跑', 'lines': [line('逃跑', 441, 544, 28, 14)]}
        a = self.turn_host([old, new, menu])
        result, _, texts, _ = self.run_turn(a)
        self.assertEqual(result, 'menu'); self.assertFalse(any('醒' in t for t in texts))

    def test_endless_battle_stops(self):
        a = self.catch_host([('menu', ESCAPE, ['但是失败了'], {})] * 40)
        with self.assertRaisesRegex(RuntimeError, '还没抓到'):
            a.catch_ditto(ESCAPE)


if __name__ == '__main__':
    unittest.main()
