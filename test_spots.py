"""白嫖闪 spot list (2026-10-19): one entry per spot, added one at a time."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import native_bridge as bridge
import pp_recovery
import spots
import test_safety


class SpotTests(unittest.TestCase):
    def test_fanba_route_is_exactly_the_verified_one(self):
        self.assertEqual(pp_recovery.RecoverySequence(spots.get('fanba')).steps, pp_recovery.RecoverySequence().steps)

    def test_every_spot_builds_a_valid_route(self):
        for spot in spots.SPOTS:
            seq = pp_recovery.RecoverySequence(spot)
            self.assertEqual(len(seq.steps), len(seq.labels), spot['key'])
            self.assertEqual(seq.steps[0], ('press', 0x34))                  # always starts with 4 = teleport
            if spot['surf']:
                self.assertEqual([s[0] for s in seq.steps[-4:]], ['press', 'wait', 'press', 'wait'])

    def test_lilycove_is_listed_as_not_yet_verified(self):
        lily = spots.get('lilycove')
        self.assertEqual(lily['targets'], ['鲤鱼王']); self.assertFalse(lily['verified'])
        self.assertIsNone(lily['spot_water'])                                 # not measured until real screenshots
        self.assertTrue(spots.get('fanba')['verified'])

    def test_target_count(self):
        self.assertEqual(spots.target_count('鲤鱼王Lv.20 鲤鱼王 鲤鱼王Lv.21', spots.get('lilycove')), 3)
        self.assertEqual(spots.target_count('轻飘飘 轻飘期 轻飘漂', spots.get('fanba')), 3)
        self.assertEqual(spots.target_count('轻飘飘 轻飘飘 轻飘飘', spots.get('lilycove')), 0)
        # Real OCR at 琉璃市 2026-10-20 14:41:46 (stopped the run before this fix):
        real = "鯉鱼王'水Lv.358g鲤鱼王'水Lv.358＆鯉鱼王'水Lv.359&鲤鱼王'水Lv.35古＆鯉鱼王'水Lv.358g"
        self.assertEqual(spots.target_count(real, spots.get('lilycove')), 5)

    def test_lilycove_waits_for_the_door_fade_before_walking(self):
        seq = pp_recovery.RecoverySequence(spots.get('lilycove'))
        kinds = [s[0] for s in seq.steps]
        self.assertIn('wait_scene', kinds)
        first_hold = kinds.index('hold'); fade = kinds.index('wait_scene')
        self.assertLess(first_hold, fade); self.assertIn('hold', kinds[fade:])
        self.assertEqual(seq.labels[fade], '等出门黑屏结束')
        self.assertIn('最多3.0秒', pp_recovery.describe(fade, seq.steps[fade], seq.labels))

    def test_unknown_key_falls_back_to_default(self):
        self.assertEqual(spots.get('nope')['key'], spots.DEFAULT_SPOT)


class EngineSpotTests(unittest.TestCase):
    def engine(self, root):
        with patch.object(bridge, 'BASE', root), patch.object(bridge.backend, 'permissions', return_value='测试权限'), \
             patch.object(bridge.NativeEngine, 'watch_stop'):
            return bridge.NativeEngine()

    def test_spot_setting_is_validated_and_reported(self):
        with tempfile.TemporaryDirectory() as d:
            engine = self.engine(Path(d))
            engine.command({'action': 'settings', 'spot': 'lilycove'})
            self.assertEqual(engine.settings['spot'], 'lilycove')
            with self.assertRaisesRegex(ValueError, '点位无效'):
                engine.command({'action': 'settings', 'spot': 'mars'})
            with patch.object(bridge, 'targets', return_value=[]):
                state = engine.snapshot()
            self.assertEqual(state['spot'], 'lilycove')
            self.assertEqual([s['key'] for s in state['spots']], [s['key'] for s in spots.SPOTS])
            self.assertEqual(state['recoveryTotal'], len(pp_recovery.RecoverySequence(spots.get('lilycove')).steps))

    def test_route_test_forces_one_hospital_trip_without_channels(self):
        with tempfile.TemporaryDirectory() as d:
            engine = self.engine(Path(d)); engine.settings.update(task_mode='fixed', channels=True); seen = []
            with patch.object(engine, 'require_permissions'), patch.object(engine, 'attach_game', side_effect=lambda: seen.append('attach')), \
                 patch.object(engine, 'run', side_effect=lambda: seen.append('run')):
                engine.command({'action': 'test_route'}); engine.worker.join(2)
            self.assertEqual(seen, ['attach', 'run'])
            self.assertTrue(engine.route_test and engine.pp_run); self.assertFalse(engine.channel_run)

    def test_route_test_is_never_auto_recovered(self):
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.protection = None; a.recoveries = []; a.route_test = True
        self.assertFalse(a.recoverable(RuntimeError('去医院路线走完后不在刷怪点（周围水面 4%）')))


class DoorFadeTests(unittest.TestCase):
    def test_wait_scene_returns_once_the_map_is_back(self):
        from PIL import Image
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        frames = [Image.new('RGB', (1024, 768), (0, 0, 0))] * 2 + [Image.new('RGB', (1024, 768), (70, 110, 120))]
        a.capture = Mock(side_effect=frames); a.wait = Mock()
        app.App.wait_scene(a, 3.0)
        self.assertEqual(a.capture.call_count, 3)
        self.assertTrue(any('已出门' in str(c.args[0]) for c in a.say.call_args_list))


class BattleTargetTests(unittest.TestCase):
    """At 琉璃市 the horde must read as 鲤鱼王 before any escape click."""
    def run_battle(self, spot, text):
        a = test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.task_mode = 'frillish'; a.spot = spots.get(spot)
        ocr = Mock(); battle = {'text': text + ' 战斗 逃跑', 'lines': [{'text': '逃跑', 'x': 440, 'y': 550, 'w': 35, 'h': 15}]}
        ocr.scan.side_effect = [{'text': '地图', 'lines': []}, battle, battle, battle]
        det = Mock(); det.map.return_value = False
        a.original_flash_guard = Mock(return_value={'matched': False, 'escape_image_hit': True})
        def stop_after_click(*args, **kwargs): a.stop_event.set()
        a.click.side_effect = stop_after_click
        with patch.multiple(app, targets=lambda: [(1, 'PokeMMO')], foreground=lambda: 1, title=lambda h: 'PokeMMO',
                            geometry=lambda h: (0, 0, 1024, 768), Detector=lambda: det, OCR=lambda stop: ocr), \
             patch.object(app.backend, 'activate'), patch.object(app.backend, 'require_permissions'), patch.object(app.backend, 'release_all'):
            a.run()
        return a

    def test_magikarp_horde_is_escaped(self):
        a = self.run_battle('lilycove', '鲤鱼王Lv.20 鲤鱼王Lv.21 鲤鱼王Lv.20 鲤鱼王 鲤鱼王')
        a.click.assert_called_once()

    def test_wrong_horde_at_lilycove_stops_without_escaping(self):
        a = self.run_battle('lilycove', '轻飘飘 轻飘飘 轻飘飘 轻飘飘 轻飘飘')
        a.click.assert_not_called()
        self.assertTrue(any('未确认是鲤鱼王群' in str(c.args[0]) for c in a.say.call_args_list))


if __name__ == '__main__':
    unittest.main()
