"""Self-recovery rules (2026-10-18): recover from game situations, never from protection stops."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
import app, screen_state
import test_safety

FRAMES = Path(__file__).resolve().parent / 'assets' / 'screens'


class RecoveryTests(unittest.TestCase):
    def host(self):
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.protection = None; a.recoveries = []; a.release_inputs = Mock(); a.incident = Mock()
        return a

    def test_shiny_and_captcha_are_never_recovered(self):
        for msg in ('原规则命中闪光：指定OCR区域文字 + 逃跑图像；已停止', '检测到验证码，已停止，请人工处理', 'F8紧急停止', '已手动停止'):
            a = self.host()
            self.assertFalse(a.recoverable(RuntimeError(msg)), msg)

    def test_protection_flag_or_stop_event_blocks_recovery(self):
        a = self.host(); a.protection = '闪光'
        self.assertFalse(a.recoverable(RuntimeError('等待画面变化超时，已停止，请检查游戏')))
        a = self.host(); a.stop_event.set()
        self.assertFalse(a.recoverable(RuntimeError('等待画面变化超时，已停止，请检查游戏')))

    def test_program_errors_stop_at_once(self):
        self.assertFalse(self.host().recoverable(AttributeError('x')))

    def test_game_situations_recover_three_times_per_ten_minutes(self):
        a = self.host(); e = RuntimeError('等待画面变化超时，已停止，请检查游戏')
        self.assertEqual([a.recoverable(e) for _ in range(4)], [True, True, True, False])

    def test_input_permission_wording_is_still_recoverable(self):
        # 21:00 sim: "…请检查5的绑定及输入权限" was a covered screen, not a permission problem.
        self.assertTrue(self.host().recoverable(RuntimeError('重试后仍是地图，游戏未触发技能；请检查5的绑定及输入权限。诊断截图已保存')))

    def test_battle_is_handed_back_to_the_normal_loop(self):
        a = self.host(); a.det = Mock(); a.det.map.return_value = False; a.det.menu.return_value = True
        a.check_verification = Mock(return_value={'text': '战斗 逃跑', 'lines': []})
        self.assertEqual(a.recover_screen(), 'battle')
        a.click.assert_not_called(); a.press.assert_not_called()   # escape only after the shiny check in run()

    def test_each_screen_gets_its_safe_action(self):
        cases = [('tournament', lambda a: a.click.assert_any_call(app.TOURNAMENT_CLOSE)),
                 ('channel_window', lambda a: a.click.assert_any_call(app.CHANNEL_CANCEL_BUTTON)),
                 ('yes_no', lambda a: a.press.assert_called_with(0x20)),
                 ('social_menu', lambda a: a.click.assert_any_call(app.SOCIAL_ICON))]
        for kind, check in cases:
            a = self.host(); a.check_verification = Mock(return_value={'text': '', 'lines': []})
            kinds = iter([(kind, {}), ('spot', {})])
            a.classify_screen = Mock(side_effect=lambda im, d: next(kinds))
            self.assertEqual(a.recover_screen(), 'press5', kind); check(a)

    def test_off_spot_walks_the_route_once_then_stops(self):
        a = self.host(); a.check_verification = Mock(return_value={'text': 'Ch.1', 'lines': []})
        a.classify_screen = Mock(return_value=('outdoor', {})); a.recover_pp = Mock(); a.pp_run = True   # 轻飘飘白嫖点
        self.assertEqual(a.recover_screen(), 'press5'); a.recover_pp.assert_called_once()
        a.recover_pp.side_effect = RuntimeError('去医院路线走完后不在刷怪点（周围水面 4%）')
        with self.assertRaisesRegex(RuntimeError, '重走过一次'): a.recover_screen()

    def test_ppberry_spot_never_walks_the_hospital_route(self):
        # PP果定点: the spot can be anywhere, so off the spot it stops instead of pressing 4.
        # 2026-10-26: first it tries 5 once (a crowd or a channel change can make the spot look foreign).
        for kind in ('outdoor', 'unknown', 'pc_hall'):
            a = self.host(); a.pp_run = False; a.check_verification = Mock(return_value={'text': 'Ch.1', 'lines': []})
            a.classify_screen = Mock(return_value=(kind, {})); a.recover_pp = Mock()
            if kind != 'pc_hall':
                self.assertEqual(a.recover_screen(), 'press5', kind)
                a.recover_pp.assert_not_called(); self.assertNotIn(0x34, [c.args[0] for c in a.press.call_args_list])
                continue
            with self.assertRaisesRegex(RuntimeError, 'PP果定点'): a.recover_screen()
            a.recover_pp.assert_not_called(); self.assertNotIn(0x34, [c.args[0] for c in a.press.call_args_list])

    def test_ppberry_probe_is_spent_once_per_recovery(self):
        # Within one recovery: after the 5-probe was used, a foreign screen stops (handled by the 3-per-10-min cap
        # across recoveries). Here: a closed popup first spends nothing, the second foreign screen probes.
        a = self.host(); a.pp_run = False; a.check_verification = Mock(return_value={'text': 'Ch.1', 'lines': []})
        kinds = iter([('social_menu', {}), ('outdoor', {})])
        a.classify_screen = Mock(side_effect=lambda im, d: next(kinds)); a.recover_pp = Mock()
        self.assertEqual(a.recover_screen(), 'press5'); a.recover_pp.assert_not_called()

    def test_ppberry_spot_is_not_judged_by_water(self):
        a = self.host(); a.task_mode = 'fixed'; a.det = Mock(); a.det.map.return_value = False; a.det.menu.return_value = False
        with patch.object(screen_state, 'water_ratio', return_value=.46):
            kind, _ = a.classify_screen(Image.new('RGB', (1024, 768), (40, 80, 200)), {'text': 'Ch.1 $21,350,355', 'lines': []})
        self.assertNotEqual(kind, 'spot')

    def test_same_screen_three_times_stops(self):
        a = self.host(); a.check_verification = Mock(return_value={'text': '', 'lines': []})
        a.classify_screen = Mock(return_value=('tournament', {}))
        with self.assertRaisesRegex(RuntimeError, '处理了3次还在'): a.recover_screen()
        self.assertEqual([c.args[0] for c in a.click.call_args_list], [app.TOURNAMENT_CLOSE, app.WORLD_FOCUS] * 2)

    def test_closed_popup_over_the_spot_tries_5_before_the_route(self):
        # 2026-10-23 琉璃市: the book menu was closed, the crowded water spot then looked "outdoor".
        a = self.host(); a.pp_run = True; a.check_verification = Mock(return_value={'text': '', 'lines': []})
        kinds = iter([('social_menu', {}), ('outdoor', {})])
        a.classify_screen = Mock(side_effect=lambda im, d: next(kinds)); a.recover_pp = Mock()
        self.assertEqual(a.recover_screen(), 'press5')
        a.recover_pp.assert_not_called()
        self.assertEqual([c.args[0] for c in a.click.call_args_list], [app.SOCIAL_ICON, app.WORLD_FOCUS])

    def test_at_start_only_windows_are_closed(self):
        a = self.host(); a.classify_screen = Mock(return_value=('outdoor', {}))
        self.assertEqual(a.recover_screen(first=True, seen=(Image.new('RGB', (1024, 768)), {'text': ''})), 'start')
        a.press.assert_not_called(); a.click.assert_not_called()

    def test_templates_exist_for_every_screen(self):
        for name in screen_state.TEMPLATES:
            self.assertTrue((FRAMES / f'{name}.png').exists(), name)


if __name__ == '__main__':
    unittest.main()
