"""Offline regressions; never post input or contact the notification service."""
import io
import threading
import tempfile
from pathlib import Path
import notifications
import unittest
from unittest.mock import patch, Mock
import numpy as np
from PIL import Image
import app
from notifications import Notifier

# Keep every test's log lines out of the user's real run.log (test_native imports this module too).
_TEST_LOG_DIR = tempfile.TemporaryDirectory()
app.LOG_FILE = Path(_TEST_LOG_DIR.name)/'run.log'
app.INCIDENT_DIR = Path(_TEST_LOG_DIR.name)/'incidents'

class SafetyTests(unittest.TestCase):
    def test_logs_never_touch_real_run_log(self):
        self.assertNotEqual(app.LOG_FILE.resolve(), (app.BASE/'run.log').resolve())

    def setUp(self):
        self.config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_dir.cleanup)
        folder = Path(self.config_dir.name)
        (folder/'notification.json').write_text('{"enabled":true,"miaocode":"OFFLINE_TEST_ONLY"}')
        config_patch = patch.object(notifications, 'BASE', folder)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def shell(self):
        a=app.App.__new__(app.App)
        a.stop_event=threading.Event();a.say=Mock();a.notifier=Mock()
        a.hwnd=1;a.bound_window=1;a.calibrated=(1,(0,0,1024,768))
        a.saved_rest=0;a.rest_until=0;a.elapsed=0;a.next_switch=3000
        a.channel_run=False;a.auto_run=True;a.mode='刷怪'
        a.pp_run=False;a.pp_progress=app.PPProgress();a.total_rounds=0
        a.wait=Mock();a.ensure=Mock(return_value=(0,0));a.press=Mock();a.click=Mock();a.diagnostic=Mock()
        a.capture=Mock(return_value=Image.new('RGB',(1024,768)))
        return a

    def test_original_rule(self):
        for a,b,img,expected in [('闪 光','',True,True),('','闪',True,False),('闪光','',False,False),('普通','',True,False)]:
            self.assertEqual(app.original_flash_match(a,b,img),expected)

    def test_monitor_mode_is_not_misclassified_as_shiny(self):
        self.assertIsNone(app.notification_kind('监测模式：原规则未命中闪光；自动逃跑未开启'))
        self.assertEqual(app.notification_kind('原规则命中闪光：指定OCR区域文字 + 逃跑图像'),'闪光')

    def test_flash_guard_stops_before_return(self):
        a=self.shell();a.det=Mock();a.det.original_escape_score.return_value=.9
        a.ocr=Mock();a.ocr.read.side_effect=['闪光','']
        with patch.object(app.backend,'release_all'):
            result=a.original_flash_guard(a.capture())
        self.assertTrue(result['matched']);self.assertTrue(a.stop_event.is_set())
        a.click.assert_not_called()

    def test_verification_stops_before_snapshot(self):
        a=self.shell()
        a.diagnostic.side_effect=lambda *args:self.assertTrue(a.stop_event.is_set())
        with self.assertRaisesRegex(RuntimeError,'验证码'):
            a.check_verification(a.capture(),{'text':'验证码'})
        a.click.assert_not_called()

    def test_run_shiny_and_verification_never_escape(self):
        for data,guard,kind in [({'text':'验证码','lines':[]},False,'验证'),({'text':'战斗逃跑','lines':[{'text':'逃跑','x':440,'y':550,'w':35,'h':15}]},True,'闪光')]:
            a=self.shell();ocr=Mock();ocr.scan.side_effect=[{'text':'地图','lines':[]},data]
            det=Mock();det.map.return_value=False
            a.original_flash_guard=Mock(return_value={'matched':guard,'escape_image_hit':True})
            a.notifier.send.side_effect=lambda *args:self.assertTrue(a.stop_event.is_set())
            with patch.multiple(app,targets=lambda:[(1,'PokeMMO')],foreground=lambda:1,title=lambda h:'PokeMMO',geometry=lambda h:(0,0,1024,768),Detector=lambda:det,OCR=lambda stop:ocr), patch.object(app.backend,'activate'),patch.object(app.backend,'require_permissions'),patch.object(app.backend,'release_all'):
                a.run()
            a.click.assert_not_called();self.assertTrue(a.stop_event.is_set())
            self.assertEqual(a.notifier.send.call_args.args[0],kind)
            ocr.close.assert_called_once()

    def test_three_rounds_then_manual_stop(self):
        a=self.shell();ocr=Mock();det=Mock();det.map.return_value=False
        battle={'text':'战斗逃跑','lines':[{'text':'逃跑','x':440,'y':550,'w':35,'h':15}]}
        plain={'text':'地图','lines':[]}
        # startup + (battle scan, fresh pre-click scan, two map confirmations) x 3
        ocr.scan.side_effect=[plain]+[d for _ in range(3) for d in (battle,battle,plain,plain)]
        a.original_flash_guard=Mock(return_value={'matched':False,'escape_image_hit':True})
        presses=[]
        def press(*args,**kwargs):
            presses.append(args)
            if len(presses)==4:a.stop_event.set()
        a.press.side_effect=press
        with patch.multiple(app,targets=lambda:[(1,'PokeMMO')],foreground=lambda:1,title=lambda h:'PokeMMO',geometry=lambda h:(0,0,1024,768),Detector=lambda:det,OCR=lambda stop:ocr,map_return_evidence=lambda *args:True),patch.object(app.backend,'activate'),patch.object(app.backend,'require_permissions'),patch.object(app.backend,'release_all'):
            a.run()
        self.assertEqual(a.click.call_count,3)
        self.assertEqual(len(presses),4)
        a.notifier.send.assert_not_called()

    def test_verification_before_escape_click(self):
        a=self.shell();ocr=Mock();det=Mock();det.map.return_value=False
        battle={'text':'战斗逃跑','lines':[{'text':'逃跑','x':440,'y':550,'w':35,'h':15}]}
        ocr.scan.side_effect=[{'text':'地图','lines':[]},battle,{'text':'验证码','lines':[]}]
        a.original_flash_guard=Mock(return_value={'matched':False,'escape_image_hit':True})
        with patch.multiple(app,targets=lambda:[(1,'PokeMMO')],foreground=lambda:1,title=lambda h:'PokeMMO',geometry=lambda h:(0,0,1024,768),Detector=lambda:det,OCR=lambda stop:ocr),patch.object(app.backend,'activate'),patch.object(app.backend,'require_permissions'),patch.object(app.backend,'release_all'):
            a.run()
        a.click.assert_not_called()
        self.assertEqual(a.notifier.send.call_args.args[0],'验证')

    def test_focus_loss_blocks_input(self):
        a=self.shell();del a.ensure
        with patch.object(app,'foreground',return_value=2):
            with self.assertRaisesRegex(RuntimeError,'前台'):a.ensure()

    def test_move_requires_recalibration(self):
        a=self.shell();del a.ensure
        with patch.object(app,'foreground',return_value=1),patch.object(app,'geometry',return_value=(10,0,1024,768)):
            with self.assertRaisesRegex(RuntimeError,'重新预览'):a.ensure()

    def test_key_released_on_interrupt(self):
        a=self.shell();del a.press
        a.wait.side_effect=RuntimeError('任务已停止')
        with patch.object(app.backend,'key') as key:
            with self.assertRaises(RuntimeError):a.press(0x34)
            self.assertEqual([c.args for c in key.call_args_list],[(0x34,True),(0x34,False)])

    def test_click_moves_before_down_and_releases(self):
        a=self.shell();del a.click
        events=[]
        with patch.object(app.backend,'move',side_effect=lambda p:events.append(('move',p))),patch.object(app.backend,'mouse',side_effect=lambda p,d:events.append(('mouse',p,d))):
            a.click((365,548,181,26))
        point=events[0][1]
        self.assertEqual(events,[('move',point),('mouse',point,True),('mouse',point,False)])  # same spot for move/down/up
        self.assertTrue(365<=point[0]<=365+181 and 548<=point[1]<=548+26,point)

    def test_digit_four_hold_is_bounded(self):
        a=self.shell();del a.press
        with patch.object(app.backend,'key'):
            for _ in range(20):a.press(0x34)
        durations=[c.args[0] for c in a.wait.call_args_list]
        self.assertTrue(all(.09<=x<=.17 for x in durations))
        self.assertGreater(len(set(durations)),1)

    def test_notification_dedup_and_success(self):
        calls=[]
        def transport(url,timeout):calls.append(url);return io.BytesIO(b'{"code":0}')
        n=Notifier(Mock(),transport=transport,local=False)
        t=n.send('闪光','测试');t.join(2)
        self.assertIsNone(n.send('闪光','测试'));self.assertEqual(len(calls),1)
        self.assertIn('text=',calls[0]);self.assertNotIn('&ts=',calls[0])

    def test_only_verification_and_shiny_can_call_remote(self):
        transport=Mock()
        n=Notifier(Mock(),transport=transport,local=False)
        for kind in ['异常','测试','停止','未知']:
            n.send(kind,'本地提示').join(2)
        transport.assert_not_called()

    def test_notification_failure_bounded(self):
        transport=Mock(side_effect=OSError('offline'));sleep=Mock();logs=[]
        n=Notifier(logs.append,transport=transport,sleeper=sleep,local=False)
        n.send('验证','测试').join(2)
        self.assertEqual(transport.call_count,2);self.assertEqual(sleep.call_count,1);self.assertGreater(sleep.call_args.args[0],14)
        self.assertIn('保持停止',logs[-1])

    def test_original_escape_template(self):
        d=app.Detector();im=Image.new('RGB',(1024,768))
        template=Image.open(app.BASE/'assets/original_escape.png').convert('RGB')
        im.paste(template,(440,545));self.assertGreaterEqual(d.original_escape_score(im),.99)

if __name__=='__main__':unittest.main(verbosity=2)
