"""走路单遇 (user 2026-10-30, 矢车森林): walk in the grass, no 5, no hospital."""
import unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image
import app
import test_safety

FRAMES = Path(__file__).resolve().parent / 'sim' / 'frames'


class WalkTests(unittest.TestCase):
    def host(self):
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.protection = None; a.recoveries = []; a.release_inputs = Mock(); a.incident = Mock()
        return a

    def test_map_view_tells_battle_intro_from_map(self):
        # Both frames have the same HUD text; only the black battle bars differ.
        self.assertTrue(app.map_view(Image.open(FRAMES / 'map_a_clean.png').convert('RGB')))
        self.assertFalse(app.map_view(Image.open(FRAMES / 'battle_a0_intro.png').convert('RGB')))

    def walker(self, axis, frames=None):
        a = self.host(); a.task_mode = 'walk'; a.walk_axis = axis; a.events = []
        a.ensure = Mock(); a.wait = lambda s: __import__('time').sleep(s)
        a.key_event = lambda k, down: a.events.append((k, down)); a.repeat_event = Mock()
        clean = Image.open(FRAMES / 'map_a_clean.png').convert('RGB')
        a.capture = Mock(side_effect=frames) if frames else Mock(return_value=clean)
        return a

    def test_steps_alternate_with_equal_length(self):
        a = self.walker('h'); import time
        lengths = []
        for _ in range(4):
            t = time.monotonic(); self.assertFalse(a.walk_step()); lengths.append(time.monotonic() - t)
        self.assertEqual([k for k, d in a.events if d], [0x41, 0x44, 0x41, 0x44])
        self.assertEqual([k for k, d in a.events if not d], [0x41, 0x44, 0x41, 0x44])   # every key let go
        self.assertAlmostEqual(lengths[0], lengths[1], delta=.15)
        self.assertTrue(all(.75 <= x <= 1.45 for x in lengths))

    def test_vertical_axis(self):
        a = self.walker('v'); a.walk_step(); a.walk_step()
        self.assertEqual([k for k, d in a.events if d], [0x57, 0x53])

    def test_step_lets_go_when_the_battle_window_appears(self):
        battle = Image.open(FRAMES / 'battle_a0_intro.png').convert('RGB')
        a = self.walker('h', frames=[battle] * 50); import time
        t = time.monotonic(); self.assertTrue(a.walk_step())
        self.assertLess(time.monotonic() - t, .6); self.assertEqual(a.events[-1], (0x41, False))

    def test_mode_label(self):
        a = self.host()
        for mode, label in (('walk', '走路单遇'), ('fixed', 'PP果定点'), ('frillish', '白嫖闪')):
            a.task_mode = mode; self.assertEqual(a.mode_label(), label)

    def test_recovery_outdoors_goes_back_to_walking(self):
        a = self.host(); a.task_mode = 'walk'; a.pp_run = False
        a.capture = Mock(return_value=Image.new('RGB', (1024, 768)))
        a.check_verification = Mock(return_value={'text': '', 'lines': []})
        a.classify_screen = Mock(return_value=('outdoor', {}))
        a.press = Mock()
        self.assertEqual(a.recover_screen(), 'press5')   # the run loop turns press5 into walking in this mode
        a.press.assert_not_called()


    def test_walk_still_battle_words(self):
        self.assertTrue(app.WALK_STILL_BATTLE.search("刺尾虫'虫Lv.9"))
        self.assertFalse(app.WALK_STILL_BATTLE.search('橙华森林（PetalburgWoods）道路•频道1 247香水步数 成功逃脱!'))


class WalkSettingsTests(unittest.TestCase):
    def test_walk_mode_and_axis_are_saved(self):
        import tempfile
        from unittest.mock import patch
        import native_bridge as bridge
        with tempfile.TemporaryDirectory() as d:
            with patch.object(bridge, 'BASE', Path(d)), patch.object(bridge.backend, 'permissions', return_value='x'), patch.object(bridge.NativeEngine, 'watch_stop'):
                e = bridge.NativeEngine()
            e.command({'action': 'settings', 'task_mode': 'walk', 'walk_axis': 'v'})
            st = e.snapshot()
            self.assertEqual((st['taskMode'], st['walkAxis'], st['ppEnabled']), ('walk', 'v', False))
            with self.assertRaises(ValueError): e.command({'action': 'settings', 'walk_axis': 'x'})


if __name__ == '__main__':
    unittest.main()
