"""Mac fixed-location helper; migrated from the supplied Windows loop fix."""
import json, pathlib, queue, random, re, threading, time
import numpy as np
from PIL import Image
from mac_backend import backend, OCR, foreground, targets, title, geometry, game_candidate, classname
from notifications import Notifier
from pp_recovery import PPProgress,RecoverySequence
from snapshot_policy import save as save_snapshot,trim_log
import screen_state
import spots
BASE=pathlib.Path(__file__).resolve().parent
LOG_FILE=BASE/'run.log'  # tests redirect this so they never write the real run log
INCIDENT_DIR=BASE/'diagnostics'/'incidents'   # tests redirect this, like LOG_FILE
def normalized(a,b):
 a=a-a.mean();b=b-b.mean();d=np.sqrt((a*a).sum()*(b*b).sum())
 return float((a*b).sum()/d) if d else 0
def escape_button_area(r):
 # OCR finds only the word 逃跑; the clickable button is ~200x45 around it
 # (title on top, 从战斗中逃走 below).  Use most of the button, not the word.
 x,y,w,h=r;cx=x+w/2
 return (int(cx-90),y+2,180,38)
def window_point(point,calibrated):
 # Log click positions relative to the game window, e.g. (465,558).
 try:return f'窗口内 {point[0]-calibrated[1][0]},{point[1]-calibrated[1][1]}'
 except Exception:return str(point)
