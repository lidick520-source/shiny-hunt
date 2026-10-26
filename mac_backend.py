"""Native macOS adapters; screen content is normalized to 1024 x 768 points."""
import io
import ctypes
import multiprocessing as mp
import queue
import threading
import time
import re
from PIL import Image
import Quartz as Q
import AppKit
import ScreenCaptureKit as SC
import functools
import objc


def pooled(fn):
    """Run fn inside an Objective-C autorelease pool.
    2026-10-26: the hunting loop runs in plain Python threads, which have no pool. Every capture left an
    autoreleased ~1 MB PNG (plus window lists, events, app objects) behind that was never freed, so the
    engine grew for as long as it ran and the Mac got slower and slower. A pool per call frees them at once."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with objc.autorelease_pool():
            return fn(*args, **kwargs)
    return run


def accessibility_trusted():
    lib = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    return lib.AXIsProcessTrusted()


@pooled
def windows():
    return Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements, 0) or []


def info(h):
    return next((w for w in windows() if w.get('kCGWindowNumber') == h), None)


def game_candidate(h):
    w = info(h)
    return bool(w and 'pokemmo' in (str(w.get('kCGWindowOwnerName', '')) + str(w.get('kCGWindowName', ''))).lower() and w.get('kCGWindowLayer') == 0)


def targets():
    return [(w['kCGWindowNumber'], str(w.get('kCGWindowName', 'PokeMMO'))) for w in windows() if game_candidate(w['kCGWindowNumber'])]


@pooled
def foreground():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app:
        return None
    pid = app.processIdentifier()
    return next((w['kCGWindowNumber'] for w in windows() if w.get('kCGWindowOwnerPID') == pid and w.get('kCGWindowLayer') == 0), None)


def title(h):
    w = info(h) or {}
    return str(w.get('kCGWindowName') or w.get('kCGWindowOwnerName') or '')


def classname(h):
    return 'macOS'


def geometry(h):
    w = info(h)
    if not w:
        raise RuntimeError('游戏窗口已关闭或最小化')
    b = w['kCGWindowBounds']
    width, height = round(b['Width']), round(b['Height'])
    # A standard titled macOS window has the titlebar above its content.
    # This inference must be explicitly confirmed with the preview before input.
    top = height - 768
    if width != 1024 or not 0 <= top <= 40:
        raise RuntimeError(f'窗口为 {width}×{height} 点，请把游戏内容区设为 1024×768，使用窗口模式')
    return (round(b['X']), round(b['Y']) + top, 1024, 768)


class Backend:
    KEYMAP = {0x34: 21, 0x35: 23, 0x36: 22, 0x39: 25, 0x20: 49, 0x57: 13, 0x41: 0, 0x53: 1, 0x44: 2, 0x28: 125, 0x0D: 36, 0x24: 115}

    def __init__(self):
        self.lock = threading.RLock()
        self.keys = set()
        self.stop_event = threading.Event()
        self.mouse_down = None
        self._content = None
        self._content_at = 0

    def permissions(self):
        screen = bool(Q.CGPreflightScreenCaptureAccess())
        access = bool(accessibility_trusted())
        listen = bool(Q.CGPreflightListenEventAccess())
        return f'屏幕录制：{"已授权" if screen else "未授权"}\n辅助功能：{"已授权" if access else "未授权"}\n输入监控（F8）：{"已授权" if listen else "未授权"}'

    def request_permissions(self):
        import ApplicationServices as AS
        import Foundation
        if not Q.CGPreflightScreenCaptureAccess():
            Q.CGRequestScreenCaptureAccess()
        if not accessibility_trusted():
            AS.AXIsProcessTrustedWithOptions(Foundation.NSDictionary.dictionaryWithObject_forKey_(True, AS.kAXTrustedCheckOptionPrompt))
        if not Q.CGPreflightListenEventAccess():
            Q.CGRequestListenEventAccess()

    def require_permissions(self):
        if not Q.CGPreflightScreenCaptureAccess() or not accessibility_trusted() or not Q.CGPreflightListenEventAccess():
            raise RuntimeError('缺少屏幕录制、辅助功能或输入监控权限，请检查权限后重新启动')

    def emergency_pressed(self):
        return bool(Q.CGEventSourceKeyState(Q.kCGEventSourceStateCombinedSessionState, 100))

    def activate(self, h):
        w = info(h)
        if not w or not game_candidate(h):
            raise RuntimeError('已校准的游戏窗口不可用，请重新预览')
        running = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(w['kCGWindowOwnerPID'])
        if not running or not running.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps):
            raise RuntimeError('macOS 未接受游戏前台切换，请手动切回游戏')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.stop_event.is_set():raise RuntimeError('任务已停止')
            if foreground() == h:return
            time.sleep(.03)
        raise RuntimeError('游戏未进入前台，已停止，未发送按键')

    @pooled
    def move(self, point):
        with self.lock:
            if self.stop_event.is_set() or self.emergency_pressed():
                raise RuntimeError('任务已停止')
            if Q.CGWarpMouseCursorPosition(point) != 0:
                raise RuntimeError('无法移动鼠标到游戏按钮')
            event = Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, point, Q.kCGMouseButtonLeft)
            Q.CGEventPost(Q.kCGHIDEventTap, event)

    @pooled
    def key(self, vk, down):
        code = self.KEYMAP.get(vk)
        if code is None: raise RuntimeError(f'未配置的 Mac 按键：{vk!r}')
        with self.lock:
            if down and (self.stop_event.is_set() or self.emergency_pressed()):
                raise RuntimeError('F8紧急停止')
            event = Q.CGEventCreateKeyboardEvent(None, code, down)
            if event is None:
                raise RuntimeError('无法创建键盘事件')
            Q.CGEventPost(Q.kCGHIDEventTap, event)
            if down:
                self.keys.add(code)
            else:
                self.keys.discard(code)

    @pooled
    def repeat_held_key(self, vk):
        """Emit a macOS auto-repeat keyDown for an already held key.

        Some applications ignore a single synthetic keyDown for continuous
        movement. Only PP recovery uses this; regular key presses are unchanged.
        """
        code = self.KEYMAP.get(vk)
        if code is None:
            raise RuntimeError(f'未配置的 Mac 按键：{vk!r}')
        with self.lock:
            if code not in self.keys:
                raise RuntimeError('无法重复未按住的方向键')
            if self.stop_event.is_set() or self.emergency_pressed():
                raise RuntimeError('任务已停止')
            event = Q.CGEventCreateKeyboardEvent(None, code, True)
            if event is None:
                raise RuntimeError('无法创建长按重复事件')
            Q.CGEventSetIntegerValueField(event, Q.kCGKeyboardEventAutorepeat, 1)
            Q.CGEventPost(Q.kCGHIDEventTap, event)

    @pooled
    def mouse(self, point, down):
        with self.lock:
            if down and (self.stop_event.is_set() or self.emergency_pressed()):
                raise RuntimeError('F8紧急停止')
            event = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown if down else Q.kCGEventLeftMouseUp, point, Q.kCGMouseButtonLeft)
            Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, 1)
            Q.CGEventPost(Q.kCGHIDEventTap, event)
            self.mouse_down = point if down else None

    @pooled
    def release_all(self):
        with self.lock:
            for code in list(self.keys):
                Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateKeyboardEvent(None, code, False))
            self.keys.clear()
            if self.mouse_down is not None:
                self.mouse(self.mouse_down, False)

    @staticmethod
    def _await(start, timeout=5):
        result = queue.Queue(maxsize=1)
        def done(value, error):
            result.put((value, error))
        start(done)
        try:
            value, error = result.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError('macOS 截图接口超时')
        if error or value is None:
            raise RuntimeError('macOS 截图失败：' + str(error))
        return value

    @pooled
    def capture(self, h):
        if not Q.CGPreflightScreenCaptureAccess():
            raise RuntimeError('尚未授予屏幕录制权限')
        rect = geometry(h)
        if self._content is None or time.monotonic() - self._content_at > 10:
            self._content = self._await(lambda cb: SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, True, cb))
            self._content_at = time.monotonic()
        window = next((w for w in self._content.windows() if w.windowID() == h), None)
        if window is None:
            self._content = None
            raise RuntimeError('无法捕获指定游戏窗口，请重新预览')
        filt = SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
        config = SC.SCStreamConfiguration.alloc().init()
        frame = window.frame()
        full_w, full_h = round(frame.size.width), round(frame.size.height)
        if full_w != 1024 or not 768 <= full_h <= 808:
            raise RuntimeError('截图窗口尺寸与校准不符')
        config.setWidth_(full_w)
        config.setHeight_(full_h)
        config.setShowsCursor_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        cg = self._await(lambda cb: SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(filt, config, cb))
        rep = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(cg)
        data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, AppKit.NSDictionary.dictionary())
        im = Image.open(io.BytesIO(bytes(data))).convert('RGB')
        if im.size != (full_w, full_h):
            raise RuntimeError('截图尺寸不符，拒绝继续操作')
        if geometry(h) != rect:
            raise RuntimeError('截图期间窗口移动，请重新预览')
        return im.crop((0, full_h - 768, 1024, full_h))


backend = Backend()


def _vision_worker(inbox, outbox):
    import Vision
    import Foundation
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(['zh-Hans', 'en-US'])
    request.setUsesLanguageCorrection_(False)
    while True:
        item = inbox.get()
        if item is None:
            return
        payload, width, height = item
        try:
            with __import__('objc').autorelease_pool():
                data = Foundation.NSData.dataWithBytes_length_(payload, len(payload))
                handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, Foundation.NSDictionary.dictionary())
                ok, error = handler.performRequests_error_([request], None)
                if not ok:
                    raise RuntimeError(str(error))
                lines = []
                for observation in request.results() or []:
                    candidates = observation.topCandidates_(1)
                    if not candidates:
                        continue
                    text = str(candidates[0].string())
                    b = observation.boundingBox()
                    lines.append({'text': text, 'x': b.origin.x * width, 'y': (1 - b.origin.y - b.size.height) * height, 'w': b.size.width * width, 'h': b.size.height * height})
                outbox.put({'ok': True, 'text': re.sub(r'\s+', '', '\n'.join(x['text'] for x in lines)), 'lines': lines})
        except Exception as exc:
            outbox.put({'ok': False, 'error': str(exc)})


class OCR:
    def __init__(self, stop_event=None):
        context = mp.get_context('spawn')
        self.inbox, self.outbox = context.Queue(), context.Queue()
        self.stop_event = stop_event
        self.warmed = False
        self.process = context.Process(target=_vision_worker, args=(self.inbox, self.outbox), daemon=True)
        self.process.start()

    def scan(self, im):
        buffer = io.BytesIO()
        im.save(buffer, format='PNG', compress_level=1)
        self.inbox.put((buffer.getvalue(), *im.size))
        until = time.monotonic() + (12 if self.warmed else 60)
        while time.monotonic() < until:
            if self.stop_event and self.stop_event.is_set():
                raise RuntimeError('任务已停止')
            try:
                result = self.outbox.get(timeout=.05)
            except queue.Empty:
                if not self.process.is_alive():
                    raise RuntimeError('Apple Vision OCR 进程已退出')
                continue
            if not result['ok']:
                raise RuntimeError('OCR失败：' + result.get('error', ''))
            self.warmed = True
            return result
        self.close()
        raise RuntimeError('OCR超时，已停止')

    def read(self, im):
        return self.scan(im)['text']

    def close(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=1)
        for q in (self.inbox, self.outbox):
            q.close()
            q.cancel_join_thread()
