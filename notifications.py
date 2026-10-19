"""Notifications run only after the caller has stopped game input."""
import json
import pathlib
import threading
import time
import subprocess
import urllib.request
import urllib.parse
BASE = pathlib.Path(__file__).resolve().parent

class Notifier:
    def __init__(self, log, transport=None, sleeper=time.sleep, local=True):
        self.log = log
        self.transport = transport or urllib.request.urlopen
        self.sleeper = sleeper
        self.local = local
        self.lock = threading.Lock()
        self.seen = set()
        self.session = str(time.time_ns())
        self.queue_lock = threading.Lock()
        self.last_attempt = 0.0

    def new_session(self):
        self.session = str(time.time_ns())

    def send(self, kind, message, event_id=None):
        token = (self.session, event_id or kind)
        with self.lock:
            if token in self.seen:
                return None
            self.seen.add(token)
        thread = threading.Thread(target=self._deliver, args=(kind, message), daemon=True)
        thread.start()
        return thread

    def _deliver(self, kind, message):
        if self.local:
            try:
                # argv avoids interpolation into AppleScript source.
                script = 'on run argv\ndisplay notification (item 1 of argv) with title "Mac刷闪助手" sound name "Glass"\nend run'
                subprocess.run(['/usr/bin/osascript', '-e', script, message], timeout=5, capture_output=True)
                subprocess.Popen(['/usr/bin/afplay', '/System/Library/Sounds/Glass.aiff'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                self.log('本地通知调用失败；请查看停止原因')
        if kind not in ('验证', '闪光'):
            self.log(kind+'：仅本地提示，不触发喵提醒或电话');return
        try:
            config = json.loads((BASE/'notification.json').read_text())
            code = config.get('miaocode', '').strip()
            if not config.get('enabled', True) or not code:
                self.log('远程通知未启用或未填写喵码');return
        except (OSError, ValueError):
            self.log('无法读取通知配置');return
        params = urllib.parse.urlencode({'id': code, 'text': f'【{kind}】{message}', 'type': 'json'})
        url = 'https://miaotixing.com/trigger?' + params
        # Serialize sends; one retry only. Timeouts may mean delivered but ACK lost.
        with self.queue_lock:
            for attempt in range(2):
                try:
                    delay = max(0, 15 - (time.monotonic() - self.last_attempt))
                    if delay:self.sleeper(delay)
                    self.last_attempt = time.monotonic()
                    with self.transport(url, timeout=5) as response:
                        data = json.loads(response.read(65536).decode('utf-8'))
                    if str(data.get('code')) == '0':
                        self.log(kind+'通知：服务端已接受（不代表手机已读）');return
                    self.log(kind+'通知被服务端拒绝，错误码：'+str(data.get('code')));return
                except Exception:
                    if attempt == 0:
                        self.log(kind+'通知未确认送达，15秒后重试一次（可能重复送达）')
                    else:self.log(kind+'通知失败；程序保持停止，请检查网络和喵提醒设置')
