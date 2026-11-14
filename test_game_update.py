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

    def test_new_ui_never_clicks_the_old_icon(self):
        for hud in ('琉璃市（水）（SootopolisCity）城镇・频道', '沙漠的地下道（DesertUnd…'):   # 2026-11-13: crop cut 「频道1」
            a = self.host(); a.elapsed = 100; a.next_switch = 0; a.click = Mock()
            a.capture = Mock(return_value=Image.new('RGB', (1024, 768))); a.switch_snapshot = Mock()
            a.read = Mock(return_value=hud); a.new_ui_switch = Mock()
            a._switch()
            a.new_ui_switch.assert_called_once()
            self.assertNotIn(app.SOCIAL_ICON, [c[0][0] for c in a.click.call_args_list])

    HUD = {'text': '47号道路(Route47) 道路・频道1', 'lines': [{'text': '47号道路(Route47) 道路・频道1', 'x': 14, 'y': 8, 'w': 260, 'h': 18}]}

    @staticmethod
    def L(text, x, y, w=60, h=16):
        return {'text': text, 'x': x, 'y': y, 'w': w, 'h': h}

    def switch_host(self, frames):
        a = self.host(); a.elapsed = 100; a.next_switch = 0; a.wait = Mock(); a.defocus_ui = Mock()
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768)))
        a.check_verification = Mock(side_effect=frames)
        return a

    def with_hud(self, *lines):
        return {'text': '', 'lines': self.HUD['lines'] + list(lines)}

    def test_new_ui_switch_goes_by_words(self):
        rows = [self.L('频道1 (当前)', 450, 300), self.L('频道2', 450, 330), self.L('频道3', 450, 360)]
        hud2 = {'text': '', 'lines': [{'text': '47号道路(Route47) 道路・频道2', 'x': 14, 'y': 8, 'w': 260, 'h': 18}]}
        frames = [self.HUD, self.HUD, self.with_hud(*rows), self.with_hud(*rows, self.L('确定', 500, 420)), hud2, hud2]
        a = self.switch_host(frames); log = Mock()
        a.new_ui_switch(log)
        clicks = [c[0][0] for c in a.click.call_args_list]
        self.assertEqual(clicks[0], app.NEW_UI_CHANNEL_ENTRIES[0][1])         # globe
        self.assertEqual(clicks[1][1], 326)                                   # 频道2 row
        self.assertEqual(clicks[2][1], 416)                                   # 确定
        self.assertGreater(a.next_switch, 100 + 2000); a.press.assert_not_called()

    def test_new_ui_switch_without_a_list_skips_and_closes_only_what_opened(self):
        other = self.with_hud(self.L('孵蛋器', 400, 300))
        frames = [self.HUD, self.HUD, other, other, self.HUD, self.HUD, self.HUD, self.HUD, self.HUD]   # base, hud, globe opened 孵蛋器, ESC, closed, ≡ nothing
        a = self.switch_host(frames)
        a.new_ui_switch(Mock())                                               # no exception: hunting goes on
        self.assertEqual([c[0][0] for c in a.press.call_args_list], [app.ESC_KEY])   # once, for the opened window
        self.assertEqual(a.next_switch, 700)

    def test_old_hud_mark_ignores_english_map_names(self):
        self.assertTrue(app.OLD_HUD_MARK.search('帆巴市(地) Ch. 2'))
        self.assertTrue(app.OLD_HUD_MARK.search('Ch.'))
        self.assertFalse(app.OLD_HUD_MARK.search('电气石洞穴（ChargestoneCave）'))

    def test_server_shutdown_stops_without_recovery(self):
        a = self.host(); im = Image.new('RGB', (1024, 768))
        with self.assertRaises(RuntimeError) as cm:
            a.check_verification(im, {'text': '5 分 0 秒后服务器关闭. 请退出游戏. 关服说明: Pre-event update.', 'lines': []})
        self.assertFalse(a.recoverable(cm.exception))


if __name__ == '__main__':
    unittest.main()
