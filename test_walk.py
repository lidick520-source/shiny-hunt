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

    def test_steps_alternate_with_equal_length(self):
        a = self.host(); a.task_mode = 'walk'; a.walk_axis = 'h'; held = []
        a._hold_once = lambda k, s: held.append((k, s))
        for _ in range(4): a.walk_step()
        self.assertEqual([k for k, _ in held], [0x41, 0x44, 0x41, 0x44])
        self.assertEqual(held[0][1], held[1][1]); self.assertEqual(held[2][1], held[3][1])
        self.assertTrue(all(.35 <= s <= .6 for _, s in held))   # short: never the 0.8 s walk check

    def test_vertical_axis(self):
        a = self.host(); a.task_mode = 'walk'; a.walk_axis = 'v'; held = []
        a._hold_once = lambda k, s: held.append(k)
        a.walk_step(); a.walk_step()
        self.assertEqual(held, [0x57, 0x53])

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
