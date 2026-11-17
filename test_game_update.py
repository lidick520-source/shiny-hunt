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

    def test_hud_channel_reads_the_traditional_ocr(self):
        # 2026-11-15: OCR gave 「頻道1」 in 48 of 62 frames; all four new-UI changes failed at the first step
        self.assertEqual(app.hud_channel('47号道路（Route47）道路・頻道1※周六02:24'), 1)
        self.assertEqual(app.hud_channel('47号道路（Route47）道路。频道3ご周六'), 3)

    def test_new_ui_is_recognised_from_the_hud_line(self):
        new = {'lines': [{'y': 12, 'text': '琉璃市（水）（SootopolisCity）城镇・频道1'}]}
        old = {'lines': [{'y': 12, 'text': '琉璃市（水）（SootopolisCity）Ch.2E6'}]}
        self.assertTrue(app.new_game_ui(new)); self.assertFalse(app.new_game_ui(old))

    def test_new_ui_uses_the_people_icon_and_the_old_window_flow(self):
        for hud in ('47号道路(Route47) 道路・頻道1', '沙漠的地下道（DesertUnd…'):   # 2026-11-13: crop cut 「频道1」
            a = self.host(); a.elapsed = 100; a.next_switch = 0; a.click = Mock()
            a.capture = Mock(return_value=Image.new('RGB', (1024, 768))); a.switch_snapshot = Mock()
            a.read = Mock(return_value=hud); a.hud_now = Mock(return_value=1)
            a.open_channel_window = Mock(return_value=(0.1, 0))
            with self.assertRaises(RuntimeError):            # no window in this fake: the attempt stops
                a._switch()
            self.assertEqual(a.open_channel_window.call_args[0][1], app.NEW_UI_SOCIAL_ICON)
            self.assertNotIn(app.SOCIAL_ICON, [c[0][0] for c in a.click.call_args_list])

    def test_people_menu_item_is_found_where_the_new_ui_shows_it(self):
        # real 21:01: 社区 menu, 更换频道 at (665,617) - OCR gave 繁体 「更换頻道」
        a = self.host(); a.wait = Mock(); a.click = Mock()
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768)))
        menu = {'text': '社区 好友 公会 更换頻道', 'lines': [{'text': '更换頻道', 'x': 665, 'y': 617, 'w': 60, 'h': 16}]}
        a.check_verification = Mock(return_value=menu)
        a.open_channel_window(icon=app.NEW_UI_SOCIAL_ICON)
        clicks = [c[0][0] for c in a.click.call_args_list]
        self.assertEqual(clicks[0], app.NEW_UI_SOCIAL_ICON); self.assertEqual(clicks[1][1], 613)

    def test_menu_text_late_is_waited_for(self):
        # 2026-11-16 10:39: the 社区 menu was open, its text came too late; it then blocked walking for 4 minutes
        a = self.host(); a.wait = Mock(); a.click = Mock()
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768)))
        empty = {'text': '', 'lines': []}
        menu = {'text': '社区 好友 公会 更换频道', 'lines': [{'text': '更换频道', 'x': 665, 'y': 617, 'w': 60, 'h': 16}]}
        a.check_verification = Mock(side_effect=[empty, empty, menu])
        a.open_channel_window(icon=app.NEW_UI_SOCIAL_ICON)
        self.assertEqual(a.click.call_args_list[1][0][0][1], 613)

    def test_menu_words_without_the_row_use_the_fixed_row(self):
        a = self.host(); a.wait = Mock(); a.click = Mock()
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768)))
        words = {'text': '社区 好友 公会 附近玩家', 'lines': [{'text': w, 'x': 665, 'y': y, 'w': 30, 'h': 16}
                                                        for w, y in (('社区', 528), ('好友', 555), ('公会', 586), ('附近玩家', 648))]}
        a.check_verification = Mock(return_value=words)
        a.open_channel_window(icon=app.NEW_UI_SOCIAL_ICON)
        self.assertEqual(a.click.call_args_list[1][0][0], app.NEW_UI_CHANGE_CHANNEL)

    def test_nothing_found_closes_the_menu_before_giving_up(self):
        a = self.host(); a.wait = Mock(); a.click = Mock()
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768)))
        empty = {'text': '', 'lines': []}
        a.check_verification = Mock(side_effect=[empty] * 5 + [{'text': '好友 公会', 'lines': []}, empty])
        a.close_social_menu = Mock()
        with self.assertRaisesRegex(RuntimeError, '更换频道'):
            a.open_channel_window(icon=app.NEW_UI_SOCIAL_ICON)
        a.close_social_menu.assert_called_once()

    def test_title_bar_in_the_capture_is_recognised(self):
        # 2026-11-17 15:27: window only 768 high, the title bar was taken for a dialog bubble
        import mac_backend
        bar = Image.new('RGB', (1024, 768), (60, 70, 50))
        bar.paste((236, 236, 236), (0, 0, 1024, 28))
        for x in (20, 40, 60):
            bar.paste((236, 127, 116), (x - 6, 7, x + 6, 19))
        self.assertTrue(mac_backend.has_titlebar(bar))
        white_dialog = Image.new('RGB', (1024, 768), (60, 70, 50)); white_dialog.paste((250, 250, 250), (0, 0, 1024, 72))
        self.assertFalse(mac_backend.has_titlebar(white_dialog))           # a real white bubble has no red dot
        self.assertFalse(mac_backend.has_titlebar(Image.new('RGB', (1024, 768), (60, 70, 50))))

    def test_short_window_stops_without_recovery(self):
        a = self.host(); a.recoveries = []; a.protection = None
        self.assertFalse(a.recoverable(RuntimeError('游戏窗口变矮了：macOS 标题栏挡住了画面最上面一条')))

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
