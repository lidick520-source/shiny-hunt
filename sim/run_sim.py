"""Drive the assistant's real code against the fake game; never touches PokeMMO.

usage: run_sim.py [--live] [scenario ...]
  (off the Mac: PYTHONPATH=tools/stubs python3 sim/run_sim.py)
  --live   use the Mac's Apple Vision OCR (otherwise replay OCR recorded from the real frames)
"""
import json, os, shutil, sys, tempfile, threading, time, pathlib
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = pathlib.Path(os.environ.get('SHINY_PROJECT', HERE.parent))   # the project folder this sim/ lives in
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(PROJECT))
from fake_game import FakeGame

SCENARIOS = {
    'normal':      dict(rounds=14, faults=(), expect='run', note='14轮：两次去医院、冲浪回点，逃跑点击全部落在按钮内'),
    'drop_space':  dict(rounds=7,  faults=('drop_space_after_heal',), expect='run', note='治疗后一下空格游戏没收到（14:32 的真实故障）'),
    'channel_switch': dict(rounds=4, faults=(), expect='run', channels=True, note='到点换频道：点书本图标→更换频道→选下一个频道→变更，看到 Ch.3（17:56 曾点进 PvP 菜单）'),
    'missed_msg':  dict(rounds=8,  faults=('missed_escape_msg',), expect='run', note='Mac 实测：成功逃脱提示太短没读到、地图上有其他玩家挡住参考图，也要认出已回到地图'),
    'drop_surf':   dict(rounds=8,  faults=('drop_surf_space',), expect='run', note='20:05 实测：确认冲浪的空格没按上，提示一直开着；要自己补按'),
    'blind_list':  dict(rounds=4,  faults=('blind_list',), channels=True, expect='run', note='20:19 实测：频道列表展开了但一行字都没读到；按量好的行位置选'),
    'focus_steal': dict(rounds=10, faults=('focus_steal',), expect='run', note='运行中你点了别的程序 4 次：自动把游戏切回前台继续刷，不停止'),
    'interruptions': dict(rounds=12, faults=('interruptions',), expect='run', note='弹出锦标赛窗口、冒出是/否提示、去医院后停在街上：都自己恢复，不停止'),
    'ppberry':     dict(rounds=14, faults=(), mode='fixed', expect='run', note='PP果定点：只按5和逃跑，14轮不回院、不按4、不走路'),
    'walk':        dict(rounds=12, faults=('walk','escape_blocked'), mode='walk', expect='run', note='走路单遇：草丛里左右来回走、遇怪查闪逃跑；没逃掉时自己再点，不按5、不回院'),
    'short_walk':  dict(rounds=8,  faults=('short_walk',), expect='run', note='最后一段没走到岸边（11:50 的真实故障）：补按一下D自己贴到岸边，继续刷'),
}


class ReplayOCR:
    cache = json.loads((HERE / 'ocr_cache.json').read_text())
    def __init__(self, stop_event=None): pass
    blind_list = False
    def scan(self, im):
        time.sleep(float(os.environ.get('SIM_OCR_DELAY', '0')))
        data = self.cache[im.info['sim']]
        if self.blind_list and im.info['sim'] == 'ch_list.png':   # 20:19 real run: no list rows read
            lines = [l for l in data['lines'] if l['y'] < 100]; data = {'text': ''.join(l['text'] for l in lines), 'lines': lines}
        return data
    def read(self, im): return ''
    def close(self): pass


def replay_read(self, rect):
    # Text of the recorded OCR lines whose centre lies inside rect (replay mode only).
    im = self.capture(); x, y, w, h = rect
    out = [l['text'] for l in ReplayOCR.cache[im.info['sim']]['lines']
           if x <= l['x'] + l['w'] / 2 <= x + w and y <= l['y'] + l['h'] / 2 <= y + h]
    return ' '.join(out)


class Silent:
    def __getattr__(self, name): return lambda *a, **k: None


