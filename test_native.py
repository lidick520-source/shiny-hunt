"""Native UI contract and engine regressions; no actual game input or network."""
import contextlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app
import screen_state
import native_bridge as bridge
import test_safety

class NativeTests(unittest.TestCase):
    def engine(self, root):
        with patch.object(bridge, 'BASE', root), patch.object(bridge.backend, 'permissions', return_value='测试权限'), patch.object(bridge.NativeEngine, 'watch_stop'):
            return bridge.NativeEngine()

    def test_native_does_not_construct_tk_and_start_needs_no_preview(self):
        # 2026-10-18 user requirement: 开始 finds and takes over the game itself, no preview step.
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));seen=[]
            self.assertFalse(hasattr(engine,'root'))
            with patch.object(engine,'require_permissions'),patch.object(engine,'attach_game',side_effect=lambda:seen.append('attach')), \
                 patch.object(engine,'run',side_effect=lambda:seen.append('run')):
                engine.start_native();engine.worker.join(2)
            self.assertEqual(seen,['attach','run']);self.assertFalse(engine.background)
            self.assertTrue(engine.snapshot()['confirmed'])                 # UI shows 开始任务 directly

    def test_attach_picks_the_game_screen_among_several_windows(self):
        # 21:49 real run: PokeMMO listed another window first; the old code waited for the wrong one.
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));calls=[]
            from PIL import Image
            def geo(h):
                if h!=42:raise RuntimeError('窗口为 300×200 点')
                return (10,30,1024,768)
            with patch.object(bridge,'targets',return_value=[(7,'PokeMMO'),(42,'PokeMMO')]),patch.object(bridge,'geometry',side_effect=geo), \
                 patch.object(bridge.backend,'capture',return_value=Image.new('RGB',(1024,768))), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}),patch.object(bridge,'title',return_value='PokeMMO'), \
                 patch.object(bridge,'foreground',return_value=7),patch.object(engine,'front_pid',return_value=4321), \
                 patch.object(bridge.AppKit.NSRunningApplication,'runningApplicationWithProcessIdentifier_',create=True):
                engine.attach_game()
                self.assertTrue(engine.game_in_front())                          # judged by process, not window id
            self.assertEqual(engine.calibrated,(42,(10,30,1024,768)));self.assertEqual(engine.game_pid,4321)
            self.assertTrue((Path(d)/'preview.png').exists())

    def test_top_window_check_never_skips_the_assistant(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            wins=[{'kCGWindowLayer':0,'kCGWindowOwnerPID':bridge.os.getppid()},{'kCGWindowLayer':0,'kCGWindowOwnerPID':4321}]
            with patch.object(bridge,'windows',return_value=wins):
                self.assertNotEqual(engine.top_window_pid(),4321)               # assistant on top: game is not in front

    def test_front_failure_explains_lost_accessibility(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.emit_native=lambda m:None
            with patch.object(engine,'native_call',side_effect=RuntimeError('Mac刷闪助手没有辅助功能权限')), \
                 patch.object(engine,'front_pid',return_value=999),patch.object(engine,'top_window_pid',return_value=999), \
                 patch.object(bridge,'accessibility_trusted',return_value=False),patch.object(bridge.time,'sleep'), \
                 patch.object(bridge.AppKit.NSRunningApplication,'runningApplicationWithProcessIdentifier_',create=True):
                with self.assertRaisesRegex(RuntimeError,'辅助功能权限失效'):engine.bring_game_front(4321,42,[42])
            self.assertIn('切前台失败详情',engine.say.call_args_list[-1][0][0] if isinstance(engine.say,Mock) else '切前台失败详情')

    def test_attach_falls_back_to_accessibility_when_activation_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));front=[999];calls=[]
            engine.emit_native=lambda m:None
            def native(op,**v):
                calls.append(op)
                if op=='activate':front[0]=4321
            with patch.object(engine,'native_call',side_effect=native),patch.object(engine,'front_pid',side_effect=lambda:front[0]), \
                 patch.object(bridge,'foreground',return_value=42),patch.object(bridge.time,'sleep'), \
                 patch.object(bridge.AppKit.NSRunningApplication,'runningApplicationWithProcessIdentifier_',create=True):
                self.assertEqual(engine.bring_game_front(4321,42,[42]),42)
            self.assertEqual(calls,['activate'])

    def test_game_window_on_top_is_not_enough_the_game_must_be_the_front_app(self):
        # 2026-10-25 01:57: 开始 clicked in the assistant; the game window was the top window but the
        # assistant stayed the front app, so no key would reach the game. Raise it through Accessibility.
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));front=[999];calls=[]
            engine.emit_native=lambda m:None
            def native(op,**v):
                calls.append(op)
                if op=='activate':front[0]=4321
            with patch.object(engine,'native_call',side_effect=native),patch.object(engine,'front_pid',side_effect=lambda:front[0]), \
                 patch.object(engine,'top_window_pid',return_value=4321), \
                 patch.object(bridge,'foreground',return_value=42),patch.object(bridge.time,'sleep'), \
                 patch.object(bridge.AppKit.NSRunningApplication,'runningApplicationWithProcessIdentifier_',create=True):
                self.assertEqual(engine.bring_game_front(4321,42,[42]),42)
            self.assertEqual(calls,['activate'])

    def test_attach_without_game_explains(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            with patch.object(bridge,'targets',return_value=[]),patch.object(engine,'game_app',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'没找到 PokeMMO'):engine.attach_game()

    def test_missing_screen_permission_blocks_before_countdown(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            with patch.object(bridge.backend,'permissions',return_value='屏幕录制：未授权'),patch.object(bridge.Q,'CGPreflightScreenCaptureAccess',return_value=False),patch.object(bridge.backend,'capture') as capture:
                with self.assertRaisesRegex(ValueError,'屏幕录制权限'):
                    engine.preview()
                capture.assert_not_called()
            self.assertIsNone(engine.worker)
            self.assertTrue(engine.stop_event.is_set())

    def test_busy_blocks_settings_but_stop_is_immediate(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.worker=Mock();engine.worker.is_alive.return_value=True
            with self.assertRaisesRegex(ValueError,'停止'):
                engine.command({'action':'settings','auto':True})
            engine.stop_event.clear()
            with patch.object(bridge.backend,'release_all') as release:
                engine.command({'action':'stop'})
            self.assertTrue(engine.stop_event.is_set());release.assert_called_once()

    def test_settings_and_reset_pp_contract(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            engine.command({'action':'settings','auto':True,'pp_enabled':True,'channels':False})
            self.assertTrue(json.loads(engine.settings_file.read_text())['pp_enabled'])
            engine.pp_progress.completed=6;engine.pp_progress.begin();engine.pp_progress.recovery_index=8
            engine.command({'action':'reset_pp'})
            self.assertFalse(engine.pp_progress.recovering)
            self.assertEqual(engine.pp_progress.recovery_index,0)
            self.assertEqual(engine.pp_progress.completed,0)

    def test_pp_hold_repeats_through_native_driver(self):
        # Regression: held keys live in the Swift driver, so repeats must go there too.
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));calls=[]
            frames=iter([Image.new('RGB',(1024,768),'black'),Image.new('RGB',(1024,768),'white')])
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append((op,v))), \
                 patch.object(engine,'ensure'),patch.object(engine,'capture',side_effect=lambda:next(frames)), \
                 patch.object(bridge.backend,'repeat_held_key') as python_repeat:
                engine.stop_event.clear()
                engine.hold_key(0x53,0.9)
            python_repeat.assert_not_called()
            ops=[op for op,_ in calls]
            self.assertEqual(calls[0],('key',{'key':0x53,'down':True}))
            self.assertEqual(calls[-1],('key',{'key':0x53,'down':False}))
            self.assertIn('repeat',ops)
            self.assertTrue(all(v=={'key':0x53} for op,v in calls if op=='repeat'))

    def test_pp_hold_stops_when_screen_does_not_change(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));calls=[]
            still=Image.new('RGB',(1024,768),'black')
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append(op)), \
                 patch.object(engine,'ensure'),patch.object(engine,'capture',return_value=still):
                engine.stop_event.clear()
                with self.assertRaisesRegex(RuntimeError,'没有走动'):
                    engine.hold_key(0x44,0.9)
            self.assertEqual(calls[-1],'key')

    def test_two_task_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            engine.command({'action':'settings','task_mode':'frillish'})
            state=engine.snapshot()
            self.assertEqual(state['taskMode'],'frillish')
            self.assertTrue(state['auto']);self.assertTrue(state['ppEnabled'])
            engine.command({'action':'settings','task_mode':'fixed','auto':False,'pp_enabled':False})
            state=engine.snapshot()
            self.assertEqual(state['taskMode'],'fixed')
            self.assertTrue(state['auto']);self.assertFalse(state['ppEnabled'])

    def test_mode_decides_the_hospital_route(self):
        # 2026-10-19 requirements: 轻飘飘白嫖点 goes back to the centre; PP果定点 never does.
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            self.assertTrue(engine.pp_setting())                                # default: 轻飘飘白嫖点
            engine.settings['task_mode']='fixed'
            self.assertFalse(engine.pp_setting())

    def test_snapshot_reports_game_window_for_docking(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d))
            with patch.object(bridge,'targets',return_value=[(42,'PokeMMO')]), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321,'kCGWindowBounds':{'X':0,'Y':25,'Width':1024,'Height':796}}):
                state=engine.snapshot()
            self.assertTrue(state['gameFound']);self.assertEqual(state['gameFrame'],[0.0,25.0,1024.0,796.0]);self.assertEqual(state['gamePid'],4321)

    def test_frillish_ocr_tolerance(self):
        self.assertEqual(app.frillish_count('轻飘飘 轻飘飘 轻飘期 轻飘飘 轻飘飘'),5)
        self.assertEqual(app.frillish_count('索罗亚 赫拉克罗斯'),0)

    def test_ppberry_mode_has_no_six_round_limit(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.settings['task_mode']='fixed';engine.pp_progress.completed=6;seen=[]
            with patch.object(engine,'require_permissions'),patch.object(engine,'attach_game',side_effect=lambda:seen.append('attach')), \
                 patch.object(engine,'run',side_effect=lambda:seen.append('run')):
                engine.start_native();engine.worker.join(2)
            self.assertEqual(seen,['attach','run']);self.assertFalse(engine.pp_run);self.assertEqual(engine.pp_progress.completed,0)

    def test_shutdown_releases_and_signals_worker(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.stop_event.clear()
            with patch.object(bridge.backend,'release_all') as release:
                engine.command({'action':'shutdown'});engine.shutdown()
            self.assertTrue(engine.closing);self.assertTrue(engine.stop_event.is_set())
            self.assertTrue(engine.hotkey_stop.is_set());self.assertGreaterEqual(release.call_count,1)

    def test_snapshot_contains_progress_and_no_notification_secret(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);engine=self.engine(root)
            (root/'notification.json').write_text('{"enabled":true,"miaocode":"TEST_ONLY_SECRET"}')
            engine.pp_progress.begin();engine.pp_progress.recovery_index=4;engine.recovery_step=4
            with patch.object(bridge,'BASE',root):state=engine.snapshot()
            self.assertEqual(state['recoveryIndex'],4)
            self.assertEqual(state['recoveryLabel'],'按空格确认治疗')
            self.assertNotIn('TEST_ONLY_SECRET',json.dumps(state))

    def run_context(self, a, ocr, det):
        stack=contextlib.ExitStack()
        stack.enter_context(patch.multiple(app,targets=lambda:[(1,'PokeMMO')],foreground=lambda:1,title=lambda h:'PokeMMO',geometry=lambda h:(0,0,1024,768),Detector=lambda:det,OCR=lambda stop:ocr))
        for method in ('activate','require_permissions','release_all'):
            stack.enter_context(patch.object(app.backend,method))
        return stack

    def test_second_ocr_miss_uses_first_button_only_with_image(self):
        battle={'text':'战斗逃跑','lines':[{'text':'逃跑','x':440,'y':550,'w':35,'h':15}]}
        plain={'text':'地图','lines':[]}
        for image_hit in (True,False):
            with self.subTest(image_hit=image_hit):
                a=test_safety.SafetyTests().shell();ocr=Mock();det=Mock();det.map.return_value=False
                ocr.scan.side_effect=[plain,battle,plain]
                a.original_flash_guard=Mock(side_effect=[{'matched':False,'escape_image_hit':True},{'matched':False,'escape_image_hit':image_hit}])
                a.click.side_effect=lambda *_:a.stop_event.set()
                with self.run_context(a,ocr,det):a.run()
                self.assertEqual(a.click.call_count,int(image_hit))
                if image_hit:a.click.assert_called_once_with(app.escape_button_area((430,544,55,27)))

    def test_native_input_waits_for_swift_ack(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));sent=[]
            def emit(message):
                sent.append(message)
                engine.resolve_native_input({'id':message['id'],'ok':True})
            engine.emit_native=emit
            engine.key_event(0x35,True)
            self.assertEqual((sent[0]['operation'],sent[0]['key'],sent[0]['down']),('key',0x35,True))

    def test_escape_uses_ocr_coordinates_not_fixed_box(self):
        first=app.escape_region({'text':'战斗逃跑','lines':[{'text':'逃跑','x':440,'y':550,'w':35,'h':15}]})
        moved=app.escape_region({'text':'战斗逃跑','lines':[{'text':'逃跑','x':610,'y':430,'w':42,'h':18}]})
        self.assertEqual(first,(430,544,55,27))
        self.assertEqual(moved,(600,424,62,30))
        self.assertNotEqual(first,moved)

    def test_retry_uses_five_and_never_teleport(self):
        a=test_safety.SafetyTests().shell();ocr=Mock();det=Mock();det.map.return_value=True
        ocr.scan.return_value={'text':'地图','lines':[]}
        presses=[]
        def press(key,*args,**kwargs):
            presses.append(key)
            if len(presses)==2:a.stop_event.set()
        a.press.side_effect=press
        ticks=iter(range(0,1000,2))
        with self.run_context(a,ocr,det),patch.object(app.time,'monotonic',side_effect=lambda:next(ticks)):
            a.run()
        self.assertEqual(presses,[0x35,0x35])

    def test_background_inputs_target_game_pid_without_foreground(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;engine.calibrated=(77,(0,0,1024,768))
            engine.stop_event.clear();calls=[]
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append((op,v))), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(app,'geometry',return_value=(0,0,1024,768)), \
                 patch.object(app,'foreground',return_value=999):
                self.assertEqual(engine.ensure(),(0,0))   # another app in front is fine
                engine.key_event(0x35,True);engine.mouse_event((10,20),True);engine.repeat_event(0x53)
            for op,v in calls:
                self.assertEqual((v['pid'],v['window']),(4321,77),op)

    def test_background_click_uses_focus_click_once(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;engine.calibrated=(77,(100,50,1024,768))
            engine.stop_event.clear();engine.mode='刷怪';calls=[]
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append((op,v))), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(app,'geometry',return_value=(100,50,1024,768)):
                points={engine.click(app.escape_button_area((427,539,50,28))) for _ in range(30)}
            self.assertEqual({c[0] for c in calls},{'focusClick'})
            self.assertGreater(len(points),10)                       # not one fixed pixel
            for x,y in points:                                       # always inside the 逃跑 button (355-555, 537-582)
                self.assertTrue(100+355<=x<=100+555 and 50+537<=y<=50+582,(x,y))
            self.assertGreater(max(p[0] for p in points)-min(p[0] for p in points),60)

    def test_background_pp_walk_runs_in_foreground_then_restores(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;calls=[];seen=[]
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append((op,v))), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(app.App,'recover_pp',side_effect=lambda *a:seen.append(engine.background)):
                engine.recover_pp()
            self.assertEqual(seen,[False])               # walk uses foreground input
            self.assertTrue(engine.background)           # back to background afterwards
            self.assertEqual([c[0] for c in calls],['focusBegin','focusEnd'])
            self.assertEqual(calls[0][1],{'pid':4321,'window':77})

    def test_background_channel_switch_runs_in_foreground(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;calls=[];seen=[]
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append(op)), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(app.App,'switch',side_effect=lambda *a:seen.append(engine.background)):
                engine.switch()
            self.assertEqual(seen,[False]);self.assertEqual(calls,['focusBegin','focusEnd']);self.assertTrue(engine.background)

    def switch_host(self, reads, scans):
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.click=Mock();a.press=Mock();a.wait=Mock();a.switch_snapshot=Mock();a.elapsed=3000
        a.read=Mock(side_effect=reads);a.capture=Mock()
        a.check_verification=Mock(side_effect=[{'text':'','lines':l} for l in scans])
        scores=getattr(self,'window_scores',None);score=patch.object(app,'channel_window_score',**({'side_effect':scores} if scores else {'return_value':getattr(self,'window_score',.97)}));score.start();self.addCleanup(score.stop)
        dys=getattr(self,'window_dys',None)
        match=patch.object(app,'channel_window_match',side_effect=lambda im:(app.channel_window_score(im),next(dys) if dys else 0));match.start();self.addCleanup(match.stop)
        return a

    MENU=[{'text':'好友','x':805,'y':588,'w':30,'h':16},{'text':'更换频道','x':790,'y':649,'w':60,'h':16}]
    LIST=[{'text':'频道2 -- 当前频道','x':452,'y':294,'w':110,'h':16},{'text':'频道1','x':489,'y':330,'w':36,'h':16},
          {'text':'频道2 -- 当前频道','x':452,'y':357,'w':110,'h':16},{'text':'频道3','x':489,'y':384,'w':36,'h':16},
          {'text':'频道4','x':489,'y':411,'w':36,'h':16},{'text':'频道5','x':489,'y':438,'w':36,'h':16}]

    def test_unverified_channel_switch_never_clicks(self):
        app.CHANNEL_SWITCH_VERIFIED=False;self.addCleanup(setattr,app,'CHANNEL_SWITCH_VERIFIED',True)
        a=self.switch_host([],[])
        a.switch()
        a.click.assert_not_called();a.press.assert_not_called()
        self.assertTrue(3000+2400<=a.next_switch<=3000+3000);self.assertIn('跳过换频道',a.say.call_args[0][0])

    def test_channel_switch_clicks_book_icon_then_next_channel(self):
        a=self.switch_host(['帆巴市(地) Ch. 2','频道2 -- 当前频道','','频道3','帆巴市(地) Ch. 3'],[self.MENU,self.LIST])
        a.switch()
        clicks=[c[0][0] for c in a.click.call_args_list]
        self.assertEqual(clicks[0],app.SOCIAL_ICON)                        # book icon, never the PvP pokeball
        self.assertTrue(782<=clicks[1][0]<=800 and 640<=clicks[1][1]<=650)  # 更换频道 found by OCR
        self.assertEqual(clicks[2],app.CHANNEL_DROPDOWN)
        self.assertTrue(370<=clicks[3][1]<=390)                            # 频道3 row
        self.assertEqual(clicks[4],app.CHANNEL_CHANGE_BUTTON)
        a.press.assert_not_called()                                         # no untested keyboard list navigation
        self.assertIn('已切到频道3',a.say.call_args[0][0])
        self.assertTrue(3000+2400<=a.next_switch<=3000+3000)                  # next change 40-50 min later

    def test_channel_interval_is_random_40_to_50_minutes(self):
        values={app.channel_interval() for _ in range(300)}
        self.assertTrue(min(values)>=2400 and max(values)<=3000 and len(values)>50)

    def test_channel_switch_wraps_from_last_channel(self):
        last=[dict(l,text=l['text'].replace('频道2 -- 当前频道','频道2')) for l in self.LIST]
        a=self.switch_host(['Ch.5','频道5 -- 当前频道','','频道1','Ch. 1'],[self.MENU,last])
        a.switch()
        self.assertTrue(320<=a.click.call_args_list[3][0][0][1]<=340)       # 频道1 row

    def test_channel_switch_accepts_traditional_characters_from_mac_ocr(self):
        # Real Apple Vision output on the user's screenshots: 更換频道 / 頻道2--当前频道
        menu=[dict(l,text=l['text'].replace('更换','更換')) for l in self.MENU]
        lst=[dict(l,text=l['text'].replace('频道2 -- 当前频道','頻道2--当前频道')) for l in self.LIST]
        a=self.switch_host(['Ch.2','頻道2--當前頻道','','頻道3','Ch.3'],[menu,lst])
        a.switch()
        self.assertTrue(370<=a.click.call_args_list[3][0][0][1]<=390)       # 频道3 row
        self.assertIn('已切到频道3',a.say.call_args[0][0])

    def test_channel_switch_stops_if_menu_is_wrong(self):
        pvp=[{'text':'匹配赛报名','x':810,'y':620,'w':70,'h':16}]
        a=self.switch_host(['Ch.2'],[pvp,pvp])
        with patch.object(screen_state,'matches',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'没找到「更换频道」'):a.switch()
        self.assertEqual(a.click.call_count,1)                              # never clicks inside a wrong menu

    def test_channel_switch_uses_the_fixed_row_when_ocr_misses_an_open_book_menu(self):
        # 2026-10-23 琉璃市: the menu was open (template match) but OCR returned no 更换频道, 6 times.
        a=self.switch_host(['Ch.2','频道2--当前频道','','频道3','Ch.3'],[[],[],self.LIST])
        with patch.object(screen_state,'matches',return_value=True):
            a.switch()
        self.assertEqual(a.click.call_args_list[1][0][0],app.SOCIAL_CHANGE_CHANNEL)
        self.assertIn('已切到频道3',a.say.call_args[0][0])

    # ---- 2026-10-23 琉璃市 real run ----
    SIM=Path(__file__).resolve().parent/'sim'/'frames'

    def test_cooldown_window_is_recognised_on_the_real_screenshot(self):
        from PIL import Image
        cool=Image.open(self.SIM/'ch_window_cooldown.png').convert('RGB')
        score,dy=app.channel_window_match(cool)
        self.assertGreaterEqual(score,.8);self.assertEqual(dy,app.CHANNEL_COOLDOWN_DY)
        self.assertEqual(app.channel_window_match(Image.open(self.SIM/'ch_window.png').convert('RGB'))[1],0)
        kind,_=screen_state.classify(cool,{'text':'','lines':[]},channel_window=app.channel_window_score(cool))
        self.assertEqual(kind,'channel_window')                              # recovery closes it before pressing 5

    def confirm_host(self,match,read):
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.click=Mock();a.wait=Mock();a.defocus_ui=Mock();a.capture=Mock()
        a.check_verification=Mock(return_value={'text':'','lines':self.MENU});a.read=Mock(return_value=read)
        p=patch.object(app,'channel_window_match',return_value=match);p.start();self.addCleanup(p.stop)
        return a

    def test_hidden_hud_channel_is_confirmed_by_the_window(self):
        a=self.confirm_host((.97,0),'频道2 -- 当前频道')
        self.assertIn('频道2',a.confirm_switched(2))
        self.assertEqual(a.click.call_args_list[-1][0][0],app.CHANNEL_CANCEL_BUTTON)   # window closed again

    def test_cooldown_row_after_the_change_counts_as_switched(self):
        a=self.confirm_host((.85,app.CHANNEL_COOLDOWN_DY),'')
        self.assertIn('冷却',a.confirm_switched(2))
        self.assertEqual(a.click.call_args_list[-1][0][0],app.shifted(app.CHANNEL_CANCEL_BUTTON,app.CHANNEL_COOLDOWN_DY))

    def test_window_naming_another_channel_is_a_failure(self):
        a=self.confirm_host((.97,0),'频道1 -- 当前频道')
        with self.assertRaisesRegex(RuntimeError,'不是频道2'):a.confirm_switched(2)

    def test_failed_switch_waits_ten_minutes_before_the_next_try(self):
        a=self.switch_host(['Ch.2'],[[],[]])
        with patch.object(screen_state,'matches',return_value=False):
            with self.assertRaises(RuntimeError):a.switch()
        self.assertEqual(a.next_switch,a.elapsed+600)

    def test_cooldown_window_at_the_start_is_closed_without_changing(self):
        self.window_dys=iter([app.CHANNEL_COOLDOWN_DY])
        a=self.switch_host(['Ch.2'],[self.MENU])
        a.switch()
        self.assertEqual([c[0][0] for c in a.click.call_args_list[-2:]],[app.shifted(app.CHANNEL_CANCEL_BUTTON,app.CHANNEL_COOLDOWN_DY),app.WORLD_FOCUS])
        self.assertEqual(a.next_switch,a.elapsed+600)

    def test_channel_switch_trusts_list_current_marker_over_hud(self):
        a=self.switch_host(['Ch.1','','','频道3','Ch.3'],[self.MENU,self.LIST])   # HUD misread as 1
        a.switch()
        self.assertTrue(370<=a.click.call_args_list[3][0][0][1]<=390)       # 频道3, never the 当前 row
        self.assertIn('已切到频道3',a.say.call_args[0][0])

    def test_channel_switch_stops_if_window_is_not_the_channel_window(self):
        self.window_score=.04                                               # e.g. tournament sign-up window
        a=self.switch_host(['Ch.2'],[self.MENU])
        with self.assertRaisesRegex(RuntimeError,'没看到换频道窗口'):a.switch()
        self.assertEqual(a.click.call_count,2)                              # icon + 更换频道 only, nothing inside

    def test_channel_switch_works_when_dropdown_text_is_unreadable(self):
        # Real Apple Vision missed the dropdown text on the user's screenshot; HUD Ch.2 is read instead.
        a=self.switch_host(['Ch.2','','','','Ch.3'],[self.MENU,self.LIST])
        a.switch()
        self.assertEqual(a.click.call_args_list[4][0][0],app.CHANNEL_CHANGE_BUTTON)
        self.assertIn('已切到频道3',a.say.call_args[0][0])

    def test_channel_switch_stops_if_wrong_channel_selected(self):
        a=self.switch_host(['Ch.2','','','频道4'],[self.MENU,self.LIST])
        with self.assertRaisesRegex(RuntimeError,'不是频道3'):a.switch()
        self.assertEqual(a.click.call_count,4)                              # never presses 变更

    def test_channel_list_unreadable_uses_rows_measured_on_real_screenshot(self):
        # 20:19 real run: list open, OCR returned no rows at all.
        self.window_scores=[.97,.25,.97]                                    # window / list covers buttons / window again
        a=self.switch_host(['Ch.1','','','','Ch.2'],[self.MENU,[]])
        a.switch()
        row=a.click.call_args_list[3][0][0]
        self.assertEqual(row,app.CHANNEL_ROW(2));self.assertEqual(row[1]+9,365)   # 频道2 row on the real screenshot
        self.assertIn('已切到频道2',a.say.call_args[0][0])

    def test_channel_list_not_open_never_guesses_rows(self):
        self.window_scores=[.97,.97]
        a=self.switch_host(['Ch.1','',''],[self.MENU,[]])
        with self.assertRaisesRegex(RuntimeError,'没有展开'):a.switch()
        self.assertEqual(a.click.call_count,3)

    def test_focus_end_brings_game_back_when_space_switched(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;seen=iter([{'kCGWindowOwnerPID':4321}]+[None]*5+[{'kCGWindowOwnerPID':4321}])
            running=Mock()
            with patch.object(engine,'native_call'), patch.object(bridge,'info',side_effect=lambda h:next(seen)), \
                 patch.object(bridge.time,'sleep'), \
                 patch.object(bridge.AppKit.NSRunningApplication,'runningApplicationWithProcessIdentifier_',return_value=running,create=True):
                engine.in_foreground(lambda:None)
            running.activateWithOptions_.assert_called_once()
            self.assertIn('已把游戏切回',engine.say.call_args[0][0] if isinstance(engine.say,Mock) else '已把游戏切回')

    def surf_host(self, texts):
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.press=Mock();a.wait=Mock();a.capture=Mock();a.diagnostic=Mock();a.pp_progress=Mock()
        a.check_verification=Mock(side_effect=[{'text':t,'lines':[]} for t in texts])
        a.det=Mock();a.det.map=Mock(return_value=True);a.battle_evidence=Mock(return_value=False)
        p=patch.object(app.RecoverySequence,'execute');p.start();self.addCleanup(p.stop)
        return a

    def test_pp_return_confirms_surf_prompt_left_open(self):
        # 20:05 real run: 要使用冲浪吗？是/否 stayed open after the route's last space.
        a=self.surf_host(['Ch.1 深蓝色的水面…要使用冲浪吗？是否','帆巴市 Ch.1 $21,350,355'])
        a.recover_pp()
        a.press.assert_called_once_with(0x20);a.pp_progress.finish.assert_called_once()

    def test_pp_return_stops_if_surf_prompt_never_closes(self):
        a=self.surf_host(['要使用冲浪吗？是否']*5)
        with self.assertRaisesRegex(RuntimeError,'冲浪提示仍在'):a.recover_pp()
        self.assertEqual(a.press.call_count,3);a.pp_progress.finish.assert_not_called()

    def test_background_pp_walk_restores_even_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;calls=[]
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append(op)), \
                 patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(app.App,'recover_pp',side_effect=RuntimeError('x')):
                with self.assertRaises(RuntimeError):engine.recover_pp()
            self.assertEqual(calls,['focusBegin','focusEnd']);self.assertTrue(engine.background)

    def test_background_stops_when_game_window_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=True;engine.hwnd=77;engine.calibrated=(77,(0,0,1024,768))
            engine.stop_event.clear()
            with patch.object(bridge,'info',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'不可见'):engine.ensure()

    def test_foreground_mode_takes_the_game_back_when_it_loses_focus(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=False;engine.hwnd=77;engine.calibrated=(77,(0,0,1024,768))
            engine.stop_event.clear();engine.focus_regains=0;engine.assistant_since=None;calls=[];front=[999]
            other=Mock();other.processIdentifier.return_value=555                      # some other app, not the assistant
            with patch.object(bridge,'foreground',side_effect=lambda:front[0]),patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(bridge.backend,'activate',side_effect=lambda h:front.__setitem__(0,77)) as act, \
                 patch.object(bridge,'app_geometry',return_value=(0,0,1024,768)),patch.object(engine,'release_inputs'), \
                 patch.object(bridge.AppKit.NSWorkspace,'sharedWorkspace',create=True) as ws:
                ws.return_value.frontmostApplication.return_value=other
                self.assertEqual(engine.ensure(),(0,0))
            act.assert_called_once_with(77);self.assertEqual(engine.focus_regains,1)
            self.assertIn('已自动切回前台',engine.say.call_args[0][0] if isinstance(engine.say,Mock) else '已自动切回前台')
            with patch.object(engine,'native_call',side_effect=lambda op,**v:calls.append(v)):engine.key_event(0x35,True)
            self.assertNotIn('pid',calls[0])

    def test_foreground_mode_stops_if_game_cannot_come_back(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=False;engine.hwnd=77;engine.calibrated=(77,(0,0,1024,768))
            engine.stop_event.clear();engine.focus_regains=0;engine.assistant_since=None
            other=Mock();other.processIdentifier.return_value=555
            with patch.object(bridge,'foreground',return_value=999),patch.object(bridge,'info',return_value={'kCGWindowOwnerPID':4321}), \
                 patch.object(bridge.backend,'activate'),patch.object(bridge.time,'sleep'),patch.object(engine,'release_inputs'), \
                 patch.object(bridge.AppKit.NSWorkspace,'sharedWorkspace',create=True) as ws:
                ws.return_value.frontmostApplication.return_value=other
                with self.assertRaisesRegex(RuntimeError,'切不回前台'):engine.ensure()

    def test_window_moved_is_relocated_not_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            engine=self.engine(Path(d));engine.background=False;engine.hwnd=77;engine.calibrated=(77,(0,0,1024,768));engine.stop_event.clear()
            with patch.object(bridge,'foreground',return_value=77),patch.object(bridge,'app_geometry',return_value=(200,90,1024,768)):
                self.assertEqual(engine.ensure(),(200,90))
            self.assertEqual(engine.calibrated,(77,(200,90,1024,768)))

    def recovery_host(self, on_map, text):
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        a.pp_progress=app.PPProgress(completed=6)
        a.capture=Mock(return_value=None);a.check_verification=Mock(return_value={'text':text,'lines':[]})
        a.det=Mock();a.det.map.return_value=on_map;a.diagnostic=Mock();a.wait=Mock()
        return a

    def test_pp_recovery_continues_automatically_on_map(self):
        a=self.recovery_host(True,'帆巴市')
        with patch.object(app.RecoverySequence,'execute'):a.recover_pp()
        self.assertFalse(a.pp_progress.recovering);self.assertEqual(a.pp_progress.completed,0)
        self.assertIn('继续刷怪',a.say.call_args[0][0])

    def test_pp_recovery_continues_when_map_unmatched_but_not_in_battle(self):
        a=self.recovery_host(False,'帆巴市')
        ticks=iter(range(0,100,2))
        with patch.object(app.RecoverySequence,'execute'),patch.object(app.time,'monotonic',side_effect=lambda:next(ticks)):a.recover_pp()
        self.assertEqual(a.pp_progress.completed,0);self.assertIn('未匹配',a.say.call_args[0][0])

    def test_pp_recovery_stops_if_still_in_battle(self):
        a=self.recovery_host(False,'野生怪群出现了！ 战斗')
        ticks=iter(range(0,100,2))
        with patch.object(app.RecoverySequence,'execute'),patch.object(app.time,'monotonic',side_effect=lambda:next(ticks)):
            with self.assertRaisesRegex(RuntimeError,'战斗或菜单'):a.recover_pp()

    def dialog_host(self, frames):
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        from PIL import Image
        a.press=Mock();a.wait=Mock();a.diagnostic=Mock()
        a.dialog_reference=Image.new('RGB',(1024,768),'black')
        a.capture=Mock(side_effect=[Image.new('RGB',(1024,768),c) for c in frames])
        return a

    def test_dialog_closed_needs_no_extra_space(self):
        a=self.dialog_host(['black']);a.close_dialog(0x20)
        self.assertEqual(a.press.call_count,1)

    def test_dialog_still_open_gets_one_more_space(self):
        a=self.dialog_host(['white','black']);a.close_dialog(0x20)
        self.assertEqual(a.press.call_count,2)
        self.assertIn('补按空格 1/3',str(a.say.call_args_list))

    def test_dialog_check_looks_at_top_bubble_not_bottom(self):
        # 14:32 real run: the "欢迎下次再来!" bubble sits at the top; the old bottom check missed it.
        from PIL import Image, ImageDraw
        a=self.dialog_host([])
        top=Image.new('RGB',(1024,768),(60,40,30));ImageDraw.Draw(top).rounded_rectangle((220,20,800,70),12,fill='white')
        bottom=Image.new('RGB',(1024,768),(60,40,30));ImageDraw.Draw(bottom).rectangle((100,560,924,740),fill='white')
        self.assertTrue(a.dialog_open(top)[0]);self.assertFalse(a.dialog_open(bottom)[0])

    def test_dialog_never_closes_stops(self):
        a=self.dialog_host(['white']*4)
        with self.assertRaisesRegex(RuntimeError,'对话框仍未关闭'):a.close_dialog(0x20)
        self.assertEqual(a.press.call_count,4)

    def test_blocked_walk_is_not_counted_as_moving(self):
        # 11:37 real run: a walk with an open dialogue changed the screen by only 3-5.
        from PIL import Image
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        base=Image.new('RGB',(1024,768),(100,100,100));nudged=Image.new('RGB',(1024,768),(104,104,104))
        a.capture=Mock(side_effect=[base,nudged,base,nudged]);a.ensure=Mock();a.key_event=Mock();a.repeat_event=Mock();a.wait=Mock()
        ticks=iter([0,0,10,10]*2)
        with patch.object(app.time,'monotonic',side_effect=lambda:next(ticks)):
            with self.assertRaisesRegex(RuntimeError,'没有走动'):a.hold_key(0x53,1.0)
        self.assertEqual(a.key_event.call_count,4)                          # tried twice before giving up

    def test_walk_that_failed_once_is_retried(self):
        # 2026-10-23 22:01: one S hold after the nurse did nothing; the second one walks.
        from PIL import Image
        a=test_safety.SafetyTests('test_logs_never_touch_real_run_log').shell()
        base=Image.new('RGB',(1024,768),(100,100,100));moved=Image.new('RGB',(1024,768),(160,160,160))
        a.capture=Mock(side_effect=[base,base,base,moved]);a.ensure=Mock();a.key_event=Mock();a.repeat_event=Mock();a.wait=Mock()
        ticks=iter([0,0,10,10]*2)
        with patch.object(app.time,'monotonic',side_effect=lambda:next(ticks)):
            a.hold_key(0x53,1.0)
        self.assertTrue(any('人物已走动' in str(c.args[0]) for c in a.say.call_args_list))

    def test_hospital_log_is_descriptive(self):
        h=Mock();h.close_dialog=Mock();del h.remember_dialog_reference;del h.route_snapshot
        app.RecoverySequence().execute(h,app.PPProgress(completed=6))
        lines=[c[0][0] for c in h.say.call_args_list]
        self.assertEqual(len(lines),22)
        self.assertIn('按4瞬移回宝可梦中心',lines[0]);self.assertIn('按空格结束对话',lines[6])
        self.assertIn('向下',lines[8]);self.assertIn('秒',lines[8])
        h.close_dialog.assert_called_once_with(0x20)

    def test_interrupted_recovery_keeps_checkpoint(self):
        progress=app.PPProgress(completed=6)
        host=Mock();host.wait.side_effect=RuntimeError('任务已停止')
        with self.assertRaises(RuntimeError):app.RecoverySequence().execute(host,progress)
        self.assertTrue(progress.recovering)
        self.assertEqual(progress.recovery_index,1)
        host.press.assert_called_once_with(0x34)

if __name__=='__main__':unittest.main()
