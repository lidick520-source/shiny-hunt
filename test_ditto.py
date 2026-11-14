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
        self.assertTrue(530 < target[1] + target[3] / 2 < 585)                  # the 蘑菇孢子 row

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
        a.ditto_turn = Mock(side_effect=turns); a.close_info_page = Mock()
        return a

    def actions(self, a):
        moves = [c[0][0] for c in a.ditto_move.call_args_list]
        return moves, a.press.call_args_list

    def test_swipe_spore_ball_caught(self):
        a = self.catch_host([('menu', ESCAPE, ['图图犬使出了点到为止']),
                             ('menu', ESCAPE, ['野生的百变怪睡着了']),
                             ('map', None, ['抓到了百变怪'])])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3]); self.assertEqual([p[0][0] for p in presses], [app.BALL_KEY])
        self.assertEqual(a.ditto_caught, 1)

    def test_missed_spore_is_used_again(self):
        a = self.catch_host([('menu', ESCAPE, []), ('menu', ESCAPE, ['但是失败了']),
                             ('menu', ESCAPE, []), ('map', None, [])])
        a.catch_ditto(ESCAPE)
        self.assertEqual(self.actions(a)[0], [1, 3, 3])

    def test_our_sleeping_lead_does_not_count_as_spore(self):
        a = self.catch_host([('menu', ESCAPE, []), ('menu', ESCAPE, ['图图犬正在呼呼大睡']),
                             ('menu', ESCAPE, []), ('map', None, [])])
        a.catch_ditto(ESCAPE)
        self.assertEqual(self.actions(a)[0], [1, 3, 3])

    def test_woken_ditto_gets_spore_again(self):
        a = self.catch_host([('menu', ESCAPE, []), ('menu', ESCAPE, []),
                             ('menu', ESCAPE, ['百变怪醒了']), ('menu', ESCAPE, []), ('map', None, [])])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3, 3]); self.assertEqual(len(presses), 2)

    def test_three_failed_balls_spore_again(self):
        a = self.catch_host([('menu', ESCAPE, [])] * 5 + [('map', None, [])])
        a.catch_ditto(ESCAPE)
        moves, presses = self.actions(a)
        self.assertEqual(moves, [1, 3, 3]); self.assertEqual(len(presses), 3)

    def test_ball_key_ignored_twice_stops(self):
        a = self.catch_host([('menu', ESCAPE, []), ('menu', ESCAPE, []),
                             ('ignored', ESCAPE, []), ('ignored', ESCAPE, [])])
        with self.assertRaisesRegex(RuntimeError, '快捷键3'):
            a.catch_ditto(ESCAPE)

    def test_game_says_no_pp_stops(self):
        a = self.catch_host([('menu', ESCAPE, ['没有剩余的PP了'])])
        with self.assertRaisesRegex(RuntimeError, 'PP不够'):
            a.catch_ditto(ESCAPE)

    def test_catch_errors_are_not_auto_recovered(self):
        a = self.host(); a.recoveries = []; a.protection = None
        self.assertFalse(a.recoverable(RuntimeError('抓百变：蘑菇孢子 的 PP 用完了')))

    def test_catch_closes_the_info_page(self):
        a = self.catch_host([('menu', ESCAPE, []), ('menu', ESCAPE, []), ('page', None, ['抓到了！', '收服到百变怪了！'])])
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
        page = {'text': "百变怪'普 Lv.35 性格 胆小 特性 变身者 持有物 无", 'lines': []}
        plain = {'text': '47号道路（Route47）道路・频道11', 'lines': []}
        a = self.page_host([page, plain, plain, plain], [False, True, True, True])
        self.run_page(a)
        self.assertEqual([c[0][0] for c in a.press.call_args_list], [app.ESC_KEY])

    def test_no_page_no_esc(self):
        plain = {'text': '47号道路（Route47）道路・频道11', 'lines': []}
        a = self.page_host([plain] * 5, [True] * 5)
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
        page = {'text': '性格 特性 持有物', 'lines': []}
        a = self.page_host([page] * 60, [False] * 60)
        with self.assertRaisesRegex(RuntimeError, 'ESC'):
            self.run_page(a)
        self.assertEqual(a.press.call_count, 3)

    def test_endless_battle_stops(self):
        a = self.catch_host([('menu', ESCAPE, ['但是失败了'])] * 40)
        with self.assertRaisesRegex(RuntimeError, '还没抓到'):
            a.catch_ditto(ESCAPE)


if __name__ == '__main__':
    unittest.main()