def run(name, live):
    cfg = SCENARIOS[name]
    import app, native_bridge as bridge
    game = FakeGame(cfg['faults'], seed=hash(name) % 1000)
    ReplayOCR.blind_list = 'blind_list' in cfg['faults']
    work = pathlib.Path(tempfile.mkdtemp(prefix='shiny-sim-'))
    shutil.copytree(PROJECT / 'assets', work / 'assets')
    app.LOG_FILE = work / 'run.log'
    patches = [patch.object(app, 'BASE', work), patch.object(bridge, 'BASE', work),
               patch.object(app, 'targets', lambda: [(77, 'PokeMMO')]), patch.object(app, 'foreground', game.foreground),
               patch.object(bridge, 'targets', lambda: [(77, 'PokeMMO')]), patch.object(bridge, 'foreground', game.foreground),
               patch.object(bridge, 'geometry', lambda h: (0, 0, 1024, 768)), patch.object(bridge, 'title', lambda h: 'PokeMMO'),
               patch.object(bridge.AppKit.NSWorkspace, 'sharedWorkspace', create=True,
                            new=lambda: type('W', (), {'frontmostApplication': lambda self: type('A', (), {'processIdentifier': lambda self: 4321 if game.front else 555})()})()),
               patch.object(app, 'title', lambda h: 'PokeMMO'), patch.object(app, 'classname', lambda h: 'sim'),
               patch.object(app, 'geometry', lambda h: (0, 0, 1024, 768)),
               patch.object(bridge, 'info', lambda h: {'kCGWindowOwnerPID': 4321}),
               patch.object(app.backend, 'activate', lambda h: game.activate()), patch.object(app.backend, 'capture', lambda h: game.capture()),
               patch.object(app.backend, 'permissions', lambda: 'sim'),
               patch.object(bridge.NativeEngine, 'require_permissions', lambda self: None),
               patch.object(bridge.NativeEngine, 'watch_stop', lambda self: None),
               # the 3-8 minute rest after a channel switch becomes 2 seconds in the simulation
               patch.object(app.random, 'randint', lambda a, b, _r=__import__('random').randint: 2 if (a, b) == (180, 480) else _r(a, b))]
    if not live:
        patches.append(patch.object(app, 'OCR', ReplayOCR)); patches.append(patch.object(app.App, 'read', replay_read))
    for p in patches: p.start()
    try:
        engine = bridge.NativeEngine()
        engine.notifier = Silent()                       # never send a real notification
        engine.native_call = game.native
        engine.background = False; engine.focus_regains = 0; engine.assistant_since = None   # as start_native: game kept in front
        engine.task_mode = cfg.get('mode', 'frillish'); engine.pp_run = engine.task_mode == 'frillish'; engine.channel_run = cfg.get('channels', False); engine.next_switch = 0 if cfg.get('channels') else 3000; engine.auto_run = True
        engine.stop_event.clear()
        def go():
            engine.attach_game(); engine.run()      # exactly what 开始 does: find the game itself, no preview
        worker = threading.Thread(target=go, daemon=True); worker.start()
        started = time.monotonic(); limit = 40 * cfg['rounds'] + 120
        while worker.is_alive() and time.monotonic() - started < limit:
            if engine.total_rounds >= cfg['rounds'] and game.state == 'map':
                engine.stop_event.set()
            time.sleep(.2)
        engine.stop_event.set(); worker.join(10)
        log = (work / 'run.log').read_text() if (work / 'run.log').exists() else ''
        stopped_by_error = any(k in log.splitlines()[-1] for k in ('已停止', '超时', '失败')) if log.strip() else True
        last = log.strip().splitlines()[-2:] if log.strip() else []
        inside = all(355 <= x <= 555 and 537 <= y <= 582 for x, y, s in game.clicks if s == 'menu')
        spread = len({(x, y) for x, y, s in game.clicks if s == 'menu'})
        ok = not game.problems and game.focus_depth == 0 and inside
        if cfg['expect'] == 'run':
            extra = 1 if 'yes_no' in game.popped else 0          # the injected 是/否 prompt answered 是 = one extra surf
            if cfg.get('mode') == 'walk':
                ok = ok and engine.total_rounds >= cfg['rounds'] and game.heals == 0 and game.teleports == 0 and game.walk_steps > 0 and game.blocked >= 2
            elif cfg.get('mode') == 'fixed':
                ok = ok and engine.total_rounds >= cfg['rounds'] and game.heals == 0 and game.surfs == 0 and game.teleports == 0
            else:
                ok = ok and engine.total_rounds >= cfg['rounds'] and game.heals >= cfg['rounds'] // 6 and game.surfs == game.heals + extra
            if 'drop_space_after_heal' in cfg['faults']: ok = ok and '补按空格' in log
            if 'focus_steal' in cfg['faults']: ok = ok and game.steals >= 3 and '已自动切回前台' in log
            if 'interruptions' in cfg['faults']: ok = ok and len(game.popped) == 3 and log.count('开始自动恢复') >= 2
            if cfg.get('channels'):
                want = 2 if 'blind_list' in cfg['faults'] else 3
                ok = ok and game.switches == 1 and game.channel == want and f'已切到频道{want}' in log
        else:
            ok = ok and engine.total_rounds < cfg['rounds'] and not worker.is_alive()
        result = dict(scenario=name, passed=ok, note=cfg['note'], rounds=engine.total_rounds, escapes=game.escapes,
                      heals=game.heals, surfs=game.surfs, clicks=len(game.clicks), distinct_click_points=spread,
                      all_clicks_inside_escape=inside, problems=game.problems, last_log=last,
                      seconds=round(time.monotonic() - started))
        (HERE / f'sim_{name}.log').write_text(log + '\n----- 游戏端 -----\n' + '\n'.join(game.log))
        return result
    finally:
        for p in reversed(patches): p.stop()


def main():
    live = '--live' in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or list(SCENARIOS)
    results = [run(n, live) for n in names]
    report = {'ocr': 'Apple Vision 实时识别' if live else '回放真实帧的识别结果', 'results': results,
              'all_passed': all(r['passed'] for r in results)}
    (HERE / ('sim_report_live.json' if live else 'sim_report.json')).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for r in results:
        print(('通过' if r['passed'] else '失败'), r['scenario'], r['rounds'], '轮', r['problems'][:3], r['last_log'])
    print('ALL', report['all_passed'])


if __name__ == '__main__':
    main()
