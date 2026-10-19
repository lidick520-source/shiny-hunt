"""Experimental Mac PP recovery, uses validated user keystroke sequence.
In-session checkpoint state only: not saved across application restarts.
"""
import random
from dataclasses import dataclass

# Last leg: hold right longer than needed; the water edge stops the character exactly at the shore.
# The extra short D push closes any gap left by the long walk (simulated fault short_walk);
# at the shore it does nothing because water blocks the way.
DEFAULT_ROUTE = [('S',2.0,2.5),('D',1.03,1.08),('W',.26,.30),('D',2.5,2.6),('D',.5,.7)]
KEY_CODES={'S':0x53,'D':0x44,'W':0x57,'A':0x41}
KEY_NAMES={0x34:'4',0x20:'空格',0x53:'S',0x44:'D',0x57:'W',0x41:'A'}
DIRECTIONS={0x53:'向下',0x44:'向右',0x57:'向上',0x41:'向左'}
# What each step of the default hospital trip does (shown in the log and the UI).
STEP_LABELS=['按4瞬移回宝可梦中心','等待传送到达','按空格和护士搭话','等待护士说话','按空格确认治疗','等待治疗完成',
 '按空格结束对话（确认对话框已关闭）','等待对话框消失','走出医院：长按S向下','停顿','长按D向右','停顿','长按W向上','停顿',
 '长按D向右走到水边','停顿','再补按D贴到岸边（已在岸边则不动）','停顿','面向水面按空格（冲浪提示）','等待冲浪提示','按空格确认冲浪','等待下水回到刷怪点']

def describe(ix, step):
    label=STEP_LABELS[ix] if ix<len(STEP_LABELS) else step[0]
    kind=step[0]
    if kind=='wait':return f'{label}（{step[1]:.1f}秒）'
    if kind in('press','close_dialog'):return f'{label}（按键 {KEY_NAMES.get(step[1],hex(step[1]))}）'
    return label

@dataclass
class PPProgress:
    every:int=6
    completed:int=0
    recovery_index:int=0
    recovering:bool=False
    def encounter_done(self):
        if self.recovering: raise RuntimeError('恢复PP期间不能计数遇怪')
        self.completed += 1
        return self.completed>=self.every
    def begin(self):
        self.recovering=True
    def finish(self):
        self.recovering=False; self.recovery_index=0;self.completed=0

class RecoverySequence:
    def __init__(self, config=None):
        config=config or {}
        route=config.get('route', DEFAULT_ROUTE)
        self.steps=[('press',0x34),('wait',5.5)]
        for i in range(3):
            self.steps += [('press' if i<2 else 'close_dialog',0x20),('wait',2.5 if i==0 else 5.5 if i==1 else .7)]
        for key,low,high in route:
            if key not in KEY_CODES or not 0<float(low)<=float(high)<=10:
                raise ValueError('返回路线参数无效')
            self.steps += [('hold',KEY_CODES[key],float(low),float(high)),('wait',.3)]
        self.steps += [('press',0x20),('wait',1.5),('press',0x20),('wait',5.5)]
    def execute(self, host, progress, *, auto_finish=True):
        progress.begin()
        for ix in range(progress.recovery_index,len(self.steps)):
            step=self.steps[ix];host.recovery_step=ix
            if step[0]=='hold':
                seconds=random.uniform(step[2],step[3])
                host.say(f'去医院 {ix+1}/{len(self.steps)}：{describe(ix,step)}（{DIRECTIONS.get(step[1],"")} {seconds:.2f}秒）')
            else:
                host.say(f'去医院 {ix+1}/{len(self.steps)}：{describe(ix,step)}')
            if ix==2 and hasattr(host,'remember_dialog_reference'):host.remember_dialog_reference()
            if step[0]=='press':host.press(step[1])
            elif step[0]=='close_dialog':
                (host.close_dialog if hasattr(host,'close_dialog') else host.press)(step[1])
            elif step[0]=='wait':host.wait(step[1])
            else:host.hold_key(step[1],seconds)
            if hasattr(host,'route_snapshot'):host.route_snapshot(ix,STEP_LABELS[ix] if ix<len(STEP_LABELS) else step[0])
            progress.recovery_index=ix+1
        if auto_finish:
            progress.finish()