def random_point(origin,rect):
 # A different spot inside the target every click, away from the edges.
 ox,oy=origin;rx,ry,rw,rh=rect;mx=max(1,rw//10);my=max(1,rh//8)
 return (ox+random.randint(rx+mx,rx+rw-mx),oy+random.randint(ry+my,ry+rh-my))
def escape_region(data):
 text=data.get('text','')
 if not any(word in text for word in ['战斗','背包','使用道具','选择技能','宝可梦']):return None
 for line in data.get('lines',[]):
  label=re.sub(r'\s+','',line['text'])
  if '逃跑' in label and 200<line['y']<720:
   x=int(line['x']);y=int(line['y']);w=max(1,int(line['w']));h=max(1,int(line['h']))
   # Click the button actually located by OCR.  The previous Mac port replaced
   # the normal battle-menu result with a hard-coded rectangle.
   return (max(0,x-10),max(0,y-6),w+20,h+12)
 return None
# Restrict shiny OCR to the upper wild-Pokemon name area; never scan our own lower status panel.
ORIGINAL_FLASH_REGIONS=((145,65,725,175),(145,65,725,175))
def original_flash_match(text69,text130,escape_present):
 return bool(escape_present and any('闪光' in re.sub(r'\s+','',t) for t in (text69,text130)))
def notification_kind(reason,protection=None,was_stopped=False):
 if protection in ('验证','闪光'):return protection
 if '验证码' in reason:return '验证'
 if was_stopped or reason.startswith('监测模式：'):return None
 if reason.startswith('原规则命中闪光') or reason.startswith('点击前原闪光规则命中'):return '闪光'
 return '异常'
def frillish_count(text):
 # Vision occasionally reads the last character as 期/漂.  Three recognized
 # names is sufficient evidence for the five-member Frillish horde.
 return len(re.findall(r'轻飘[飘期漂]',re.sub(r'\s+','',text or '')))
def map_surface_scores(im):
 # These strips are black outside the recorded battle viewport, colored on map.
 return tuple(float((np.asarray(im.crop(rect)).max(axis=2)>24).mean()) for rect in [(20,180,115,600),(160,630,780,700)])
DIALOG_WHITE=.2
CHANNEL_SWITCH_VERIFIED=True  # positions checked on real screenshots 2026-10-18 18:14-18:19
SOCIAL_ICON=(810,740,16,16)            # 3rd icon from the left, bottom-right bar (book): 好友/公会/更换频道
CHANNEL_DROPDOWN=(446,292,132,20)      # "频道N -- 当前频道" dropdown
CHANNEL_DROPDOWN_READ=(438,290,148,26)
CHANNEL_CHANGE_BUTTON=(446,363,132,44) # 变更
CHANNEL_COUNT=5
CHANNEL_CANCEL_BUTTON=(446,429,132,44) # 取消 (438-586, 423-480 on the real screenshot)
TOURNAMENT_CLOSE=(845,181,8,8)         # × of the 锦标赛报名 window (real screenshot)
PVP_ICON=(837,740,16,16)               # 4th bottom-right icon (pokeball)
# 「更换频道」 row of the book menu: the same place on every real screenshot (帆巴市 2026-10-18, 琉璃市 2026-10-23).
SOCIAL_CHANGE_CHANNEL=(778,647,82,20)
# Empty game-world spot left of the player. 2026-10-23 琉璃市: after the book icon was clicked to close its
# menu, the icon kept keyboard focus, so the nurse's 空格 reopened the menu and opened 好友 instead.
# A click on the world hands the keyboard back to the game.
WORLD_FOCUS=(40,420,10,10)
# User confirmed 2026-10-18 21:16: clicking the same icon again closes its popup menu (book and pokeball).
# Never auto-recover from these: protection stops, user stops, setup problems.
def channel_interval():
 # User 2026-10-18: change channel at a random moment, every 40-50 minutes (not a fixed 50).
 return random.randint(2400,3000)
NO_RECOVERY=('闪光','验证','手动停止','F8','已关闭','缺少屏幕录制','尚未授予','窗口为','原生输入','六次遭遇完成','轻飘飘全自动','未确认是','路线测试','游戏已关闭','切不回前台','没找到 PokeMMO','自动恢复')
def CHANNEL_ROW(n):return (446,338+27*(n-1)-9,132,18)   # 频道1 y=338, step 27 (real screenshots)
CHANNEL_WINDOW_BOX=(424,322,600,488)   # checkbox + 变更/取消, cut from the real screenshot (assets/channel_window.png)
# 2026-10-23 琉璃市: right after a change the window shows a 「下次切换: N秒后」 row on top and everything
# below it sits 11 px lower (变更 greyed out). Same window, so it is found with that offset too.
CHANNEL_COOLDOWN_DY=11
def channel_window_match(im):
 """(score, dy): dy=0 normal window, dy≈CHANNEL_COOLDOWN_DY the cooldown layout."""
 try:
  ref=np.asarray(Image.open(BASE/'assets'/'channel_window.png').convert('L')).astype(float)
  g=im.convert('L')
 except Exception:return 0.0,0
 x0,y0,x1,y1=CHANNEL_WINDOW_BOX;b=ref-ref.mean();best=(-1.0,0)
 for dx in (-2,0,2):
  for dy in (-2,0,2,CHANNEL_COOLDOWN_DY-2,CHANNEL_COOLDOWN_DY,CHANNEL_COOLDOWN_DY+2):
   a=np.asarray(g.crop((x0+dx,y0+dy,x1+dx,y1+dy))).astype(float)
   if a.shape!=ref.shape:continue
   a=a-a.mean();sc=float((a*b).sum()/np.sqrt((a*a).sum()*(b*b).sum()+1e-9))
   if sc>best[0]:best=(sc,dy)
 return best[0],(CHANNEL_COOLDOWN_DY if best[1]>5 else 0)   # buttons only move with the layout, not the ±2 jitter
def channel_window_score(im):
 return channel_window_match(im)[0]
def shifted(rect,dy):
 return (rect[0],rect[1]+dy)+tuple(rect[2:])
def hud_channel(text):
 m=re.search(r'Ch\.?\s*([1-9])',str(text or ''));return int(m.group(1)) if m else None
_CN_VARIANTS=str.maketrans({'換':'换','頻':'频','當':'当','變':'变','選':'选'})
def cn_norm(text):
 return re.sub(r'\s+','',str(text or '')).translate(_CN_VARIANTS)
def dialog_bubble_white(im):
 # PokeMMO NPC dialogue is a white speech bubble at the TOP of the screen.
 # Real frames 2026-10-18: bubble open 53-57% white, closed <1%.
 r=np.asarray(im.crop((140,15,900,72)).convert('RGB'))
 return float((r.min(axis=2)>235).mean())
def hud_only_map_text(data):
 # Map screen OCR: location HUD (channel/money) present, no battle words.
 # Black transition frames have no HUD, so they never count.
 text=data.get('text','')
 if not re.search(r'Ch\.?\s*\d|\$\s*[\d,]{3,}',text):return False
 return not re.search(r'Lv\.?\s*\d+|成功逃脱|野生怪群出现|派出了|使出了|战斗|逃跑|背包|使用道具|选择技能',text)
def map_return_evidence(im,data,button,map_confirmed=False):
 # Colored borders are not evidence of map state: battle frames also contain them.
 modal=any(word in data.get('text','') for word in ['变更','取消','验证码','是否'])
 battle=bool(re.search(r'Lv\.?\s*\d+|成功逃脱|野生怪群出现|派出了|战斗|逃跑',data.get('text','')))
 return bool(map_confirmed and not button and not modal and not battle)
class Detector:
 def __init__(self):
  self.defs={'map_tree':(450,90,140,100),'map_bridge':(410,430,200,125),'escape_text':(437,544,40,19),'battle_menu':(145,483,200,45)}
  self.t={}
  for k in self.defs:
   local=BASE/'assets'/('local_'+k+'.png');source=local if k.startswith('map_') and local.exists() else BASE/'assets'/(k+'.png')
   self.t[k]=np.asarray(Image.open(source).convert('L'),dtype=float)
  self.original_escape=np.asarray(Image.open(BASE/'assets'/'original_escape.png').convert('L'),dtype=float)
 def original_escape_score(self,im,hint=None):
  # Search around the OCR position instead of assuming the escape button is at
  # one fixed coordinate.  The wide fallback is used only by offline tests.
  if hint:
   x,y,w,h=hint;box=(max(0,x-24),max(0,y-16),min(im.width,x+w+24),min(im.height,y+h+16))
  else:box=(350,500,600,620)
  a=np.asarray(im.crop(box).convert('L'),dtype=float);th,tw=self.original_escape.shape
  if a.shape[0]<th or a.shape[1]<tw:return -1
  return max(normalized(a[y:y+th,x:x+tw],self.original_escape)
             for y in range(a.shape[0]-th+1) for x in range(a.shape[1]-tw+1))
 def set_map_reference(self,first,second):
  # Learn stationary, textured map patches from this run rather than a named map.
  a=np.asarray(first.convert('L'),dtype=float);b=np.asarray(second.convert('L'),dtype=float);candidates=[]
  for y in [120,240,360,480,600]:
   for x in [70,210,350,490,630,750]:
    patch=a[y:y+72,x:x+96];other=b[y:y+72,x:x+96]
    if patch.std()<8:continue
    similarity=normalized(patch,other);candidates.append((similarity*min(patch.std(),45),(x,y,96,72),other))
  candidates.sort(key=lambda c:c[0],reverse=True);chosen=[]
  for candidate in candidates:
   x,y,_,_=candidate[1]
   if all(abs(x-c[1][0])+abs(y-c[1][1])>=240 for c in chosen):chosen.append(candidate)
   if len(chosen)==2:break
  if len(chosen)<2:
   chosen=[(0,(70,120,96,72),b[120:192,70:166]),(0,(630,480,96,72),b[480:552,630:726])]
  for name,(_,rect,patch) in zip(['map_tree','map_bridge'],chosen):self.defs[name]=rect;self.t[name]=patch.copy()
 def score(self,im,k):
  x,y,w,h=self.defs[k];a=np.asarray(im.crop((x-12,y-12,x+w+12,y+h+12)).convert('L'),dtype=float);best=-1
  for dy in range(-12,13,4):
   for dx in range(-12,13,4):
    b=a[12+dy:12+dy+h,12+dx:12+dx+w]
    if b.shape==self.t[k].shape:best=max(best,normalized(b,self.t[k]))
  return best
 def map(self,im):return self.score(im,'map_tree')>.92 and self.score(im,'map_bridge')>.92
 def menu(self,im):return self.score(im,'escape_text')>.92 and self.score(im,'battle_menu')>.92
class App:
 def __init__(self):
  global tk, ttk, messagebox, ImageTk
  import tkinter as tk
  from tkinter import ttk, messagebox
  from PIL import ImageTk
  self.root=tk.Tk();self.root.title('Mac 刷闪助手');self.root.geometry('690x760');self.root.resizable(True,True)
  self.events=queue.Queue();self.stop_event=threading.Event();self.worker=None;self.elapsed=0;self.clock=time.monotonic();self.mode='未启动';self.next_switch=channel_interval();self.rest_until=0;self.saved_rest=0
  self.notifier=Notifier(self.say)
  self.settings_file=BASE/'settings.json'
  try:self.settings=json.loads(self.settings_file.read_text())
  except (OSError,ValueError):self.settings={}
  self.pp_enabled=tk.BooleanVar(value=self.settings.get('pp_enabled',False));self.pp_progress=PPProgress();self.total_rounds=0
  self.auto=tk.BooleanVar(value=True);self.channels=tk.BooleanVar(value=self.settings.get('channels',False))
  self.calibrated=None;self.preview_image=None
  self.hotkey_stop=threading.Event()
  threading.Thread(target=self.watch_stop,daemon=True).start()
  style=ttk.Style(self.root)
  try:style.theme_use('clam')
  except tk.TclError:pass
  style.configure('TButton',padding=(13,8),font=('Helvetica',12))
  style.configure('TCheckbutton',font=('Helvetica',11),padding=5)
  style.configure('TLabel',font=('Helvetica',12))
  box=ttk.Frame(self.root,padding=20);box.pack(fill='both',expand=True)
  ttk.Label(box,text='Shiny Hunt  ·  Mac 2.0  |  甜甜香气 5  |  F8 停止',font=('Helvetica',14,'bold')).pack(anchor='w')
  self.time_label=ttk.Label(box,text='累计运行 00:00:00',font=('Helvetica',18));self.time_label.pack(anchor='w',pady=10)
  self.count_label=ttk.Label(box,text='换频道：40–50分钟随机');self.count_label.pack(anchor='w')
  self.pp_label=ttk.Label(box,text='遇怪 0 次  ·  PP 0/6');self.pp_label.pack(anchor='w',pady=4)
  self.state_label=ttk.Label(box,text='状态：未启动');self.state_label.pack(anchor='w',pady=8)
  ttk.Checkbutton(box,text='每40–50分钟随机换到下一频道，成功后随机休息3–8分钟',variable=self.channels).pack(anchor='w',pady=4)
  ttk.Checkbutton(box,text='6 次后自动瞬移 → 护士恢复 → 返回 → 冲浪（测试）',variable=self.pp_enabled).pack(anchor='w',pady=4)
  ttk.Label(box,text='固定点位 · 游戏内容区 1024×768 · 首次先预览并测试\n实验性 PP 恢复：默认关闭，确认配置后再开启',wraplength=590).pack(anchor='w',pady=8)
  ttk.Label(box,text='窗口内容区：1024×768；开始前请先预览并确认截图。').pack(anchor='w')
  setup=ttk.Frame(box);setup.pack(anchor='w',pady=5)
  ttk.Button(setup,text='检查 / 申请权限',command=self.permissions).pack(side='left',padx=3)
  ttk.Button(setup,text='5秒后预览游戏',command=self.preview).pack(side='left',padx=3)
  ttk.Button(setup,text='测试本地提示',command=self.test_notification).pack(side='left',padx=3)
  self.confirmed=tk.BooleanVar(value=False)
  ttk.Checkbutton(box,text='预览完整显示游戏内容，没有标题栏或裁切',variable=self.confirmed).pack(anchor='w')
  self.preview_label=ttk.Label(box,text='尚未预览');self.preview_label.pack(anchor='w',pady=4)
  buttons=ttk.Frame(box);buttons.pack(anchor='w',pady=6)
  ttk.Button(buttons,text='开始 / 继续（自动切回游戏）',command=self.start).pack(side='left',padx=3)
  ttk.Button(buttons,text='停止（F8）',command=lambda:self.halt('已手动停止')).pack(side='left',padx=3)
  ttk.Button(buttons,text='清零计时',command=self.reset).pack(side='left',padx=3)
  self.log=tk.Text(box,height=10,width=73,state='disabled');self.log.pack(fill='both',expand=True,pady=6)
  self.root.protocol('WM_DELETE_WINDOW',self.close);self.root.after(150,self.refresh)
 def say(self,text):
  self.events.put(text)
  try:
   trim_log(LOG_FILE)
   with LOG_FILE.open('a') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+text+'\n')
  except OSError:pass
 def watch_stop(self):
  while not self.hotkey_stop.wait(.05):
   if backend.emergency_pressed():self.halt('F8紧急停止')
 def permissions(self):
  backend.request_permissions()
  status=backend.permissions()
  (BASE/'permission-status.txt').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+'\n'+status)
  messagebox.showinfo('权限检查',status+'\n如未授权，请在系统设置 → 隐私与安全性中授权启动程序/终端，然后重新启动助手。')
 def preview(self):
  if self.worker and self.worker.is_alive():return
  self.confirmed.set(False);self.mode='等待预览'
  self.say('5秒内切回游戏；预览不发送输入')
  def work():
   try:
    time.sleep(5);h=foreground()
    if not h or not game_candidate(h):raise RuntimeError('请切到 PokeMMO 游戏窗口')
    rect=geometry(h);im=backend.capture(h)
    self.events.put(('preview',h,rect,im))
   except Exception as e:self.say('预览失败：'+str(e))
   finally:self.mode='已停止'
  self.worker=threading.Thread(target=work,daemon=True);self.worker.start()
 def test_notification(self):
  self.notifier.send('测试','Mac 刷闪助手本地提示测试（不打电话）',str(time.time()))
 def check_verification(self,im,data=None):
  data=data if data is not None else self.ocr.scan(im)
  if '验证码' in data['text']:
   self.protection='验证';self.stop_event.set();self.diagnostic(im,data,'verification');raise RuntimeError('检测到验证码，已停止，请人工处理')
  return data
 def halt(self,text):
  already=self.stop_event.is_set();self.stop_event.set();self.release_inputs()
  if not already:self.say(text)
 def reset(self):
  if self.worker and self.worker.is_alive():messagebox.showinfo('计时','请先停止后再清零');return
  self.elapsed=0;self.next_switch=channel_interval();self.rest_until=0;self.saved_rest=0
 def start(self):
  if self.worker and self.worker.is_alive():return
  if not self.calibrated or not self.confirmed.get():messagebox.showinfo('需要预览','请先预览并确认游戏截图区域');return
  self.settings['pp_enabled']=self.pp_enabled.get();self.settings['channels']=self.channels.get();self.settings_file.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2))
  self.notifier.new_session();self.protection=None;backend.stop_event=self.stop_event
  self.auto_run=True;self.pp_run=self.pp_enabled.get();self.channel_run=self.channels.get();self.stop_event.clear();self.worker=threading.Thread(target=self.run,daemon=True);self.worker.start()
 def wait(self,seconds):
  until=time.monotonic()+seconds
  while time.monotonic()<until:
   if self.stop_event.wait(min(.25,max(0,until-time.monotonic()))):raise RuntimeError('任务已停止')
   if self.mode=='切换频道' and seconds>2 and hasattr(self,'ocr'):self.check_verification(self.capture())
 def take_game_front(self):
  backend.activate(self.bound_window)
  choices=targets();fg=foreground();bound=getattr(self,'bound_window',None)
  return bound if bound and bound==fg else next((h for h,t in choices if h==fg),None)
 def ensure(self):
  if self.stop_event.is_set():raise RuntimeError('任务已停止')
  if foreground()!=self.hwnd:raise RuntimeError('游戏失去前台，已停止；切回游戏后点继续')
  rect=geometry(self.hwnd)
  if self.calibrated!=(self.hwnd,rect):raise RuntimeError('游戏窗口位置或大小改变，请重新预览')
  return rect[:2]
 def capture(self):
  self.ensure();return backend.capture(self.hwnd)
 def press(self,key,virtual=False):
  if self.mode=='切换频道':self.check_verification(self.capture())
  self.ensure();self.key_event(key,True)
  try:self.wait(random.uniform(.09,.17) if key in (0x34,0x35) else .12)
  finally:self.key_event(key,False)
 def click(self,rect):
  if self.mode=='切换频道':self.check_verification(self.capture())
  point=random_point(self.ensure(),rect)
  self.ensure();self.move_event(point);self.wait(.04)
  self.ensure();self.mouse_event(point,True)
  try:self.wait(random.uniform(.08,.14))
  finally:self.mouse_event(point,False)
  return point
 def slow_click(self,rect):
  # 2026-10-27 21:59 龙穴: three quick clicks on 逃跑 were ignored (menu stayed). Last try: rest the
  # pointer on the button, nudge it, then a longer press - what a person's click looks like.
  if self.mode=='切换频道':self.check_verification(self.capture())
  point=random_point(self.ensure(),rect)
  self.ensure();self.move_event((point[0]-3,point[1]-2));self.wait(.2)
  self.ensure();self.move_event(point);self.wait(.3)
  self.ensure();self.mouse_event(point,True)
  try:self.wait(random.uniform(.22,.3))
  finally:self.mouse_event(point,False)
  return point
 def key_event(self,key,down):backend.key(key,down)
 def repeat_event(self,key):backend.repeat_held_key(key)
 def move_event(self,point):backend.move(point)
 def mouse_event(self,point,down):backend.mouse(point,down)
 def release_inputs(self):backend.release_all()
 def require_permissions(self):backend.require_permissions()
 def read(self,rect):
  x,y,w,h=rect;return self.ocr.read(self.capture().crop((x,y,x+w,y+h)).resize((w*2,h*2)))
 def diagnostic(self,im,data,state):
  folder=BASE/'diagnostics';folder.mkdir(exist_ok=True);im.save(folder/'latest-screen.png',compress_level=1)
  category='shiny' if data.get('original_flash_rule',{}).get('matched') else ('error' if state in ('verification','protected') else 'normal')
  save_snapshot(BASE/'snapshots',im,category)
  if state=='verification' or data.get('original_flash_rule',{}).get('matched'):
   stamp=time.strftime('%Y%m%d-%H%M%S');im.save(folder/(stamp+'-event.png'))
   (folder/(stamp+'-event.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
  (folder/'latest-ocr.json').write_text(json.dumps({'state':state,'time':time.strftime('%Y-%m-%d %H:%M:%S'),**data},ensure_ascii=False,indent=2),encoding='utf8')
 def original_flash_guard(self,im,button=None):
  score=self.det.original_escape_score(im,button);image_hit=score>=.8;texts=['','']
  if image_hit:
   for i,(x,y,w,h) in enumerate(ORIGINAL_FLASH_REGIONS):texts[i]=self.ocr.read(im.crop((x,y,x+w,y+h)).resize((w*2,h*2)))
  if original_flash_match(*texts,image_hit):self.protection='闪光';self.stop_event.set();self.release_inputs()
  return {'region69':texts[0],'region130':texts[1],'escape_image_score':round(score,4),'escape_image_hit':image_hit,'matched':original_flash_match(*texts,image_hit)}
 @staticmethod
 def battle_evidence(data):
  text=data.get('text','')
  return bool(re.search(r'Lv\.?\s*\d+|野生怪群出现|派出了|成功逃脱|战斗|逃跑|使出了',text))
 def hold_key(self,key,seconds):
  moved=self._hold_once(key,seconds)
  if moved is None:return
  if moved<12:
   # 2026-10-23 22:01: once right after the nurse a full S hold did nothing (no dialog on screen).
   # Give the game the keyboard again and try the same walk once more before calling it a failure.
   self.say(f'  这次长按没走动（画面差异 {moved:.1f}），切回游戏再按一次');self.ensure();self.wait(.8)
   moved=self._hold_once(key,seconds)
  if moved<12:  # real walks measured 38-170; a blocked walk (open dialogue) only 3-5
   raise RuntimeError(f'长按方向键后人物没有走动（画面差异 {moved:.1f}，正常应大于12）；可能对话框没关或被挡住，已停止')
  self.say(f'  人物已走动（画面差异 {moved:.1f}）')
 def _hold_once(self,key,seconds):
  # macOS: one synthetic keyDown may not auto-repeat in PokeMMO.
  # Keep the key held and feed repeat events at a bounded cadence.
  before=self.capture().crop((200,140,820,610)).resize((155,118)) if seconds>=.8 else None
  self.ensure();self.key_event(key,True)
  started=time.monotonic()
  try:
   while True:
    remaining=seconds-(time.monotonic()-started)
    if remaining<=0:break
    self.wait(min(.08,remaining))
    if time.monotonic()-started<seconds:
     self.ensure()
     self.repeat_event(key)
  finally:self.key_event(key,False)
  if before is None:return None
  # Verify the playfield visibly changed; no assumption that an event post equals movement.
  from PIL import ImageChops, ImageStat
  after=self.capture().crop((200,140,820,610)).resize((155,118))
  return max(ImageStat.Stat(ImageChops.difference(before,after)).mean)
 def remember_dialog_reference(self):
  # Arrival frame at the nurse, before talking: used to tell whether the dialogue box is gone.
  self.dialog_reference=self.capture()
 def dialog_open(self,im):
  white=dialog_bubble_white(im)
  return white>DIALOG_WHITE,f'顶部对话气泡白色占比 {white:.0%}'
 def close_dialog(self,key):
  self.press(key)
  for attempt in range(4):
   self.wait(.8);im=self.capture();still,why=self.dialog_open(im)
   if not still:
    self.say(f'  治疗对话已结束（{why}）');return
   if attempt==3:break
   self.say(f'  对话框还没关（{why}），补按空格 {attempt+1}/3');self.press(key)
  self.diagnostic(im,{'text':''},'dialog-stuck')
  raise RuntimeError('治疗后补按3次空格对话框仍未关闭，已停止，请看 diagnostics/latest-screen.png')
 def wait_scene(self,limit):
  # Wait until the map is drawn again after a door fade (playfield no longer near-black), then let the fade finish.
  from PIL import ImageStat
  started=time.monotonic()
  while time.monotonic()-started<limit:
   bright=max(ImageStat.Stat(self.capture().crop((200,140,820,610)).convert('L').resize((62,47))).mean)
   if bright>25:
    self.wait(.35);self.say(f'  已出门，画面恢复（用时 {time.monotonic()-started:.1f} 秒）');return
   self.wait(.15)
  self.say(f'  {limit:.0f} 秒后画面仍是黑的，按原路线继续')
 def route_snapshot(self,ix,label):
  try:
   folder=BASE/'diagnostics'/'hospital-route';folder.mkdir(parents=True,exist_ok=True)
   if ix==0:
    for old in folder.glob('*.png'):old.unlink()
   self.capture().save(folder/f'{ix+1:02d}-{label}.png',compress_level=1)
  except Exception:pass
 def current_spot(self):
  return getattr(self,'spot',None) or spots.get(spots.DEFAULT_SPOT)
 def recover_pp(self):
  spot=self.current_spot()
  self.mode='补充PP';self.say(f'PP 已用 6 轮，开始去医院补满 PP（{spot["name"]}）')
  RecoverySequence(spot).execute(self,self.pp_progress,auto_finish=False)
  # User choice: after the route, continue hunting automatically.
  # Only stop when the screen is clearly still in a battle/menu; a wrong position
  # is caught by the next round's own encounter timeout.
  deadline=time.monotonic()+6;surf_retries=0
  while True:
   check=self.capture();data=self.check_verification(check)
   if '冲浪' in data.get('text','') and surf_retries<3:
    # 20:05 real run: the confirm space was not received and 要使用冲浪吗？是/否 stayed open.
    surf_retries+=1;self.say(f'  冲浪提示还开着，补按空格确认冲浪（{surf_retries}/3）')
    self.press(0x20);self.wait(5.5);deadline=time.monotonic()+6;continue
   on_map=self.det.map(check);blocked=bool(self.battle_evidence(data) or escape_region(data))
   if (on_map and not blocked) or time.monotonic()>=deadline:break
   self.wait(.5)
  self.diagnostic(check,data,'pp-return')
  if not on_map and not blocked:
   kind,_=self.classify_screen(check,data)
   if kind=='outdoor' and spot.get('spot_water') is not None:
    # Route ended somewhere without the water around: not the hunting spot. Self-recovery takes it from here.
    raise RuntimeError(f'去医院路线走完后不在刷怪点（周围水面 {screen_state.water_ratio(check):.0%}）')
  if '冲浪' in data.get('text',''):
   raise RuntimeError('补按3次空格后冲浪提示仍在，已停止，请手动检查')
  if blocked:
   raise RuntimeError('去医院路线走完后画面仍在战斗或菜单中，已停止，请手动检查')
  self.pp_progress.finish();self.mode='刷怪'
  self.say('去医院补PP完成，已回到刷怪点，计数清零，继续刷怪'+('' if on_map else '（未匹配到起点参考图，按路线结果继续；若下一轮没进战斗会超时停止）'))
 def blocking_ui(self,im,data):
  # A window or prompt covering the spot (tournament, menus, 是/否 ...): never press 5 into it.
  try:
   kind,_=self.classify_screen(im,data)
   return kind if kind in ('yes_no','dialog','channel_window','channel_list','tournament','social_menu','pvp_menu','pc_hall') else None
  except Exception:return None
 def recoverable(self,e):
  # Self-recovery instead of stopping, but never after a protection stop (shiny / captcha) or a user stop.
  # Only game situations (RuntimeError) are recovered; a program error must stop at once.
  if not isinstance(e,RuntimeError):return False
  if getattr(self,'route_test',False):return False   # a route test must show where the route goes wrong
  msg=str(e)
  if self.stop_event.is_set() or getattr(self,'protection',None):return False
  if any(word in msg for word in NO_RECOVERY):return False
  now=time.monotonic();self.recoveries=[t for t in getattr(self,'recoveries',[]) if now-t<600]
  if len(self.recoveries)>=3:
   self.say('10分钟内已经自动恢复了3次，问题反复出现，不再自动恢复');return False
  self.recoveries.append(now);return True
 def incident(self,reason):
  # Keep evidence of every problem in its own folder (screenshot + recent log), newest 15 kept.
  try:
   root=INCIDENT_DIR;folder=root/time.strftime('%Y%m%d-%H%M%S');folder.mkdir(parents=True,exist_ok=True)
   self.capture().save(folder/'screen.png',compress_level=1)
   tail=LOG_FILE.read_text(errors='ignore').splitlines()[-60:] if LOG_FILE.exists() else []
   (folder/'log.txt').write_text('\n'.join(tail+['问题：'+str(reason)]),encoding='utf8')
   for old in sorted(p for p in root.iterdir() if p.is_dir())[:-15]:
    for f in old.iterdir():f.unlink()
    old.rmdir()
  except Exception:pass
 def recover_from(self,e):
  self.mode='自动恢复';self.release_inputs();self.incident(e);self.recovery_count=getattr(self,'recovery_count',0)+1
  self.say(f'出现问题：{e}');self.say('不停止，开始自动恢复：先看清现在是什么画面')
  if '频道' in str(e):self.next_switch=self.elapsed+600;self.say('  换频道这次没成功，10分钟后再试')
  return self.recover_screen()
 def classify_screen(self,im,data):
  try:
   # The spot is on the water: other players often cover the learned map patches, the water is still there.
   need=self.current_spot().get('spot_water')
   water=getattr(self,'task_mode','frillish')=='frillish' and need is not None and screen_state.water_ratio(im)>=need
   on_spot=(bool(self.det.map(im)) or water) and not self.battle_evidence(data)
   return screen_state.classify(im,data,on_spot=on_spot,menu=bool(escape_region(data)) or bool(self.det.menu(im)),
                                channel_window=channel_window_score(im),dialog_white=dialog_bubble_white(im))
  except Exception:
   return ('battle_menu' if escape_region(data) else 'unknown'),{}
 def recover_screen(self,first=False,seen=None):
  # Look at the screen, do the one safe action for what is there, look again. Battles go back to the
  # normal loop (it does the shiny checks). first=True: at start only close windows, the start point is here.
  last=None;same=0;route_done=False;closed_popup=False;probed=False
  for attempt in range(1,11):
   if self.stop_event.is_set():raise RuntimeError('任务已停止')
   im,data=seen if (seen and attempt==1) else (None,None)
   if im is None:im=self.capture();data=self.check_verification(im)
   kind,scores=self.classify_screen(im,data)
   # At start only open windows/prompts are closed; a battle was already handled by the OCR check before.
   if first and kind not in ('yes_no','dialog','channel_window','channel_list','tournament','social_menu','pvp_menu'):return 'start'
   label=screen_state.LABELS[kind];same=same+1 if kind==last else 1;last=kind
   if not first or kind not in ('spot','outdoor'):self.say(f'  自动恢复 {attempt}：现在是「{label}」')
   if same>=3 and kind not in ('battle','loading','unknown'):
    raise RuntimeError(f'自动恢复：「{label}」处理了3次还在，已停止')
   if kind in ('battle_menu','battle'):
    self.say('  在战斗中：交回正常流程（会先检查闪光再逃跑）');return 'battle'
   if kind=='spot':
    self.say('  已在刷怪点');return 'start' if first else 'press5'
   if first and kind=='outdoor':return 'start'
   if kind=='yes_no':self.say('  按空格选「是」');self.press(0x20);self.wait(3)
   elif kind=='dialog':self.say('  按空格关闭对话');self.press(0x20);self.wait(1)
   elif kind=='channel_window':
    try:dy=channel_window_match(im)[1]
    except Exception:dy=0
    self.say('  点「取消」关闭换频道窗口'+('（冷却中的样式）' if dy>5 else ''))
    self.click(shifted(CHANNEL_CANCEL_BUTTON,dy));self.wait(.5);self.defocus_ui();closed_popup=True
   elif kind=='channel_list':self.say('  点下拉框收起频道列表');self.click(CHANNEL_DROPDOWN);self.wait(.8)
   elif kind=='tournament':self.say('  点 × 关闭锦标赛报名窗口');self.click(TOURNAMENT_CLOSE);self.wait(.5);self.defocus_ui();closed_popup=True
   elif kind=='social_menu':self.say('  再点一次书本图标收起菜单');self.click(SOCIAL_ICON);self.wait(.5);self.defocus_ui();closed_popup=True
   elif kind=='pvp_menu':self.say('  再点一次精灵球图标收起菜单');self.click(PVP_ICON);self.wait(.5);self.defocus_ui();closed_popup=True
   elif kind=='loading':self.wait(1.5)
   elif kind=='unknown' and attempt<=3:self.wait(1.5)
   elif kind=='pc_hall' and not self.pp_run:
    if first:return 'start'
    raise RuntimeError('自动恢复：PP果定点模式下人在宝可梦中心里，这个模式不会自己走回点位，已停止')
   elif kind=='pc_hall':
    if first:return 'start'
    self.say('  在宝可梦中心里：按住S走出门')
    try:self.hold_key(0x53,3.0)
    except RuntimeError as exc:self.say(f'  {exc}')
    self.wait(1.5)
   else:   # outdoor somewhere else, or still unrecognised: the hospital route is the known way back
    if first:return 'start'
    if kind=='outdoor' and closed_popup and not probed:
     # A popup opened over the spot does not move the player; crowded water spots (琉璃市) defeat the
     # map reference, so try 5 once before walking the whole route. No battle -> the normal loop raises
     # and the next recovery walks the route.
     probed=True;self.say('  刚收起弹窗，人应该还在原地：先按 5 试一次');return 'press5'
    if not self.pp_run:
     if kind in ('unknown','outdoor') and not probed:
      # PP果定点 has no map of its spot: a crowd or a channel change can make the spot look foreign.
      # The player rarely moves on his own, so try 5 once; no battle -> the next recovery stops.
      probed=True;self.say('  PP果定点：先按 5 试一次，进了战斗就说明还在原地');return 'press5'
     if kind=='unknown' or kind=='outdoor':
      raise RuntimeError(f'自动恢复：PP果定点模式下不在原来的点位（{screen_state.LABELS[kind]}），这个模式不会自己走回去，已停止')
    if route_done:raise RuntimeError('自动恢复：已经重走过一次去医院的路线，还是没回到刷怪点，已停止')
    route_done=True;self.say('  不在刷怪点：按4回宝可梦中心，从头走一遍路线（顺便补满PP）')
    self.pp_progress.recovering=False;self.pp_progress.recovery_index=0
    try:self.recover_pp()
    except RuntimeError as exc:
     if self.stop_event.is_set() or any(word in str(exc) for word in NO_RECOVERY):raise
     self.say(f'  路线中出现问题：{exc}；继续看画面')
     continue
    return 'press5'
  raise RuntimeError('自动恢复：10步之后仍没回到刷怪点，已停止')
 def defocus_ui(self):
  # Hand the keyboard back to the game after clicking a HUD control (see WORLD_FOCUS).
  self.click(WORLD_FOCUS);self.wait(.3)
 def switch_snapshot(self,step,label):
  try:
   folder=BASE/'diagnostics'/'channel-switch';folder.mkdir(parents=True,exist_ok=True)
   if step==1:
    for old in folder.glob('*.png'):old.unlink()
   self.capture().save(folder/f'{step:02d}-{label}.png',compress_level=1)
  except Exception:pass
 def switch(self):
  try:return self._switch()
  except RuntimeError:
   # Whatever went wrong, the next attempt is 10 minutes away (2026-10-23: a failure at 23:05:37 was
   # retried 17 s later into the cooldown window).
   self.next_switch=self.elapsed+600;raise
 def open_channel_window(self,log=None):
  """Book icon -> 更换频道. Returns (score, dy) of the channel window."""
  def lines():return self.check_verification(self.capture()).get('lines',[])
  def find():
   for l in lines():
    if re.search(r'更换频道',cn_norm(l['text'])) and 560<=l['y']<=720:return (int(l['x'])-8,int(l['y'])-4,int(l['w'])+16,int(l['h'])+8)
   return None
  self.click(SOCIAL_ICON);self.wait(.7)
  if log:log('点右下角第3个图标（书本：好友/公会/更换频道）')
  item=find()
  if not item:self.wait(.5);item=find()
  if not item and screen_state.matches(self.capture(),'social_menu'):
   # 2026-10-23 琉璃市: OCR missed the menu text over the dark water 6 times in a row, the menu was open.
   item=SOCIAL_CHANGE_CHANNEL;self.say('  菜单文字没读到，但书本菜单确实开着：按固定位置点「更换频道」')
  if not item:raise RuntimeError('右下角菜单里没找到「更换频道」，已停止，截图在 diagnostics/channel-switch')
  self.click(item);self.wait(1)
  if log:log('点「更换频道」')
  return channel_window_match(self.capture())
 def confirm_switched(self,target):
  """The HUD's channel digit can sit under the hotbar (long map names such as 琉璃市(水)(Sootopolis City)).
  Reopen the window: its dropdown names the current channel, and the cooldown row only exists right after a change."""
  score,dy=self.open_channel_window()
  if score<.8:raise RuntimeError(f'切换后重新打开换频道窗口失败（相似度 {score:.2f}），无法确认是否切到频道{target}，已停止')
  current=cn_norm(self.read(shifted(CHANNEL_DROPDOWN_READ,dy)));m=re.search(r'频道([1-9])',current)
  self.click(shifted(CHANNEL_CANCEL_BUTTON,dy));self.wait(.6);self.defocus_ui()
  if m and int(m.group(1))==target:return f'窗口显示当前是频道{target}'
  if m:raise RuntimeError(f'切换后窗口显示当前是频道{m.group(1)}，不是频道{target}，已停止')
  if dy>5:return '窗口出现了换频道冷却（刚换成功才会有）'
  raise RuntimeError(f'切换后左上角看不到频道号，窗口里也读不出当前频道，无法确认是否切到频道{target}，已停止')
 def _switch(self):
  if not CHANNEL_SWITCH_VERIFIED:
   self.next_switch=self.elapsed+channel_interval()
   self.say('换频道时间到：点击位置还没核实，本次跳过换频道，继续刷');return
  self.mode='切换频道';self.say('换频道时间到（40–50分钟随机），地图就绪，准备切换频道')
  step=[0]
  def log(label):
   step[0]+=1;self.say(f'换频道 {step[0]}：{label}');self.switch_snapshot(step[0],label)
  def lines():
   return self.check_verification(self.capture()).get('lines',[])
  def find(pattern,y0,y1):
   for l in lines():
    t=cn_norm(l['text'])
    if re.search(pattern,t) and y0<=l['y']<=y1:return t,(int(l['x'])-8,int(l['y'])-4,int(l['w'])+16,int(l['h'])+8)
   return None,None
  log('当前画面')
  hud_before=hud_channel(self.read((0,0,330,30)))
  # Positions verified on real screenshots 2026-10-18 18:14-18:19 (game window coordinates).
  score,dy=self.open_channel_window(log)
  if score>=.8 and dy>5:
   # Cooldown layout: a change happened moments ago (e.g. confirmed late). Close it and wait for the next turn.
   self.click(shifted(CHANNEL_CANCEL_BUTTON,dy));self.wait(.6);self.defocus_ui()
   self.next_switch=self.elapsed+600;self.say('  换频道窗口在冷却中（刚换过频道），已关掉，10 分钟后再换');return
  if score<.8:raise RuntimeError(f'没看到换频道窗口（和真实截图的相似度 {score:.2f}），已停止，截图在 diagnostics/channel-switch')
  current=cn_norm(self.read(CHANNEL_DROPDOWN_READ));m=re.search(r'频道([1-9])',current)
  old=hud_before or (int(m.group(1)) if m else None)
  if not old:raise RuntimeError(f'读不出当前频道（左上角和下拉框都没读到），已停止')
  self.say(f'  换频道窗口已确认（相似度 {score:.2f}），当前频道：{old}')
  cooldown=self.read((438,273,148,18));n=re.search(r'(\d+)秒',cooldown)
  if n and int(n.group(1))>0:self.say(f'  频道冷却 {n.group(1)} 秒，等待');self.wait(min(int(n.group(1))+2,120))
  self.click(CHANNEL_DROPDOWN);self.wait(.6);log('点开频道下拉列表')
  options={};listed_current=None
  for l in lines():
   t=cn_norm(l['text']);mm=re.match(r'频道([1-9])',t)
   if not mm or not 322<=l['y']<=520:continue
   if '当前' in t:listed_current=int(mm.group(1));continue   # never click the row of the channel we are on
   options[int(mm.group(1))]=(int(l['x'])-10,int(l['y'])-4,int(l['w'])+20,int(l['h'])+8)
  if listed_current and listed_current!=old:self.say(f'  列表标明当前是频道{listed_current}（之前读到{old}），以列表为准');old=listed_current
  options.pop(old,None)
  if not options:
   # 20:19 real run: the list was open but OCR returned no rows. Use the row positions measured on
   # the real screenshots, only when the image shows the list covering the window's buttons.
   if channel_window_score(self.capture())>.5:raise RuntimeError('点下拉框后频道列表没有展开，已停止，截图在 diagnostics/channel-switch')
   options={n:CHANNEL_ROW(n) for n in range(1,CHANNEL_COUNT+1) if n!=old}
   self.say(f'  列表文字没读到，按真实截图量好的行位置选择（共{CHANNEL_COUNT}个频道）')
  ordered=sorted(options);target=next((c for c in ordered if c>old),ordered[0])
  self.click(options[target]);self.wait(.6);log(f'在列表里点「频道{target}」')
  chosen=cn_norm(self.read(CHANNEL_DROPDOWN_READ));picked=re.search(r'频道([1-9])',chosen)
  if picked and int(picked.group(1))!=target:raise RuntimeError(f'下拉框显示的是{picked.group(0)}，不是频道{target}，已停止')
  if channel_window_score(self.capture())<.8:raise RuntimeError('选完频道后换频道窗口不见了，已停止，截图在 diagnostics/channel-switch')
  if not picked:self.say(f'  下拉框文字没读到，窗口仍在；点变更后用左上角 Ch.{target} 最终确认')
  self.click(CHANNEL_CHANGE_BUTTON);self.wait(3);log(f'点「变更」切到频道{target}')
  deadline=time.monotonic()+10;how=None
  while True:
   hud=self.read((0,0,330,30))
   if re.search(r'Ch\.?\s*'+str(target)+r'(?!\d)',hud):how=f'左上角已显示 Ch.{target}';break
   if time.monotonic()>deadline:break
   self.wait(.8)
  if how:log(how);self.defocus_ui()
  else:
   self.say(f'  左上角没读到 Ch.{target}（读到：{hud.strip() or "空"}，频道号可能被快捷栏挡住），重新打开换频道窗口确认')
   how=self.confirm_switched(target);log(f'已确认：{how}')
  gap=channel_interval();self.next_switch=self.elapsed+gap;self.rest_until=time.monotonic()+random.randint(180,480)
  self.say(f'已切到频道{target}，随机休息至倒计时结束；下次换频道在 {gap//60} 分钟后')
 def run(self):
  try:
   self.mode='切换到游戏';self.say('正在将已校准的游戏切到前台；F8停止')
   self.hwnd=self.take_game_front()
   if not self.hwnd:
    choices=targets()
    listed='；'.join(t or classname(h) for h,t in choices) or '没有找到候选窗口'
    raise RuntimeError('未绑定前台游戏。请重新预览并切回游戏，然后开始。候选：'+listed)
   self.say('已连接：'+title(self.hwnd)+' / '+classname(self.hwnd))
   geometry(self.hwnd);self.det=Detector();self.ocr=OCR(self.stop_event);self.require_permissions();stable=0;last_diagnostic=0
   self.mode='OCR预热与地图检查'
   reference=self.capture();initial=self.check_verification(reference);resume='start'
   if escape_region(initial):resume='battle';self.say('启动时在战斗中：先按正常流程检查闪光并逃跑，回到地图后再记录起点')
   else:
    resume=self.recover_screen(first=True,seen=(reference,initial))
    reference=self.capture();self.wait(.4);self.det.set_map_reference(reference,self.capture())
    self.say('OCR预热完成，已记录当前起点画面')
   while True:
    try:
     state='ready';partial_since=None;deadline=time.monotonic()+45;last_combat_log=0;key_sent=time.monotonic();key_retry=False;rounds=0;return_log=0;last_escape_click=0;escape_retries=0;escape_success=False;hud_frames=0
     if resume=='battle':
      state='battle';key_retry=True;deadline=time.monotonic()+55
     elif resume=='press5':
      self.press(0x35);self.say('已回到刷怪点，发送5继续刷');state='battle';key_sent=time.monotonic();deadline=time.monotonic()+55
     else:
      if getattr(self,'route_test',False):
       # 「测试回院路线」: walk the hospital route once for a new spot, screenshot every step, then stop.
       self.pp_progress.recovering=False;self.pp_progress.recovery_index=0
       self.recover_pp();self.say('路线测试完成：去医院、回点位都走完了，每一步截图在 diagnostics/hospital-route');self.halt('路线测试完成')
      elif self.saved_rest:self.rest_until=time.monotonic()+self.saved_rest;self.saved_rest=0
      elif self.pp_progress.recovering or (self.pp_run and self.pp_progress.completed>=self.pp_progress.every):
       self.say('本次会话存在未完成 PP 恢复，从记录的步骤继续')
       self.recover_pp()
       self.press(0x35);self.say('PP恢复后已发送5，等待战斗');state='battle';key_sent=time.monotonic();deadline=time.monotonic()+55
      elif self.channel_run and self.elapsed>=self.next_switch:self.switch();deadline=time.monotonic()+45
      else:
       self.press(0x35);self.say('已发送5，等待进入战斗');state='battle';key_sent=time.monotonic();key_retry=False;deadline=time.monotonic()+55
     while not self.stop_event.is_set():
      self.ensure()
      if self.rest_until>time.monotonic():
       self.mode='休息';self.check_verification(self.capture());self.wait(1);continue
      if self.rest_until:
       deadline=time.monotonic()+45
       # 2026-10-26 21:41: after a channel change other players stand around the spot, the start reference
       # (two patches of the map) no longer matched (0.81 < 0.92) and the run stopped. The player has not
       # moved: if the screen is a plain map, learn the reference again from it.
       first=self.capture();data=self.check_verification(first)
       if not self.battle_evidence(data) and not self.blocking_ui(first,data) and not self.det.map(first):
        self.wait(.4);self.det.set_map_reference(first,self.capture());self.say('换完频道周围的人变了：按当前画面重新记录起点')
      self.rest_until=0;self.mode='刷怪';im=self.capture();data=self.check_verification(im);on_map=self.det.map(im)
      if state=='ready':
       if time.monotonic()-last_diagnostic>=5:
        tree=self.det.score(im,'map_tree');bridge=self.det.score(im,'map_bridge');self.say(f'确认返回本轮起点：参考区1={tree:.3f} / 参考区2={bridge:.3f}');last_diagnostic=time.monotonic()
       stable=stable+1 if (on_map and not self.battle_evidence(data)) else 0
       if not on_map and not self.battle_evidence(data):
        # 2026-10-27 21:03: one reference patch at 0.99, the other 0.80-0.85 for 45 s (a player stood on it)
        # -> timeout. Same place, so after 8 s of "half match" on a plain map, learn the reference again.
        half=max(self.det.score(im,'map_tree'),self.det.score(im,'map_bridge'))>=.95
        partial_since=(partial_since or time.monotonic()) if half else None
        if half and time.monotonic()-partial_since>=8 and not self.blocking_ui(im,data):
         self.wait(.3);self.det.set_map_reference(im,self.capture());partial_since=None
         self.say('起点有一块被别的玩家挡住了：按当前画面重新记录起点')
       else:partial_since=None
       if stable>=2:
        ui=self.blocking_ui(im,data)
        if ui:raise RuntimeError(f'刷怪点被「{screen_state.LABELS[ui]}」挡住了，没有按5')
        if self.channel_run and self.elapsed>=self.next_switch:self.switch();stable=0;deadline=time.monotonic()+45;continue
        self.press(0x35);self.say('已按5，等待进入战斗');state='battle';stable=0;key_sent=time.monotonic();key_retry=False;deadline=time.monotonic()+55
      elif state=='battle':
       button=escape_region(data)
       if '验证码' in data['text']:
        self.diagnostic(im,data,'protected');raise RuntimeError('检测到验证码文字，已停止，请检查画面')
       if time.monotonic()-last_combat_log>=4:
        self.diagnostic(im,data,'battle');self.say(('已找到逃跑文字位置' if button else '未找到逃跑菜单')+'；当前OCR：'+data['text'][:180]);last_combat_log=time.monotonic()
       if not button and on_map and not self.battle_evidence(data) and time.monotonic()-key_sent>8 and not key_retry:
        self.say('仍是起点地图，重新检查画面后重试5一次');self.press(0x35,virtual=True);key_retry=True;key_sent=time.monotonic()
       elif not button and on_map and not self.battle_evidence(data) and key_retry and time.monotonic()-key_sent>12 and not getattr(self,'_patience_used',False):
        # 2026-10-27 21:59-22:08 龙穴: the game ignored three escape clicks, then 5 twice, and answered
        # again 20 minutes later - a server stall, not a broken setup. Wait once more before giving up.
        self._patience_used=True;self.say('游戏 20 秒没反应（可能是服务器卡顿），等 15 秒再按一次 5')
        self.wait(15);self.press(0x35,virtual=True);key_sent=time.monotonic()
       elif not button and on_map and not self.battle_evidence(data) and key_retry and time.monotonic()-key_sent>12:
        raise RuntimeError('重试后仍是地图，游戏未触发技能；请检查5的绑定及输入权限。诊断截图已保存')
       if button:
        self._patience_used=False
        spot=self.current_spot()
        if getattr(self,'task_mode','fixed')=='frillish' and spots.target_count(data.get('text',''),spot)<spot['min_count']:
         self.diagnostic(im,data,'protected');raise RuntimeError(f'白嫖闪：未确认是{"/".join(spot["targets"])}群，已停止，未点击逃跑')
        check_started=time.monotonic();guard=self.original_flash_guard(im,button)
        if guard['matched']:
         self.diagnostic(im,{**data,'original_flash_rule':guard},'original-flash-check');raise RuntimeError('原规则命中闪光：指定OCR区域文字 + 逃跑图像；已停止')
        if guard['escape_image_hit']:
         self.say('原闪光规则未命中；'+(f'已确认{"/".join(self.current_spot()["targets"])}群' if getattr(self,'task_mode','fixed')=='frillish' else 'PP果定点'))
         fresh=self.capture();latest=self.check_verification(fresh);guard=self.original_flash_guard(fresh,button)
         if guard['matched']:
          self.diagnostic(fresh,{**latest,'original_flash_rule':guard},'before-escape');raise RuntimeError('点击前原闪光规则命中，已停止')
         if '验证码' in latest['text']:raise RuntimeError('点击前检测到验证码，已停止')
         confirmed_button=escape_region(latest)
         if not guard['escape_image_hit']:raise RuntimeError('点击前逃跑图像不匹配，已停止')
         if confirmed_button:
          button=confirmed_button
         else:
          # macOS Vision can miss text in a single frame even while the button
          # graphic remains unchanged. Require both image checks and reuse ONLY
          # the location recognized earlier in this very same encounter.
          self.say('点击前OCR漏读；前后两帧逃跑图像均匹配，使用本轮首次OCR确认的位置')
         area=escape_button_area(button);point=self.click(area);self.say(f'已点击逃跑按钮内随机位置（{window_point(point,self.calibrated)}），等待游戏确认；双检至点击耗时{time.monotonic()-check_started:.3f}秒');state='return';deadline=time.monotonic()+35;stable=0;hud_frames=0;prev_return_im=None;last_escape_click=time.monotonic();escape_retries=0;escape_success=False;return_log=0
         self.diagnostic(fresh,{**latest,'original_flash_rule':guard},'escape-click-sent')
        else:self.say(f'等待原逃跑图像条件，当前相似度{guard["escape_image_score"]:.3f}')
      elif state=='return':
       button=escape_region(data);escape_success=escape_success or '成功逃脱' in data['text']
       # Other players walking over a memorised map patch break the image match;
       # after a seen escape message, a HUD-only OCR frame is also valid evidence.
       hud_only=hud_only_map_text(data) and not button
       hud_frames=hud_frames+1 if hud_only else 0
       # The 成功逃脱 bubble is short and slow OCR can miss it; then 3 consecutive HUD-only frames
       # (no menu, no battle words) at least 2.5s after the click also prove we are back on the map.
       ocr_map=hud_only and (escape_success or (hud_frames>=3 and time.monotonic()-last_escape_click>=2.5))
       restored=map_return_evidence(im,data,button,on_map or ocr_map)
       if restored and not on_map and stable>=1 and prev_return_im is not None:
        self.det.set_map_reference(prev_return_im,im);self.say('地图参考图被遮挡，已按当前画面重新记录')
       if not restored and not button and time.monotonic()-last_escape_click>=3:
        ui=self.blocking_ui(im,data)
        if ui:raise RuntimeError(f'逃跑后画面被「{screen_state.LABELS[ui]}」挡住了，先处理再继续')
       stable=stable+1 if restored else 0
       prev_return_im=im if restored else None
       if time.monotonic()-return_log>=4:
        self.diagnostic(im,{**data,'map_surface_scores':map_surface_scores(im),'map_template_scores':[self.det.score(im,'map_tree'),self.det.score(im,'map_bridge')],'ocr_map':bool(ocr_map),'escape_success_seen':escape_success,'hud_only_frames':hud_frames,'return_stable':stable},'return')
        self.say(f'确认逃跑结果：菜单={bool(button)}，成功逃脱提示={escape_success}，地图恢复={restored}（图像={on_map}，文字={bool(ocr_map)}，连续HUD帧={hud_frames}）')
        return_log=time.monotonic()
       if button and escape_retries>=2 and time.monotonic()-last_escape_click>=20:
        # Server stalls (2026-10-27 21:59) can hold the menu for a while: give the game 20 s, not 4.
        raise RuntimeError('已发送三次逃跑点击，又等了 20 秒菜单仍在；已停止，请检查是否有遮挡或游戏未接收点击')
       if button and time.monotonic()-last_escape_click>=2 and escape_retries<2:
        guard=self.original_flash_guard(im,button)
        if guard['matched']:raise RuntimeError('重试逃跑前原闪光规则命中，已停止')
        if guard['escape_image_hit']:
         fresh=self.check_verification(self.capture())
         if not escape_region(fresh):raise RuntimeError('重试前无法再次确认逃跑菜单，已停止')
         area=escape_button_area(escape_region(fresh))
         (self.slow_click if escape_retries==1 else self.click)(area);escape_retries+=1;last_escape_click=time.monotonic()
         self.say(f'逃跑菜单仍在，已复查并发送重试点击（{escape_retries}/2{"，慢速点击" if escape_retries==2 else ""}），等待游戏确认')
       if stable>=2:
        rounds+=1;self.total_rounds+=1
        if self.pp_run:due=self.pp_progress.encounter_done();self.say(f'第{rounds}轮完成；PP {self.pp_progress.completed}/6')
        else:due=False;self.say(f'第{rounds}轮完成（PP果定点：不回院，一直刷）')
        stable=0
        ui=self.blocking_ui(im,data)
        if ui:raise RuntimeError(f'逃跑后画面被「{screen_state.LABELS[ui]}」挡住了，先处理再继续')
        if due:
         if self.pp_run:self.recover_pp()
         else:raise RuntimeError('六次遭遇完成，PP恢复未启用，已停止')
        if self.channel_run and self.elapsed>=self.next_switch:self.switch();state='ready';deadline=time.monotonic()+45
        else:
         self.press(0x35);self.say('已再次发送5，开始下一轮战斗');state='battle';key_sent=time.monotonic();key_retry=False;deadline=time.monotonic()+55
      if time.monotonic()>deadline:raise RuntimeError('等待画面变化超时，已停止，请检查游戏')
      self.wait(.10)
     break
    except Exception as e:
     if not self.recoverable(e):raise
     resume=self.recover_from(e)
  except Exception as e:
   was_stopped=self.stop_event.is_set();self.stop_event.set();self.release_inputs();reason=str(e);self.say(reason)
   try:self.diagnostic(backend.capture(self.hwnd),{'text':reason},'stopped')  # screenshot of the moment it stopped
   except Exception:pass
   kind=notification_kind(reason,getattr(self,'protection',None),was_stopped)
   if kind:self.notifier.send(kind,reason)

  finally:
   self.release_inputs()
   if self.rest_until:self.saved_rest=max(0,self.rest_until-time.monotonic());self.rest_until=0
   self.mode='已停止'
   if hasattr(self,'ocr'):
    try:self.ocr.close()
    except Exception:pass
 def refresh(self):
  now=time.monotonic();delta=now-self.clock;self.clock=now
  if self.worker and self.worker.is_alive() and self.mode in ['刷怪','切换频道'] and not self.stop_event.is_set():self.elapsed+=delta
  s=int(self.elapsed);self.time_label.config(text=f'累计运行 {s//3600:02}:{s//60%60:02}:{s%60:02}')
  remain=max(0,int(self.rest_until-now))
  self.count_label.config(text=f'休息剩余 {remain//60:02}:{remain%60:02}' if remain else f'距换频道 {max(0,int(self.next_switch-self.elapsed))//60:02}:{max(0,int(self.next_switch-self.elapsed))%60:02}')
  self.state_label.config(text='状态：'+self.mode)
  self.pp_label.config(text=f'累计 {self.total_rounds} 轮  ·  PP {self.pp_progress.completed}/6')
  while not self.events.empty():
   text=self.events.get()
   if isinstance(text,tuple):
    _,h,rect,im=text;self.bound_window=h;self.calibrated=(h,rect)
    im.save(BASE/'preview.png');im.thumbnail((384,288));self.preview_image=ImageTk.PhotoImage(im)
    self.preview_label.config(image=self.preview_image,text='');continue
   self.log.config(state='normal');self.log.insert('end',time.strftime('%H:%M:%S')+' '+text+'\n');self.log.see('end');self.log.config(state='disabled')
  self.root.after(150,self.refresh)
 def close(self):
  self.hotkey_stop.set();self.halt('已关闭');self.root.destroy()
 def main(self):self.root.mainloop()

if __name__=='__main__':App().main()
