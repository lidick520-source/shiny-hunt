"""Line-delimited JSON bridge. Reuses the game engine without creating a Tk window.
The UI owns this process: closing its input pipe always stops game input.
"""
import json
import os
import queue
import signal
import sys
import threading
import time
import uuid
from app import App, BASE, backend, foreground, geometry, game_candidate, targets, title
import app as _app

def app_geometry(h):
    # Look up through the app module so tests patching app.geometry apply here too.
    return _app.geometry(h)
from mac_backend import Q, info, AppKit, windows, accessibility_trusted
from notifications import Notifier
from pp_recovery import PPProgress, RecoverySequence

import spots


def read_object(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


class NativeEngine(App):
    def __init__(self):
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.stop_event.set()
        backend.stop_event = self.stop_event
        self.hotkey_stop = threading.Event()
        self.worker = None
        self.elapsed = 0
        self.clock = time.monotonic()
        self.mode = '未启动'
        self.next_switch = _app.channel_interval()
        self.rest_until = self.saved_rest = 0
        self.notifier = Notifier(self.say)
        self.settings_file = BASE / 'settings.json'
        self.settings = read_object(self.settings_file)
        self.settings.setdefault('task_mode','frillish')   # 轻飘飘群 is the main feature
        self.pp_progress = PPProgress()
        self.recovery_step = 0
        self.total_rounds = 0
        self.calibrated = None
        self.confirmed = False
        self.preview_revision = 0
        self.permission_status = backend.permissions()
        self.last_reason = ''
        self.protection = None
        self.operation = None
        self.closing = False
        self.emit_native = None
        self.native_waiters = {}
        self.native_lock = threading.Lock()
        threading.Thread(target=self.watch_stop, daemon=True).start()

    @property
    def busy(self):
        return bool(self.worker and self.worker.is_alive())

    def halt(self, text):
        self.last_reason = text
        super().halt(text)

    def preview(self):
        if self.busy:
            raise ValueError('请先停止当前任务')
        self.permission_status = backend.permissions()
        if not Q.CGPreflightScreenCaptureAccess():
            raise ValueError('尚未授予屏幕录制权限。请先点击「检查 / 申请权限」，在系统设置中允许「Mac刷闪助手」录屏，然后退出并重新打开助手。')
        self.confirmed = False
        self.calibrated = None
        self.stop_event.clear()
        self.mode = '准备预览'
        self.operation = 'preview'
        self.say('将在5秒后截取游戏窗口；请切回 PokeMMO')
        def work():
            try:
                for remaining in range(5, 0, -1):
                    self.mode = f'预览倒计时 {remaining} 秒'
                    if self.stop_event.wait(1):
                        return
                h = foreground()
                if not h or not game_candidate(h):
                    raise RuntimeError('没有找到前台 PokeMMO，请打开游戏并在倒计时内切回')
                rect = geometry(h)
                im = backend.capture(h)
                if self.stop_event.is_set():
                    return
                temp = BASE / 'preview.tmp.png'
                im.save(temp)
                temp.replace(BASE / 'preview.png')
                self.bound_window = h
                self.calibrated = (h, rect)
                self.preview_revision += 1
                self.last_reason = '预览完成，请确认画面完整后开始'
                self.say(self.last_reason)
            except Exception as exc:
                self.last_reason = str(exc)
                self.say('预览失败：' + str(exc))
            finally:
                self.stop_event.set()
                self.mode = '已停止'
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def start_native(self, route_test=False):
        if self.busy:
            raise ValueError('当前任务还在运行或正在停止')
        # No preview needed any more: the worker finds and takes over the game window itself.
        self.require_permissions()
        if self.pp_progress.recovering:
            # A route was interrupted earlier. Position is unknown, so start it over (4 teleports to the centre).
            self.pp_progress.recovering = False; self.pp_progress.recovery_index = 0
        automatic_pp = self.pp_setting()
        if not automatic_pp:self.pp_progress.completed = 0   # PP果定点 never goes to the hospital: no 6-round limit
        if self.pp_progress.completed >= self.pp_progress.every and not automatic_pp and not self.pp_progress.recovering:
            raise ValueError('六轮已完成：开启PP恢复，或手动补满PP后重置计数')
        self.task_mode = self.settings.get('task_mode','frillish')   # frillish = 白嫖闪 (spot list), fixed = PP果定点
        self.spot = spots.get(self.settings.get('spot', spots.DEFAULT_SPOT))
        self.route_test = bool(route_test)
        if route_test:self.task_mode = 'frillish'
        self.auto_run = True
        self.pp_run = self.pp_setting() or self.route_test   # hospital route only in 白嫖闪 (user requirement 2026-10-19)
        self.channel_run = self.settings.get('channels', False) and not self.route_test
        # User requirement 2026-10-18: keep the game in front for the whole run (no background input).
        self.background = False
        self.focus_regains = 0; self.assistant_since = None
        self.notifier.new_session()
        self.protection = None
        self.last_reason = ''
        self.stop_event.clear()
        self.mode = '正在启动'
        self.operation = 'run'
        def work():
            try:
                self.attach_game()
                self.run()
            except BrokenPipeError:
                # The pipe to the Swift window is gone: the assistant window was quit or hung.
                self.say('和助手界面的连接断了（界面被关掉或卡住），刷怪已停止；重新打开助手再按开始即可')
            except Exception as exc:
                self.say(f'{exc}，已停止' if '已停止' not in str(exc) else str(exc))
            finally:
                self.stop_event.set()
                if self.mode == '寻找游戏窗口':self.mode = '已停止'
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def game_app(self):
        apps = AppKit.NSWorkspace.sharedWorkspace().runningApplications()
        return next((a for a in apps if 'pokemmo' in str(a.localizedName() or '').lower()), None)

    def attach_game(self):
        # Find the open PokeMMO window, bring it to the front and calibrate from it. Replaces the preview step.
        self.mode = '寻找游戏窗口'
        wins = [h for h, _ in targets()]
        if not wins:
            app = self.game_app()
            if app:
                app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
                for _ in range(20):
                    time.sleep(.15); wins = [h for h, _ in targets()]
                    if wins:break
        if not wins:
            raise RuntimeError('没找到 PokeMMO 游戏窗口：请先打开游戏（窗口模式，内容区 1024×768）')
        # PokeMMO can own several windows (21:49 real run: the first listed one was not the game screen).
        # Use only windows with the 1024x768 content area, prefer the one already bound, then the front one.
        valid = []
        for h in wins:
            try:geometry(h);valid.append(h)
            except RuntimeError:pass
        if not valid:
            geometry(wins[0])   # raises the size message (window mode, 1024x768)
        h = self.bound_window if getattr(self, 'bound_window', None) in valid else valid[0]
        pid = int((info(h) or {}).get('kCGWindowOwnerPID', 0))
        h = self.bring_game_front(pid, h, valid)
        rect = geometry(h)
        im = backend.capture(h)
        try:
            base = self.settings_file.parent   # == BASE, but follows tests' temporary folder
            temp = base / 'preview.tmp.png'; im.save(temp); temp.replace(base / 'preview.png'); self.preview_revision += 1
        except OSError:pass
        self.game_pid = int((info(h) or {}).get('kCGWindowOwnerPID', 0)) or None
        self.bound_window = h; self.calibrated = (h, rect); self.confirmed = True
        self.say('已自动找到游戏窗口并切到前台：' + (title(h) or 'PokeMMO') + '（不需要预览）')

    def game_in_front(self):
        # By process, not window id: PokeMMO may own several windows (21:49 real run).
        pid = getattr(self, 'game_pid', None)
        return self.front_pid() == pid if pid else foreground() == self.hwnd

    def take_game_front(self):
        if self.game_in_front():return self.bound_window
        self.regain_focus_once()
        return self.bound_window if self.game_in_front() else None

    def regain_focus_once(self):
        pid = getattr(self, 'game_pid', None)
        if pid:self.bring_game_front(pid, self.bound_window, [self.bound_window])

    def front_pid(self):
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return front.processIdentifier() if front else None

    def bring_game_front(self, pid, h, valid):
        # 1) ask macOS to activate the game; 2) if that is refused, the Swift app raises it through
        # Accessibility (works even while the assistant is the active app). Then use the game's front window.
        try:
            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app:app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        except Exception:pass
        native_error = ''
        # Only the frontmost APP receives keys. 2026-10-24/25: the game window was the top window but the
        # assistant (where 开始 was clicked) stayed the frontmost app; accepting "top window" skipped the
        # Accessibility raise and 开始 then failed with 未绑定前台游戏 nine times in a row.
        for attempt in range(3):
            deadline = time.monotonic() + (1.0 if attempt == 0 else 1.5)
            while time.monotonic() < deadline:
                if self.front_pid() == pid:
                    fg = foreground()
                    return fg if fg in valid else h
                time.sleep(.05)
            if attempt < 2 and self.emit_native:
                try:self.native_call('activate', pid=pid)
                except RuntimeError as exc:native_error = str(exc)
        # 2026-10-20 real run failed here 5 times with no detail: record exactly what was seen.
        try:trusted = '有' if accessibility_trusted() else '没有'
        except Exception:trusted = '未知'
        front = self.front_pid(); top = self.top_window_pid()
        self.say(f'  切前台失败详情：游戏进程 {pid}，macOS 记录的前台进程 {front}，屏幕最上层窗口的进程 {top}，'
                 f'辅助功能授权：{trusted}，助手拉前台的结果：{native_error or "没有报错"}')
        if trusted == '没有' or '辅助功能' in native_error:
            raise RuntimeError('游戏没能切到前台：「Mac刷闪助手」的辅助功能权限失效了（重新编译后系统可能不认了）。'
                               '请到 系统设置 → 隐私与安全性 → 辅助功能，把「Mac刷闪助手」关掉再打开')
        raise RuntimeError('游戏没能切到前台：请点一下游戏窗口再按开始')

    def top_window_pid(self):
        # Front-to-back window list: owner of the topmost normal window (the assistant's own counts:
        # if it is on top, the game is NOT in front and no key may be sent).
        try:
            for w in windows():
                if w.get('kCGWindowLayer') == 0 and float(w.get('kCGWindowAlpha', 1)) > 0:
                    return int(w.get('kCGWindowOwnerPID'))
        except Exception:pass
        return None

    def regain_focus(self):
        # Keep the game in front: whatever took the focus, give it back to the game and carry on.
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        front_pid = front.processIdentifier() if front else None
        if front_pid == os.getppid():
            # The assistant itself is in front: the user may be about to press 停止. Give them 4 seconds.
            self.release_inputs()
            if self.assistant_since is None:self.assistant_since = time.monotonic()
            while time.monotonic() - self.assistant_since < 4 and not self.stop_event.is_set():
                if self.game_in_front():break
                time.sleep(.2)
            if self.stop_event.is_set():raise RuntimeError('任务已停止')
        self.assistant_since = None
        if self.game_in_front():return
        self.release_inputs()
        last = None
        for attempt in range(3):
            try:
                if info(self.hwnd):
                    pid = int(info(self.hwnd)['kCGWindowOwnerPID'])
                    try:
                        backend.activate(self.hwnd)
                        if not self.game_in_front():raise RuntimeError('未进入前台')
                    except RuntimeError:
                        if self.emit_native:self.native_call('activate', pid=pid)
                        deadline = time.monotonic() + 1.5
                        while time.monotonic() < deadline and not self.game_in_front():time.sleep(.05)
                else:
                    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(getattr(self, 'game_pid', 0) or 0) or self.game_app()
                    if not app:raise RuntimeError('游戏已关闭，已停止')
                    app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and not (info(self.hwnd) and self.game_in_front()):time.sleep(.1)
                if self.game_in_front():
                    self.focus_regains += 1
                    self.say(f'  游戏被切到了后台，已自动切回前台（本次运行第 {self.focus_regains} 次）')
                    return
            except RuntimeError as exc:
                last = exc
                if '已关闭' in str(exc) or '已停止' == str(exc):raise
            time.sleep(.5)
        raise RuntimeError(f'游戏窗口切不回前台（{last or "可能被最小化"}），已停止')

    def say(self, text):
        super().say(text)
        if '已停止' in text or '命中闪光' in text:
            self.last_reason = text

    def require_permissions(self):
        # Keyboard/mouse events are posted by the signed Swift application.
        # The Python child only needs capture and F8 monitoring permissions.
        if not Q.CGPreflightScreenCaptureAccess() or not Q.CGPreflightListenEventAccess():
            raise RuntimeError('缺少屏幕录制或输入监控权限，请检查权限后重新启动')

    def native_call(self, operation, **values):
        if not self.emit_native:
            raise RuntimeError('原生输入通道尚未连接')
        request_id = uuid.uuid4().hex
        waiter = {'event': threading.Event(), 'ok': False, 'message': ''}
        with self.native_lock:
            self.native_waiters[request_id] = waiter
        self.emit_native({'type':'nativeInput','id':request_id,'operation':operation,**values})
        if not waiter['event'].wait(3):
            with self.native_lock:self.native_waiters.pop(request_id,None)
            raise RuntimeError('原生输入超时，已停止')
        if not waiter['ok']:
            raise RuntimeError(waiter['message'] or '原生输入失败，已停止')

    def resolve_native_input(self, data):
        with self.native_lock:
            waiter = self.native_waiters.pop(str(data.get('id','')), None)
        if waiter:
            waiter['ok'] = bool(data.get('ok'))
            waiter['message'] = str(data.get('message',''))
            waiter['event'].set()

    def input_target(self):
        if not getattr(self, 'background', False):
            return {}
        w = info(self.hwnd)
        if not w:
            raise RuntimeError('游戏窗口不可见（最小化或在其他桌面），已停止')
        return {'pid': int(w['kCGWindowOwnerPID']), 'window': int(self.hwnd)}

    def ensure(self):
        if not getattr(self, 'background', False):
            if self.stop_event.is_set():raise RuntimeError('任务已停止')
            if not self.game_in_front():self.regain_focus()
            rect = app_geometry(self.hwnd)
            if self.calibrated != (self.hwnd, rect):
                self.calibrated = (self.hwnd, rect); self.say('  游戏窗口被移动了，已按新位置自动重新定位')
            return rect[:2]
        # Background mode: the game may be behind other apps; only the window itself must stay put.
        if self.stop_event.is_set():raise RuntimeError('任务已停止')
        if not info(self.hwnd):raise RuntimeError('游戏窗口不可见（最小化或在其他桌面），已停止')
        rect = app_geometry(self.hwnd)
        if self.calibrated != (self.hwnd, rect):raise RuntimeError('游戏窗口位置或大小改变，请重新预览')
        return rect[:2]

    def in_foreground(self, action):
        if not getattr(self, 'background', False):
            return action()
        target = self.input_target()
        self.native_call('focusBegin', **target)
        self.background = False
        try:
            return action()
        finally:
            self.background = True
            try:self.native_call('focusEnd')
            except RuntimeError:pass
            self.keep_game_visible(target['pid'])

    def keep_game_visible(self, pid):
        # 20:19 real run: focus went back to the assistant, whose window is on another Space;
        # macOS followed it and the game window went off screen. Bring the game back then.
        for _ in range(4):
            if info(self.hwnd):return
            time.sleep(.25)
        try:
            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app:app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        except Exception:pass
        for _ in range(20):
            if info(self.hwnd):
                self.say('  焦点回到了别的桌面上的程序，已把游戏切回当前桌面');return
            time.sleep(.25)

    def recover_pp(self):
        # PokeMMO ignores held direction keys in the background: run the walk with the game in front.
        return self.in_foreground(super().recover_pp)

    def switch(self):
        # Game menus close when it loses focus, so the whole channel change runs with the game in front.
        return self.in_foreground(super().switch)

    def click(self, rect):
        if not getattr(self, 'background', False):
            return super().click(rect)
        if self.mode=='切换频道':self.check_verification(self.capture())
        point=_app.random_point(self.ensure(),rect)
        # Game ignores background mouse events: Swift raises it briefly, clicks, restores your app.
        self.native_call('focusClick', x=float(point[0]), y=float(point[1]), **self.input_target())
        return point

    def key_event(self, key, down):
        self.native_call('key', key=int(key), down=bool(down), **self.input_target())

    def repeat_event(self, key):
        # The Swift app owns held keys and Accessibility permission; repeat there too.
        self.native_call('repeat', key=int(key), **self.input_target())

    def move_event(self, point):
        self.native_call('move', x=float(point[0]), y=float(point[1]), **self.input_target())

    def mouse_event(self, point, down):
        self.native_call('mouse', x=float(point[0]), y=float(point[1]), down=bool(down), **self.input_target())

    def release_inputs(self):
        if self.emit_native:
            try:self.native_call('release')
            except RuntimeError:pass
        backend.release_all()

    def pp_setting(self):
        # 2026-10-19 user requirements: 轻飘飘白嫖点 = 5 + check + escape, back to the centre every 6 rounds,
        # walk to the shore, surf back. PP果定点 = only 5 + check + escape at a fixed spot, never the hospital.
        return self.settings.get('task_mode', 'frillish') == 'frillish'

    def game_window(self):
        # For the UI: is PokeMMO open, and where (so the assistant can dock right of it). Cached 1.5 s.
        now = time.monotonic()
        cached = getattr(self, '_game_cache', None)
        if cached and now - cached[0] < 1.5:return cached[1]
        found = None
        try:
            wins = [h for h, _ in targets()]
            h = getattr(self, 'hwnd', None) if getattr(self, 'hwnd', None) in wins else (wins[0] if wins else None)
            w = info(h) if h else None
            if w:
                b = w['kCGWindowBounds']
                found = {'frame': [float(b['X']), float(b['Y']), float(b['Width']), float(b['Height'])], 'pid': int(w['kCGWindowOwnerPID'])}
        except Exception:found = None
        self._game_cache = (now, found)
        return found

    def snapshot(self):
        now = time.monotonic()
        if self.busy and self.operation == 'run' and not self.stop_event.is_set():
            self.elapsed += now - self.clock
        self.clock = now
        recovery = self.pp_progress
        notification = read_object(BASE / 'notification.json')
        spot = spots.get(self.settings.get('spot', spots.DEFAULT_SPOT))
        sequence = RecoverySequence(spot)
        index = min(self.recovery_step, len(sequence.labels)-1)
        game = self.game_window()
        return dict(type='state', busy=self.busy, running=self.busy and self.operation == 'run',
                    stopping=self.busy and self.stop_event.is_set(), mode=self.mode,
                    elapsed=int(self.elapsed), rounds=self.total_rounds, ppCompleted=recovery.completed,
                    ppEvery=recovery.every, recovering=recovery.recovering,
                    recoveryIndex=recovery.recovery_index, recoveryTotal=len(sequence.steps),
                    recoveryLabel=sequence.labels[index] if recovery.recovering else '尚未开始恢复',
                    spot=spot['key'], spots=spots.summary(), routeTest=bool(getattr(self, 'route_test', False)),
                    confirmed=True, calibrated=True, previewRevision=self.preview_revision,
                    permissionStatus=self.permission_status, screenAllowed=bool(Q.CGPreflightScreenCaptureAccess()), lastReason=self.last_reason,
                    restRemaining=max(0,int(self.rest_until-now)) if self.rest_until else int(self.saved_rest),
                    channelRemaining=max(0,int(self.next_switch-self.elapsed)),
                    taskMode=str(self.settings.get('task_mode','frillish')),
                    auto=True,
                    ppEnabled=self.pp_setting(),
                    gameFound=bool(game), gameFrame=(game or {}).get('frame', []), gamePid=int((game or {}).get('pid', 0)),
                    protection=str(getattr(self, 'protection', None) or ''),
                    recoveriesTotal=int(getattr(self, 'recovery_count', 0)), focusRegains=int(getattr(self, 'focus_regains', 0)),
                    channels=bool(self.settings.get('channels',False)),
                    notificationEnabled=bool(notification.get('enabled',True)), notificationConfigured=bool(notification.get('miaocode','').strip()))

    def command(self, data):
        action = data.get('action')
        if action == 'native_input_result':
            self.resolve_native_input(data)
            return
        if action == 'stop':
            self.halt('已手动停止')
            return
        if action == 'shutdown':
            self.closing = True
            self.halt('已关闭助手')
            return
        if self.busy:
            raise ValueError('请先停止任务，再修改设置或预览')
        if action == 'start':
            self.start_native()
        elif action == 'test_route':
            self.start_native(route_test=True)
        elif action == 'focus_game':
            game = self.game_window()
            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(game['pid']) if game else self.game_app()
            if not app:raise ValueError('没找到 PokeMMO，请先打开游戏')
            app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        elif action == 'preview':
            self.preview()
        elif action == 'confirm':
            if not self.calibrated:
                raise ValueError('请先获取游戏预览')
            self.confirmed = bool(data.get('value'))
        elif action == 'settings':
            if 'task_mode' in data:
                if data['task_mode'] not in ('fixed','frillish'):
                    raise ValueError('任务类型无效')
                self.settings['task_mode'] = data['task_mode']
            if 'spot' in data:
                if data['spot'] not in [s['key'] for s in spots.SPOTS]:
                    raise ValueError('点位无效')
                self.settings['spot'] = data['spot']
            for key in ('channels','pp_enabled'):
                if key in data:
                    if not isinstance(data[key], bool):
                        raise ValueError('设置值应为开关')
                    self.settings[key] = data[key]
            atomic_json(self.settings_file, self.settings)
        elif action == 'permissions':
            backend.request_permissions()
            self.permission_status = backend.permissions()
            self.say('权限已检查；更改系统授权后可能需要重新启动助手')
        elif action == 'check_permissions':
            self.permission_status = backend.permissions()
        elif action == 'reset_timer':
            self.elapsed = 0
            self.next_switch = _app.channel_interval()
            self.rest_until = self.saved_rest = 0
            self.say('运行计时已清零')
        elif action == 'reset_pp':
            self.pp_progress.finish()
            self.recovery_step = 0
            self.say('已按手动补满PP重置六轮计数及恢复步骤')
        elif action == 'notification':
            config = read_object(BASE / 'notification.json')
            config['enabled'] = bool(data.get('enabled',False))
            if data.get('code'):
                config['miaocode'] = str(data['code']).strip()
            atomic_json(BASE/'notification.json', config)
            os.chmod(BASE/'notification.json',0o600)
            self.say('提醒设置已保存；仅闪光与验证码允许远程提醒')
        elif action == 'test_notification':
            self.test_notification()
        else:
            raise ValueError('无法识别操作')

    def shutdown(self):
        self.hotkey_stop.set()
        self.halt('助手连接已关闭')
        if self.worker:
            self.worker.join(timeout=3)
        if hasattr(self,'ocr'):
            try:
                self.ocr.close()
            except Exception:
                pass
        self.release_inputs()


def main():
    commands = queue.Queue()
    output_lock = threading.Lock()
    def emit(data):
        with output_lock:
            print(json.dumps(data,ensure_ascii=False), flush=True)
    engine = NativeEngine()
    engine.emit_native = emit
    def read_commands():
        try:
            for line in sys.stdin:
                try:
                    data = json.loads(line)
                    if not isinstance(data,dict):
                        raise ValueError('操作格式错误')
                    if data.get('action') == 'native_input_result':
                        engine.resolve_native_input(data)
                    else:commands.put(data)
                except ValueError:
                    commands.put({'action':'invalid'})
        finally:
            commands.put({'action':'shutdown'})
    signal.signal(signal.SIGTERM, lambda *_: commands.put({'action':'shutdown'}))
    signal.signal(signal.SIGINT, lambda *_: commands.put({'action':'shutdown'}))
    threading.Thread(target=read_commands, daemon=True).start()
    try:
        while not engine.closing:
            try:
                command = commands.get(timeout=.15)
                try:
                    engine.command(command)
                    emit({'type':'ack','action':command.get('action')})
                except Exception as exc:
                    emit({'type':'error','message':str(exc)})
            except queue.Empty:
                pass
            while not engine.events.empty():
                emit({'type':'log','message':engine.events.get(),'time':time.strftime('%H:%M:%S')})
            emit(engine.snapshot())
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        engine.shutdown()

if __name__ == '__main__':
    main()
