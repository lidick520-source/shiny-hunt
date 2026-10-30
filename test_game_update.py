"""PokeMMO update 2026-10-31: new HUD/icon bar, and the server-shutdown notice before it."""
import unittest
from unittest.mock import Mock
from PIL import Image
import app
import test_safety


class GameUpdateTests(unittest.TestCase):
    def host(self):
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.protection = None; a.recoveries = []; a.release_inputs = Mock(); a.incident = Mock(); a.diagnostic = Mock()
        return a

    def test_hud_channel_old_and_new(self):
        self.assertEqual(app.hud_channel('琉璃市（水）（SootopolisCity）Ch.2E6$18,255,355'), 2)
        self.assertEqual(app.hud_channel('琉璃市（水）（SootopolisCity）城镇・频道1'), 1)

    def test_new_ui_is_recognised_from_the_hud_line(self):
        new = {'lines': [{'y': 12, 'text': '琉璃市（水）（SootopolisCity）城镇・频道1'}]}
        old = {'lines': [{'y': 12, 'text': '琉璃市（水）（SootopolisCity）Ch.2E6'}]}
        self.assertTrue(app.new_game_ui(new)); self.assertFalse(app.new_game_ui(old))

    def test_new_ui_skips_the_channel_change_without_clicking(self):
        a = self.host(); a.elapsed = 100; a.next_switch = 0; a.click = Mock()
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768))); a.switch_snapshot = Mock()
        a.read = Mock(return_value='琉璃市（水）（SootopolisCity）城镇・频道')   # the digit hides under the hotbar
        a._switch()
        a.click.assert_not_called(); self.assertGreater(a.next_switch, 100)

    def test_server_shutdown_stops_without_recovery(self):
        a = self.host(); im = Image.new('RGB', (1024, 768))
        with self.assertRaises(RuntimeError) as cm:
            a.check_verification(im, {'text': '5 分 0 秒后服务器关闭. 请退出游戏. 关服说明: Pre-event update.', 'lines': []})
        self.assertFalse(a.recoverable(cm.exception))


if __name__ == '__main__':
    unittest.main()
