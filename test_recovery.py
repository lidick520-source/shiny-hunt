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
        cases = [('tournament', lambda a: a.click.assert_called_with(app.TOURNAMENT_CLOSE)),
                 ('channel_window', lambda a: a.click.assert_called_with(app.CHANNEL_CANCEL_BUTTON)),
                 ('yes_no', lambda a: a.press.assert_called_with(0x20)),
                 ('social_menu', lambda a: a.click.assert_called_with(app.SOCIAL_ICON))]
        for kind, check in cases:
            a = self.host(); a.check_verification = Mock(return_value={'text': '', 'lines': []})
            kinds = iter([(kind, {}), ('spot', {})])
            a.classify_screen = Mock(side_effect=lambda im, d: next(kinds))
            self.assertEqual(a.recover_screen(), 'press5', kind); check(a)

    def test_off_spot_walks_the_route_once_then_stops(self):
        a = self.host(); a.check_verification = Mock(return_value={'text': 'Ch.1', 'lines': []})
        a.classify_screen = Mock(return_value=('outdoor', {})); a.recover_pp = Mock()
        self.assertEqual(a.recover_screen(), 'press5'); a.recover_pp.assert_called_once()
        a.recover_pp.side_effect = RuntimeError('去医院路线走完后不在刷怪点（周围水面 4%）')
        with self.assertRaisesRegex(RuntimeError, '重走过一次'): a.recover_screen()

    def test_same_screen_three_times_stops(self):
        a = self.host(); a.check_verification = Mock(return_value={'text': '', 'lines': []})
        a.classify_screen = Mock(return_value=('tournament', {}))
        with self.assertRaisesRegex(RuntimeError, '处理了3次还在'): a.recover_screen()
        self.assertEqual(a.click.call_count, 2)

    def test_at_start_only_windows_are_closed(self):
        a = self.host(); a.classify_screen = Mock(return_value=('outdoor', {}))
        self.assertEqual(a.recover_screen(first=True, seen=(Image.new('RGB', (1024, 768)), {'text': ''})), 'start')
        a.press.assert_not_called(); a.click.assert_not_called()

    def test_templates_exist_for_every_screen(self):
        for name in screen_state.TEMPLATES:
            self.assertTrue((FRAMES / f'{name}.png').exists(), name)


if __name__ == '__main__':
    unittest.main()
