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
def map_view(im):
 # The battle scene leaves black bars where the map was drawn; the HUD text stays in both, so OCR alone
 # cannot tell a battle intro from the map (sim frames: map 1.00/1.00, battle 0.79/0.89).
 return min(map_surface_scores(im))>=.95
def edge_brightness(im):
 # Mean brightness of the strips left of / below the battle window. A battle darkens them: old UI black
 # bars, new UI (2026-10-31) dims the map behind the window. Real frames: map 78-127, battle 22-30.
 a=np.asarray(im.convert('L')).astype(float)
 return float(np.concatenate([a[180:600,20:115].ravel(),a[630:700,160:780].ravel()]).mean())
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
# New UI (game update 2026-10-31), bottom-right bar at y 735 (real screenshots 2026-11-14): ... 878 globe, 912 mail,
# 946 gift, 990 ≡. The channel entry is not measured yet: try the globe, then ≡, and go only by the words that appear.
NEW_UI_CHANNEL_ENTRIES=(('右下角地球图标',(870,727,16,16)),('右下角≡菜单',(982,727,16,16)))
# Empty game-world spot left of the player. 2026-10-23 琉璃市: after the book icon was clicked to close its
# menu, the icon kept keyboard focus, so the nurse's 空格 reopened the menu and opened 好友 instead.
# A click on the world hands the keyboard back to the game.
WORLD_FOCUS=(40,420,10,10)
# 走路单遇 (user 2026-10-30, 矢车森林): walk back and forth in the grass, no Sweet Scent, no hospital.
WALK_KEYS={'h':(0x41,0x44),'v':(0x57,0x53)}   # left/right or up/down
WALK_NO_ENCOUNTER=90                          # seconds of walking without a battle -> not in the grass
WALK_ESCAPE_RETRIES=6
WALK_STILL_BATTLE=re.compile(r'Lv\.?\s*\d+|派出了|使出了|逃跑|选择战斗技能')                         # a single wild Pokemon can block the escape a few times
# 抓百变 (user 2026-11-14, 城都47号道路, lead 图图犬): a wild 百变怪 in 走路单遇 is caught, not escaped:
# move 1 点到为止 once, move 3 蘑菇孢子 until it sleeps, then hotbar key 3 (精灵球) until caught.
# OCR has read the name as 百变怪 and 百变任 (2026-11-13 沙漠的地下道).
DITTO=re.compile(r'百变')
DITTO_MOVES={1:('点到为止',r'点到'),3:('蘑菇孢子',r'蘑菇|孢子|包子')}
BALL_KEY=0x33
LEPPA_KEY=0x39          # 苹野果 on hotbar slot 9 (user 17:56; hotbar screenshot 16:57)
LEPPA_AT=4              # after a catch, 蘑菇孢子 PP at or below this -> 苹野果 (+10 PP)
DITTO_MAX_ACTIONS=20
DITTO_ASLEEP=re.compile(r'睡着|睡得|呼呼大睡|睡觉|睡眠')   # 16:57 real: 「野生的百变怪'普睡得很香.」
DITTO_MISSED=re.compile(r'没有效果|但是失败|没有命中|没有打中|避开|不会有任何效果')
DITTO_NO_PP=re.compile(r'没有剩余|PP不足|PP用完|没有PP')
DITTO_CAUGHT_WORDS=re.compile(r'收服|成功捕获|送到了?电脑|送往')
DITTO_NEAR_MISS=re.compile(r'差一点|差点|就快|都快|就要|看起来|可惜|糟糕|嗨呀|挣脱|逃出')   # 17:22 「嗨呀！都快抓到了！」
def caught_text(t):
 # Success: 「抓到了！」 then 「收服到百变怪'普了！」. Failed balls also say 抓到了 (16:49 「差一点就抓到了」,
 # 16:58 again), so only 收服 / sent to the PC count.
 t=cn_norm(t);return bool(DITTO_CAUGHT_WORDS.search(t)) and not DITTO_NEAR_MISS.search(t)
LEAD_HP_BOX=(560,300,900,400)   # our lead's 「93 / 234」 under its plate (real 16:59: y 340; full HP frames: y 370)
def lead_hp(data):
 x0,y0,x1,y1=LEAD_HP_BOX
 for l in data.get('lines',[]):
  if not(x0<=l['x']<=x1 and y0<=l['y']<=y1):continue
  m=re.fullmatch(r'(\d{1,3})/(\d{2,3})',cn_norm(l['text']))
  if m and 0<=int(m.group(1))<=int(m.group(2)):return int(m.group(1)),int(m.group(2))
 return None
ESC_KEY=0x1B
# User 2026-11-14 16:40: after a catch the game opens the 「宝可梦 资料」 window (real screenshot 16:50:46:
# title 宝可梦 资料, 质子 / 这只宝可梦没有质子, 持有道具: 无, 百变怪'普 Lv. 35). ESC closes it. It covers only the
# middle of the screen, so the edges still look like the map.
INFO_PAGE=re.compile(r'宝可梦资料|持有道具|没有质子')
def info_page(data):
 return bool(INFO_PAGE.search(cn_norm(data.get('text','')))) and not escape_region(data)
DITTO_PLATES=(120,55,900,215)   # x0,y0,x1,y1: wild name plates (single 143, horde 97/150); our own plate is at y 337
def ditto_plate(data):
 """True only for ONE wild 百变怪: its name plate says 百变 and no other wild plate is there.
 2026-11-14 16:29 47号道路: a 拉达 horde was taken for a Ditto - 百变 came from the open chat, not a plate."""
 x0,y0,x1,y1=DITTO_PLATES
 near=[l for l in data.get('lines',[]) if y0<=l['y']<=y1 and x0<=l['x']<=x1]
 levels=[l for l in near if re.search(r'Lv\.?\d+',cn_norm(l['text']),re.I)]   # one 「Lv.」 per wild Pokemon
 if len(levels)!=1:return False
 # OCR may split 「百变怪'普」 and 「Lv. 35」 into two lines of the same row.
 return any(DITTO.search(cn_norm(l['text'])) and abs(l['y']-levels[0]['y'])<=12 for l in near)
def battle_menu_rects(escape):
 """Click areas of the 2x2 battle menu from the OCR rect of 逃跑 (bottom-right button). The move menu uses the
 same grid: move 1 = 战斗's place, 2 = 背包, 3 = 宝可梦, 4 = 逃跑. Measured on sim/frames/newui_battle_driftveil.png:
 战斗 (245,497)  背包 (455,497) / 宝可梦 (245,551)  逃跑 (455,551): columns 210 px, rows 54 px apart."""
 x,y,w,h=escape
 def at(dx,dy):return escape_button_area((x+dx,y+dy,w,h))
 return {1:at(-210,-54),2:at(0,-54),3:at(-210,0),4:at(0,0)}
def inside(line,rect):
 cx=line['x']+line['w']/2;cy=line['y']+line['h']/2;x,y,w,h=rect
 return x<=cx<=x+w and y-6<=cy<=y+h+6
def move_pp(lines,rect):
 """Remaining PP of the move button at rect (「PP 12/24」 or 「12/24」 on the button), None when unreadable."""
 for l in lines:
  if not inside(l,rect):continue
  m=re.search(r'(\d+)\s*/\s*(\d+)',cn_norm(l['text']))
  if m and int(m.group(2))<=64:return int(m.group(1))
 return None
# User confirmed 2026-10-18 21:16: clicking the same icon again closes its popup menu (book and pokeball).
# Never auto-recover from these: protection stops, user stops, setup problems.
def channel_interval():
 # User 2026-10-18: change channel at a random moment, every 40-50 minutes (not a fixed 50).
 return random.randint(2400,3000)
NO_RECOVERY=('抓百变','香水没开','关服','闪光','验证','手动停止','F8','已关闭','缺少屏幕录制','尚未授予','窗口为','原生输入','六次遭遇完成','轻飘飘全自动','未确认是','路线测试','游戏已关闭','切不回前台','没找到 PokeMMO','自动恢复')
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
 # Old HUD (until 2026-10-30): 「Ch.2」. Game update 2026-10-31: 「城镇 · 频道1」.
 m=re.search(r'(?:Ch\.?|频道)\s*([1-9])(?!\d)',str(text or ''));return int(m.group(1)) if m else None
SERVER_SHUTDOWN=re.compile(r'服务器关闭|关服说明')
OLD_HUD_MARK=re.compile(r'Ch\.|Ch(?![A-Za-z])')   # old HUD 「Ch.2」; not English map names such as Chargestone
def new_game_ui(data):
 # The 2026-10-31 PokeMMO update moved the HUD and the bottom-right icon bar: the HUD line reads
 # 「城镇·频道1」 instead of 「Ch.1」. The old channel-menu positions do not apply there.
 top=' '.join(l['text'] for l in data.get('lines',[]) if l.get('y',999)<40)
 return bool(re.search(r'城镇|频道',top)) and not re.search(r'Ch\.?\s*\d',top)
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
 # New HUD since the 2026-10-31 update: 「道路・频道1」 (digit often under the hotbar) and 「◎ $18,532,248」;
 # Vision sometimes drops the $ (2026-11-07 橙华森林: walk mode waited 84 s, 0 steps, on a plain map).
 if not re.search(r'Ch\.?\s*\d|\$\s*[\d,]{3,}|[◎©]\s*[\d,]{5,}|频道|捐赠者状态',text):return False
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
  if SERVER_SHUTDOWN.search(re.sub(r'\s+','',data['text'])):
   # 2026-10-30 21:42: 「5 分 0 秒后服务器关闭. 请退出游戏.」 (event update). Nothing to recover: stop and say so.
   self.diagnostic(im,data,'protected');raise RuntimeError('游戏提示服务器要关服维护，已停止；更新完再开游戏、按开始')
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
  if state=='return':   # latest-ocr.json is overwritten at stop; keep the last return frame for analysis
   (folder/'latest-return.json').write_text(json.dumps({'time':time.strftime('%Y-%m-%d %H:%M:%S'),**data},ensure_ascii=False,indent=2),encoding='utf8')
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
 def walking(self):return getattr(self,'task_mode','fixed')=='walk'
 def mode_label(self):
  m=getattr(self,'task_mode','fixed');return '走路单遇' if m=='walk' else ('PP果定点' if m=='fixed' else '白嫖闪')
 def walk_step(self):
  # One step, then the opposite one of the same length: the player stays on the same patch of grass.
  # 2026-11-07: 0.35-0.6 s taps with a full OCR between them looked like standing still (3 steps in 6 s).
  # Now 0.25-0.45 s steps in bursts (walk_burst), and while holding a cheap image check (battle window = map no longer fills the
  # screen) lets go of the key as soon as a battle starts. Returns True when that happened.
  keys=WALK_KEYS.get(getattr(self,'walk_axis','h'),WALK_KEYS['h']);i=getattr(self,'_walk_i',0)
  if i%2==0:self._walk_t=random.uniform(.25,.45)
  key=keys[i%2];self._walk_i=i+1;hit=False
  self.ensure();self.key_event(key,True);started=time.monotonic();peek=started
  try:
   while time.monotonic()-started<self._walk_t:
    self.wait(.08)
    if time.monotonic()-started>=self._walk_t:break
    self.ensure();self.repeat_event(key)
    if time.monotonic()-peek>=.15:
     peek=time.monotonic()
     if not self.walk_map(self.capture()):hit=True;break
  finally:self.key_event(key,False)
  return hit
 def walk_map(self,im,learn=False):
  # Map (not battle) by picture, relative to the brightness of this spot's map (night or caves are darker).
  b=edge_brightness(im);base=getattr(self,'_walk_base',None)
  ok=b>=.6*base if base else b>=45
  if ok and learn:self._walk_base=b if not base else .8*base+.2*b
  return ok
 def lure_prompt(self,data,ui):
  # 「香水用完了. 使用下一个?」 comes every ~100 steps with a lure (2026-11-12 210号道路: about every 6 min).
  # OCR often misses its text, so a 是/否 box in 走路单遇 is answered 是 here, not by the recovery (which
  # allows only 3 per 10 min and stopped the run twice). Never for a surf question; never in a tight loop.
  if not self.walking() or ui!='yes_no' or '冲浪' in data.get('text',''):return False
  now=time.monotonic();self._lure_answers=[t for t in getattr(self,'_lure_answers',[]) if now-t<60]+[now]
  if len(self._lure_answers)>3:return False
  self.say('  是/否提示（香水用完了，使用下一个？）：按空格选「是」');self.press(0x20);self.wait(1.5);return True
 def walk_burst(self,seconds=3.0):
  # Several steps back to back (no OCR pause between them); stops at the first sign of a battle.
  # 2026-11-07: the patch of grass is only 6 tiles wide, so short quick steps, not long slow ones.
  end=time.monotonic()+seconds
  while time.monotonic()<end:
   if not self.walk_map(self.capture()):return True   # the battle began between two steps
   if self.walk_step():return True
  return False
 def ditto_wanted(self,*frames):
  return self.walking() and bool(getattr(self,'ditto_catch',False)) and any(ditto_plate(d) for d in frames)
 def ditto_move(self,slot,escape):
  """战斗 -> move `slot`. Returns the clicked area (to click again if the game ignored it)."""
  name,pattern=DITTO_MOVES[slot];rects=battle_menu_rects(escape)
  for attempt in range(2):
   self.click(rects[1]);self.wait(1.2)   # 16:32:03: a move clicked 0.9 s after 战斗 was not taken once
   im=self.capture();data=self.check_verification(im)
   if not escape_region(data):break
  else:raise RuntimeError('抓百变：点了两次「战斗」技能菜单都没打开，已停止')
  lines=[l for l in data.get('lines',[]) if 440<=l['y']<=640]
  hit=next((l for l in lines if re.search(pattern,cn_norm(l['text']))),None)
  if hit:
   # Click the whole button the name sits in, not an area centred on the name: the name is at the button's left
   # end (real 16:34 screenshot: 蘑菇孢子 at x 148, button 143-345), so a name-centred area hung off the button and
   # some clicks missed it (17:05: the move menu stayed open 60 s).
   cx=hit['x']+hit['w']/2;cy=hit['y']+hit['h']/2
   target=min(rects.values(),key=lambda r:abs(r[0]+r[2]/2-cx)+abs(r[1]+r[3]/2-cy));how='按技能名'
  elif lines:target=rects[slot];how=f'技能名没读到，按{slot}号位'
  else:
   self.diagnostic(im,data,'ditto-moves');raise RuntimeError(f'抓百变：技能菜单里一个字都没读到，不敢乱点{name}，已停止，截图在 diagnostics/latest-screen.png')
  pp=move_pp(lines,target)
  if pp==0:raise RuntimeError(f'抓百变：{name} 的 PP 用完了，已停止；回中心补满 PP 再开始')
  if slot==3 and pp is not None:self.spore_pp=pp-1   # what is left after this use
  self.diagnostic(im,data,'ditto-moves')
  self.click(target);self.say(f'  抓百变：{slot}号技能「{name}」（{how}{"" if pp is None else f"，剩 {pp} PP"}）')
  return target
 def ditto_turn(self,what,timeout=60,stale=None):
  """After a move or a ball: ('menu', 逃跑 rect) when the battle menu is back, ('map', None) when the battle is over,
  ('ignored', rect) when the menu never went away (input not taken). stale=(pattern, rect): the move button that
  was clicked; still there 5 s later = the click was not taken. Also returns every OCR line of the turn."""
  started=time.monotonic();texts=[];gone=False;map_frames=0;last_log=started;stale_since=None;caught_at=None
  while True:
   im=self.capture();data=self.check_verification(im);button=escape_region(data);now=time.monotonic()
   if not button:gone=True
   if gone:texts.extend(cn_norm(l['text']) for l in data.get('lines',[]))   # last turn's message is not this turn's
   if button and gone and now-started>=1.5:return 'menu',button,texts,data
   if info_page(data):return 'page',None,texts,data
   if not button and any(caught_text(t) for t in texts):
    caught_at=caught_at or now
    if now-caught_at>=4:return 'page',None,texts,data   # caught, the battle window is closing / a page opens
   if button and not gone and now-started>=6:return 'ignored',button,texts,data
   # The move menu still open = the move button and its 「PP: 21 / 24」. 16:56:53: the battle message 「使出了蘑菇孢子」
   # in the same place was taken for the button and the move was clicked again.
   if stale and not button and any(inside(l,stale[1]) and re.search(stale[0],cn_norm(l['text'])) and '使出' not in l['text'] for l in data.get('lines',[])) \
      and any((inside(l,stale[1]) and 'PP' in l['text'].upper()) or (cn_norm(l['text']).endswith('返回') and l['x']>=700 and l['y']>=520)
              for l in data.get('lines',[])):   # its PP, or the move menu's own 「B 返回」 button (real 16:34: x 790-880)
    stale_since=stale_since or now
    if now-stale_since>=4:return 'ignored',None,texts,data
   else:stale_since=None
   on_map=not button and self.walk_map(im) and not WALK_STILL_BATTLE.search(data['text'])
   map_frames=map_frames+1 if on_map else 0
   if map_frames>=2 and now-started>=2.5:return 'map',None,texts,data
   if self.blocking_ui(im,data)=='yes_no':
    self.diagnostic(im,data,'ditto-yes-no')
    raise RuntimeError(f'抓百变：{what}之后出现了是/否选择框，不知道该选哪个，已停止，截图在 diagnostics/latest-screen.png')
   if now-started>timeout:
    self.diagnostic(im,data,'ditto-timeout');raise RuntimeError(f'抓百变：{what}之后等了{timeout}秒战斗菜单没回来，已停止，截图在 diagnostics/latest-screen.png')
   if now-last_log>=5:last_log=now;self.say(f'  抓百变：{what}，等这一回合结束；当前OCR：{data["text"][:120]}')
   self.wait(.15)
 def catch_ditto(self,escape):
  self.mode='抓百变';self.say('是百变怪：不逃跑，开始抓（1号技能点到为止 → 3号技能蘑菇孢子 → 睡着后按3扔球）')
  swiped=asleep=False;balls=0;quiet=0;thrown=0;retried=False;target=None;what=''
  for action in range(DITTO_MAX_ACTIONS+1):
   if not retried:
    if action==DITTO_MAX_ACTIONS:raise RuntimeError(f'抓百变：出手{DITTO_MAX_ACTIONS}次还没抓到，已停止，请手动看一下')
    if not swiped:what='点到为止';target=self.ditto_move(1,escape)
    elif not asleep:what='蘑菇孢子';target=self.ditto_move(3,escape)
    else:what='扔精灵球';target=None;thrown+=1;self.press(BALL_KEY);self.say(f'  抓百变：按3扔精灵球（这只第{thrown}个）')
   result,button,texts,data=self.ditto_turn(what,stale=None if target is None else (DITTO_MOVES[1 if what=='点到为止' else 3][1],target))
   if result=='ignored':
    if retried:raise RuntimeError('抓百变：按3两次都没扔出球，已停止；检查快捷键3是不是精灵球、球还有没有' if target is None else f'抓百变：点了两次「{what}」游戏都没反应，已停止')
    retried=True;self.say(f'  抓百变：{what}游戏没反应，再来一次')
    if target is None:self.press(BALL_KEY)
    else:self.click(target)
    continue
   retried=False
   if any(DITTO_NO_PP.search(t) for t in texts):raise RuntimeError(f'抓百变：游戏提示PP不够了（{what}），已停止；回中心补满 PP 再开始')
   if result in ('map','page'):
    if what=='扔精灵球' or any(caught_text(t) for t in texts):
     back,button=self.close_info_page()
     if back=='battle':   # the battle menu came back: that ball did not catch it after all
      self.say('  抓百变：战斗菜单又出现了，这个球其实没抓住，继续');escape=button;balls+=1;continue
     self.ditto_caught=getattr(self,'ditto_caught',0)+1;self.say(f'抓到百变怪了！这次开始后共抓到 {self.ditto_caught} 只，继续走')
     pp=getattr(self,'spore_pp',None)
     if pp is not None and pp<=LEPPA_AT:self.use_leppa(pp)
    else:self.say(f'  抓百变：{what}之后战斗就结束了，没看到捕获提示，继续走')
    self.mode='刷怪';return
   escape=button
   # No HP stop (17:54 stopped at 29/234 for nothing): a transformed Ditto only has our lead's moves, and the only
   # damaging one, 点到为止, always leaves at least 1 HP. Only a fainted lead (0 HP) stops.
   hp=lead_hp(data)
   if hp and hp[0]==0:raise RuntimeError('抓百变：图图犬倒下了，已停止；回中心恢复再开始')
   ditto_lines=[t for t in texts if DITTO.search(t)]
   ours_asleep=any(DITTO_ASLEEP.search(t) and '图图' in t and not DITTO.search(t) and '使出' not in t for t in texts)   # our lead 图图犬
   if what=='点到为止':swiped=True
   elif what=='蘑菇孢子':
    # 蘑菇孢子 always hits; 「失败」 means it was already asleep. Only our own lead sleeping stops it.
    asleep=not ours_asleep;quiet=0
    self.say('  抓百变：百变怪睡着了，下一步扔球' if asleep else '  抓百变：图图犬自己睡着了，孢子没放出来，再来一次')
   else:
    balls+=1;self.say(f'  抓百变：第{thrown}个球没抓住')
    # User 17:23: no 蘑菇孢子 while it still sleeps. A sleeping Ditto says 「睡得很香」 on its turn; spore again only
    # when it woke up, or when two balls in a row showed no sign of sleep (the wake-up message missed by OCR).
    if any(re.search(r'醒',t) for t in ditto_lines):asleep=False;self.say('  抓百变：百变怪醒了，再用蘑菇孢子')
    elif any(DITTO_ASLEEP.search(t) for t in ditto_lines):quiet=0;self.say('  抓百变：百变怪还在睡，继续扔球')
    else:
     quiet+=1
     if quiet>=2:asleep=False;self.say('  抓百变：连续两个球都没看到百变怪在睡，可能醒了，再用蘑菇孢子')
  raise RuntimeError(f'抓百变：出手{DITTO_MAX_ACTIONS}次还没抓到，已停止，请手动看一下')
 def leppa_snapshot(self,im,step):
  try:
   folder=BASE/'diagnostics'/'leppa';folder.mkdir(parents=True,exist_ok=True)
   if step==1:
    for old in folder.glob('*.png'):old.unlink()
   im.save(folder/f'{step:02d}-{time.strftime("%H%M%S")}.png',compress_level=1)
  except Exception:pass
 def find_text(self,pattern,tries=6):
  # Look for an OCR line matching pattern for ~3 s; returns (line, im, data) or (None, im, data).
  for _ in range(tries):
   im=self.capture();data=self.check_verification(im)
   for l in data.get('lines',[]):
    if re.search(pattern,cn_norm(l['text'])):return l,im,data
   self.wait(.5)
  return None,im,data
 def use_leppa(self,pp):
  """On the map after a catch, fill 蘑菇孢子 up with 苹野果 in one go (user 19:10 / 00:09).
  Real screens: hotbar 9 -> 「对哪只宝可梦使用?」 (图图犬'普) -> 「回复哪个技能?」 (蘑菇孢子 / PP: 4 / 24) -> box 「苹野果(PP果)」
  「要对图图犬'普使用多少苹野果(PP果)?」 [-] [1] [+] [最大] / 「使用」 「取消」 (00:00: - 423, 1 488, + 552, 最大 593 at y 398;
  使用 512,457). Each berry +10, so the count is set with + to what fills it; then a toast 「对图图犬'普使用了2x 苹野果(PP果).」
  stays a few seconds - it is not a window (00:00:36 it was taken for one and the run stopped).
  Every step is checked by its words; screenshots go to diagnostics/leppa."""
  self.mode='用苹野果';step=[0]
  def snap(im):step[0]+=1;self.leppa_snapshot(im,step[0])
  def box(l):return (int(l['x'])-30,int(l['y'])-6,int(l['w'])+60,int(l['h'])+12)
  def window_lines(data):   # lines of a real window; the 「使用了」 toast is not one
   return [l for l in data.get('lines',[]) if '使用了' not in l['text'] and
           re.search(r'对哪只|回复哪个|使用多少|^使用$|取消$|^最大$',cn_norm(l['text']))]   # 「B 取消」 too
  def close():
   for _ in range(4):   # close our windows until none is left; never ESC on the map (it opens the menu)
    im=self.capture();data=self.check_verification(im)
    if not window_lines(data):return True
    self.press(ESC_KEY);self.wait(1)
   return False
  def fail(why,im,data):
   self.diagnostic(im,data,'leppa');snap(im);close()
   raise RuntimeError(f'抓百变：用苹野果失败（{why}），已停止，截图在 diagnostics/leppa；可能苹野果用完了，补充后再开始')
  self.press(LEPPA_KEY);self.wait(1)
  who,im,data=self.find_text(r'图图犬');snap(im)
  if not who:fail('按9后没出现「对哪只宝可梦使用」',im,data)
  self.click(box(who));self.wait(1)
  move,im,data=self.find_text(r'蘑菇|孢子');snap(im)
  if not move:fail('选了图图犬后没出现技能列表',im,data)
  cur=None;top=24
  for l in data.get('lines',[]):   # 「PP: 4 / 24」 right under the name, inside the same button
   m=re.search(r'PP:?(\d+)/(\d+)',cn_norm(l['text']),re.I)
   if m and abs(l['x']-move['x'])<=40 and 0<l['y']-move['y']<=40:cur,top=int(m.group(1)),int(m.group(2))
  if cur is None:cur=pp
  if cur>=top:
   close();self.spore_pp=cur;self.mode='刷怪';self.say('  蘑菇孢子本来就是满的，继续走');return
  need=-(-(top-cur)//10)
  self.say(f'  蘑菇孢子 {cur}/{top} PP：用 {need} 个苹野果补满')
  self.click(box(move));self.wait(1)
  origin=self.ensure();self.move_event((origin[0]+150,origin[1]+700));self.wait(.6)   # the move's tooltip covers the box
  use,im,data=self.find_text(r'^使用$');snap(im)
  if not use:fail('选了蘑菇孢子后没出现「使用」按钮',im,data)
  most=next((l for l in data.get('lines',[]) if cn_norm(l['text'])=='最大'),None)
  if need>1:
   if not most:fail('没找到数量框（「最大」按钮）',im,data)
   cx=most['x']+most['w']/2-41;plus=(int(cx)-11,int(most['y'])-4,22,int(most['h'])+8)   # 「+」 centre 41 px left of 最大's (552 / 593)
   for _ in range(need-1):self.click(plus);self.wait(.35)
   im=self.capture();data=self.check_verification(im);snap(im)
   count=next((int(cn_norm(l['text'])) for l in data.get('lines',[]) if re.fullmatch(r'\d{1,2}',cn_norm(l['text']))
               and abs(l['y']-most['y'])<=10 and most['x']-150<=l['x']<most['x']),None)
   if count is not None and count!=need:fail(f'数量框是 {count}，应该是 {need}',im,data)
  self.click(box(use));self.wait(1.2)
  done,im,data=self.find_text(r'使用了',tries=4);snap(im)
  used=need
  if done:
   m=re.search(r'使用了(\d+)',cn_norm(done['text']));used=int(m.group(1)) if m else need
  if not close():im=self.capture();fail('用完后窗口关不掉',im,self.check_verification(im))
  self.spore_pp=min(top,cur+10*used);self.mode='刷怪'
  self.say(f'  用了 {used} 个苹野果，蘑菇孢子 {cur} → {self.spore_pp}/{top} PP，继续走'+('' if done else '（没看到使用提示，下次开技能菜单会读到真实PP）'))
 def caught_snapshot(self,im,label):
  try:
   folder=BASE/'diagnostics'/'ditto-caught';folder.mkdir(parents=True,exist_ok=True)
   im.save(folder/f'{time.strftime("%Y%m%d-%H%M%S")}-{label}.png',compress_level=1)
  except Exception:pass
 def close_info_page(self,limit=12):
  """After a catch: the info page opens (user 2026-11-14); ESC closes it. ESC only when the page is recognised by its
  words, or after 6 s when the screen is neither the map nor the battle (something unrecognised covers it).
  Done when the plain map is back for 2 frames, at least 2.5 s after the catch."""
  started=time.monotonic();presses=0;map_frames=0;last_esc=0;menu_frames=0
  while True:
   im=self.capture();data=self.check_verification(im);now=time.monotonic()
   page=info_page(data)
   plain=not page and not escape_region(data) and self.walk_map(im) and not WALK_STILL_BATTLE.search(data['text'])
   map_frames=map_frames+1 if plain else 0
   if map_frames>=2 and now-started>=(2.5 if presses else 5):
    self.caught_snapshot(im,'closed' if presses else 'no-page');return 'map',None
   button=escape_region(data);menu_frames=menu_frames+1 if button else 0
   if menu_frames>=2:self.caught_snapshot(im,'battle-again');return 'battle',button   # not caught: the menu is back
   # The page shows 「Lv.」 too, so a battle is told by its menu and the catch messages, not by Lv.
   cover=not plain and not escape_region(data) and not caught_text(data['text']) and now-started>=6
   if (page or cover) and now-last_esc>=1.5:
    if presses>=3:
     self.diagnostic(im,data,'ditto-page');raise RuntimeError('抓百变：抓到后按了3次ESC画面还没回到地图，已停止，截图在 diagnostics/ditto-caught')
    self.caught_snapshot(im,'page' if page else 'cover');self.press(ESC_KEY);presses+=1;last_esc=now;map_frames=0
    self.say(f'  抓百变：{"精灵信息页" if page else "抓到后画面被挡住（没认出是什么）"}，按ESC关闭（第{presses}次）')
   if now-started>limit+3*presses:
    self.diagnostic(im,data,'ditto-page');self.caught_snapshot(im,'stuck')
    raise RuntimeError('抓百变：抓到后一直没回到地图，已停止，截图在 diagnostics/ditto-caught')
   self.wait(.25)
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
    raise RuntimeError(f'自动恢复：{self.mode_label()}模式下人在宝可梦中心里，这个模式不会自己走回点位，已停止')
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
    if self.walking() and kind in ('unknown','outdoor'):
     # Outdoors in the grass is the normal place for 走路单遇: go back to walking. Not in the grass ->
     # the walk loop stops by itself after WALK_NO_ENCOUNTER seconds without a battle.
     self.say('  走路单遇：回到来回走');return 'press5'
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
 def ocr_lines(self):
  return self.check_verification(self.capture()).get('lines',[])
 def hud_now(self):
  # Whole top line (not a 330 px crop: long map names push 「频道1」 out of it, 2026-11-13 沙漠的地下道).
  return hud_channel(' '.join(l['text'] for l in self.ocr_lines() if l['y']<40))
 def new_ui_switch(self,log):
  """New UI channel change, by words only. Any step not found: close what was opened (ESC only while new
  window text is on screen - ESC on the plain map opens the menu), skip, retry in 10 minutes. Never stops."""
  def rect(l):return (int(l['x'])-8,int(l['y'])-4,int(l['w'])+16,int(l['h'])+8)
  base={cn_norm(l['text']) for l in self.ocr_lines()}
  def new_lines():return [l for l in self.ocr_lines() if l['y']>=40 and cn_norm(l['text']) not in base]
  def close():
   for _ in range(3):
    if not new_lines():return
    self.press(ESC_KEY);self.wait(.8)
  def skip(why):
   close();self.defocus_ui();self.next_switch=self.elapsed+600
   self.say(f'  新界面换频道没成功（{why}），已关掉打开的窗口，10 分钟后再试，截图在 diagnostics/channel-switch')
  old=self.hud_now()
  if not old:return skip('左上角读不出当前频道')
  rows=None
  for name,icon in NEW_UI_CHANNEL_ENTRIES:
   self.click(icon);self.wait(1);log(f'点{name}')
   found=new_lines()
   item=next((l for l in found if re.search(r'更换频道|切换频道|频道选择|选择频道',cn_norm(l['text']))),None)
   if item and not re.match(r'频道[1-9]',cn_norm(item['text'])):
    self.click(rect(item));self.wait(1);log(f'点「{item["text"]}」');found=new_lines()
   rows=[l for l in found if re.match(r'频道\s*[1-9](?!\d)',cn_norm(l['text']))]
   if rows:break
   close()
  if not rows:return skip('地球图标和≡菜单里都没看到频道列表')
  options={}
  for l in rows:
   t=cn_norm(l['text']);n=int(re.match(r'频道([1-9])',t).group(1))
   if n!=old and '当前' not in t:options[n]=l
  if not options:return skip(f'列表里只有当前频道{old}')
  ordered=sorted(options);target=next((c for c in ordered if c>old),ordered[0])
  self.click(rect(options[target]));self.wait(.8);log(f'点「频道{target}」')
  button=next((l for l in new_lines() if re.fullmatch(r'(变更|确定|确认|切换|更换|加入|进入)',cn_norm(l['text']))),None)
  if button:self.click(rect(button));log(f'点「{button["text"]}」')
  deadline=time.monotonic()+12
  while time.monotonic()<deadline:
   self.wait(1)
   if self.hud_now()==target:break
  else:return skip(f'点完后左上角还不是频道{target}')
  if any(re.match(r'频道[1-9]',cn_norm(l['text'])) for l in new_lines()):self.press(ESC_KEY);self.wait(.8)   # list still open
  self.defocus_ui();log(f'已切到频道{target}')
  gap=channel_interval();self.next_switch=self.elapsed+gap;self.rest_until=time.monotonic()+random.randint(180,480)
  self.say(f'已切到频道{target}，随机休息至倒计时结束；下次换频道在 {gap//60} 分钟后')
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
  hud_text=self.read((0,0,330,30));hud_before=hud_channel(hud_text)
  if new_game_ui({'lines':[{'y':10,'text':hud_text}]}) or not OLD_HUD_MARK.search(hud_text):
   # Clicking the old icon position now opens the 孵蛋器 window (2026-10-31 01:30-02:09, five times),
   # then the recovery walked the whole hospital route. Until the new entry is measured: no clicks.
   # 2026-11-13 21:48-22:59 沙漠的地下道（DesertUnderpass）: the long map name pushed 「频道1」 out of
   # the 330 px HUD crop, the new UI went unrecognised, the old icon was clicked 7 times and the window it
   # opened blocked walking (90 s without encounters, 3 recoveries, stopped). Old positions are only
   # clicked when the old 「Ch.」 HUD is actually seen.
   return self.new_ui_switch(log)
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
     walk=self.walking();self._walk_base=None;lure_seen=False;walk_since=time.monotonic();walk_log=time.monotonic();state='ready';partial_since=None;deadline=time.monotonic()+45;last_combat_log=0;key_sent=time.monotonic();key_retry=False;rounds=0;return_log=0;last_escape_click=0;escape_retries=0;escape_success=False;hud_frames=0
     if resume=='battle':
      state='battle';key_retry=True;deadline=time.monotonic()+55
     elif resume=='press5' and walk:
      state='walk';self.say('继续在草丛里来回走，等遇怪');deadline=time.monotonic()+45
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
      elif walk:
       state='walk';self._walk_base=edge_brightness(reference)   # the start frame: plain map where you stand
       self.say('走路单遇：在草丛里来回走，等遇怪');deadline=time.monotonic()+45
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
      if state=='ready' and walk:
       if not self.battle_evidence(data) and edge_brightness(im)>=45:self._walk_base=edge_brightness(im)
       state='battle' if self.battle_evidence(data) else 'walk';walk_since=time.monotonic();key_sent=time.monotonic();key_retry=True;deadline=time.monotonic()+55;continue
      if state=='walk':
       # 2026-11-07 橙华森林: on the plain map Vision often reads only the right-hand event panel
       # (「◎312（已找到南瓜猫…」), not the HUD, so OCR cannot say "map". The picture can: a battle darkens
       # the screen edges (edge_brightness 22-30) against 78-127 on maps.
       if escape_region(data) or self.battle_evidence(data):
        self.say('遇到野生宝可梦，进入战斗');state='battle';key_sent=time.monotonic();key_retry=True;last_combat_log=0;deadline=time.monotonic()+55;continue
       if re.search(r'香水用|使用下一个',data['text']):
        # 「香水用完了. 使用下一个?」 (PokeLoop template 1766587015): answer 是 with space, keep walking.
        self.say('  香水用完了，按空格用下一瓶');self.press(0x20);self.wait(1.5);walk_since=time.monotonic();continue
       if getattr(self,'ditto_catch',False) and info_page(data):
        self.say('  精灵信息页还开着，按ESC关闭');self.caught_snapshot(im,'late-page');self.press(ESC_KEY);self.wait(1.2);walk_since=time.monotonic();continue
       ui=self.blocking_ui(im,data)
       if ui and self.lure_prompt(data,ui):walk_since=time.monotonic();continue
       if ui:raise RuntimeError(f'刷怪点被「{screen_state.LABELS[ui]}」挡住了，没有继续走')
       if not self.walk_map(im,learn=True):
        # Battle window opening (or a transition): stop pressing keys and let the battle state look.
        self.say('遇到野生宝可梦（画面出现战斗窗口），进入战斗');state='battle';key_sent=time.monotonic();key_retry=True;last_combat_log=0;deadline=time.monotonic()+55;continue
       if time.monotonic()-walk_log>=30:
        walk_log=time.monotonic();self.say(f'  走路中：已走 {getattr(self,"_walk_i",0)} 步，{int(time.monotonic()-walk_since)} 秒没遇到怪')
       if self.channel_run and self.elapsed>=self.next_switch:self.switch();walk_since=time.monotonic();deadline=time.monotonic()+45;continue
       if time.monotonic()-walk_since>WALK_NO_ENCOUNTER:
        raise RuntimeError(f'走路单遇：来回走了{WALK_NO_ENCOUNTER}秒都没遇到怪，可能不在草丛里或被挡住了，已停止')
       self.walk_burst();deadline=time.monotonic()+45;continue
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
       # In battle the HUD (with 「247香水步数」 while a lure runs) is read reliably, unlike on the map.
       if walk and re.search(r'香水步',data['text']):lure_seen=True
       if time.monotonic()-last_combat_log>=4:
        self.diagnostic(im,data,'battle');self.say(('已找到逃跑文字位置' if button else '未找到逃跑菜单')+'；当前OCR：'+data['text'][:180]);last_combat_log=time.monotonic()
       if not walk and not button and on_map and not self.battle_evidence(data) and time.monotonic()-key_sent>8 and not key_retry:
        self.say('仍是起点地图，重新检查画面后重试5一次');self.press(0x35,virtual=True);key_retry=True;key_sent=time.monotonic()
       elif not walk and not button and on_map and not self.battle_evidence(data) and key_retry and time.monotonic()-key_sent>12 and not getattr(self,'_patience_used',False):
        # 2026-10-27 21:59-22:08 龙穴: the game ignored three escape clicks, then 5 twice, and answered
        # again 20 minutes later - a server stall, not a broken setup. Wait once more before giving up.
        self._patience_used=True;self.say('游戏 20 秒没反应（可能是服务器卡顿），等 15 秒再按一次 5')
        self.wait(15);self.press(0x35,virtual=True);key_sent=time.monotonic()
       elif not walk and not button and on_map and not self.battle_evidence(data) and key_retry and time.monotonic()-key_sent>12:
        raise RuntimeError('重试后仍是地图，游戏未触发技能；请检查5的绑定及输入权限。诊断截图已保存')
       if walk and not button and self.walk_map(im) and not self.battle_evidence(data) and time.monotonic()-key_sent>6:
        self.say('  没进战斗（画面还是地图），继续走');state='walk';walk_since=time.monotonic();deadline=time.monotonic()+45;continue
       if button:
        self._patience_used=False
        spot=self.current_spot()
        if getattr(self,'task_mode','fixed')=='frillish' and spots.target_count(data.get('text',''),spot)<spot['min_count']:
         self.diagnostic(im,data,'protected');raise RuntimeError(f'白嫖闪：未确认是{"/".join(spot["targets"])}群，已停止，未点击逃跑')
        check_started=time.monotonic();guard=self.original_flash_guard(im,button)
        if guard['matched']:
         self.diagnostic(im,{**data,'original_flash_rule':guard},'original-flash-check');raise RuntimeError('原规则命中闪光：指定OCR区域文字 + 逃跑图像；已停止')
        if guard['escape_image_hit']:
         self.say('原闪光规则未命中；'+(f'已确认{"/".join(self.current_spot()["targets"])}群' if getattr(self,'task_mode','fixed')=='frillish' else self.mode_label()))
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
         if self.ditto_wanted(data,latest):
          # Shiny checks above are done; a 百变怪 is caught instead of escaped (抓百变 switch).
          self.catch_ditto(button);rounds+=1;self.total_rounds+=1;lure_seen=False
          if self.channel_run and self.elapsed>=self.next_switch:self.switch()
          state='walk';walk_since=time.monotonic();deadline=time.monotonic()+45;continue
         area=escape_button_area(button);point=self.click(area);self.say(f'已点击逃跑按钮内随机位置（{window_point(point,self.calibrated)}），等待游戏确认；双检至点击耗时{time.monotonic()-check_started:.3f}秒');state='return';deadline=time.monotonic()+35;stable=0;hud_frames=0;prev_return_im=None;last_escape_click=time.monotonic();escape_retries=0;escape_success=False;return_log=0
         self.diagnostic(fresh,{**latest,'original_flash_rule':guard},'escape-click-sent')
        else:self.say(f'等待原逃跑图像条件，当前相似度{guard["escape_image_score"]:.3f}')
      elif state=='return':
       button=escape_region(data);escape_success=escape_success or '成功逃脱' in data['text']
       # Other players walking over a memorised map patch break the image match;
       # after a seen escape message, a HUD-only OCR frame is also valid evidence.
       hud_only=hud_only_map_text(data) and not button
       if walk and not hud_only and not button and time.monotonic()-last_escape_click>=2.5 and self.walk_map(im) and not WALK_STILL_BATTLE.search(data['text']):
        # 2026-11-07 橙华森林: after the game update the walk-mode map frames never passed hud_only (logged
        # 文字=False 274/274 times since 10-31), and walking leaves the start-image reference behind.
        # No menu and no name plate (Lv.) for a while after the click is the map.
        hud_only=True
       hud_frames=hud_frames+1 if hud_only else 0
       # The 成功逃脱 bubble is short and slow OCR can miss it; then 3 consecutive HUD-only frames
       # (no menu, no battle words) at least 2.5s after the click also prove we are back on the map.
       ocr_map=hud_only and (escape_success or (hud_frames>=3 and time.monotonic()-last_escape_click>=2.5))
       restored=map_return_evidence(im,data,button,on_map or ocr_map)
       if walk and not restored and ocr_map and not any(w in data['text'] for w in ('变更','取消','验证码','是否')):
        restored=True   # a lingering 成功逃脱 banner is not a battle (walk mode has no start-image to compare)
       if restored and not on_map and stable>=1 and prev_return_im is not None and not walk:
        self.det.set_map_reference(prev_return_im,im);self.say('地图参考图被遮挡，已按当前画面重新记录')
       if not restored and not button and time.monotonic()-last_escape_click>=3:
        ui=self.blocking_ui(im,data)
        if ui and self.lure_prompt(data,ui):ui=None
        if ui:raise RuntimeError(f'逃跑后画面被「{screen_state.LABELS[ui]}」挡住了，先处理再继续')
       stable=stable+1 if restored else 0
       prev_return_im=im if restored else None
       if time.monotonic()-return_log>=4:
        self.diagnostic(im,{**data,'map_surface_scores':map_surface_scores(im),'map_template_scores':[self.det.score(im,'map_tree'),self.det.score(im,'map_bridge')],'ocr_map':bool(ocr_map),'escape_success_seen':escape_success,'hud_only_frames':hud_frames,'return_stable':stable},'return')
        self.say(f'确认逃跑结果：菜单={bool(button)}，成功逃脱提示={escape_success}，地图恢复={restored}（图像={on_map}，文字={bool(ocr_map)}，连续HUD帧={hud_frames}）')
        return_log=time.monotonic()
       limit=WALK_ESCAPE_RETRIES if walk else 2
       if button and escape_retries>=limit and time.monotonic()-last_escape_click>=20:
        # Server stalls (2026-10-27 21:59) can hold the menu for a while: give the game 20 s, not 4.
        raise RuntimeError(f'已发送{limit+1}次逃跑点击，又等了 20 秒菜单仍在；已停止，请检查是否有遮挡或游戏未接收点击')
       if button and time.monotonic()-last_escape_click>=2 and escape_retries<limit:
        guard=self.original_flash_guard(im,button)
        if guard['matched']:raise RuntimeError('重试逃跑前原闪光规则命中，已停止')
        if guard['escape_image_hit']:
         fresh=self.check_verification(self.capture())
         if not escape_region(fresh):raise RuntimeError('重试前无法再次确认逃跑菜单，已停止')
         area=escape_button_area(escape_region(fresh))
         (self.slow_click if escape_retries>=1 else self.click)(area);escape_retries+=1;last_escape_click=time.monotonic();deadline=time.monotonic()+35
         self.say(f'逃跑菜单仍在{"（可能没逃掉）" if walk else ""}，已复查并发送重试点击（{escape_retries}/{limit}{"，慢速点击" if escape_retries>=2 else ""}），等待游戏确认')
       if stable>=2:
        rounds+=1;self.total_rounds+=1
        if self.pp_run:due=self.pp_progress.encounter_done();self.say(f'第{rounds}轮完成；PP {self.pp_progress.completed}/6')
        elif walk:
         due=False;self.say(f'第{rounds}只完成（走路单遇）')
         if getattr(self,'lure_check',False):
          # 2026-11-13 user: the lure was often found off. Two battles in a row without 「香水步数」 = off.
          self._lure_missing=0 if lure_seen else getattr(self,'_lure_missing',0)+1
          if self._lure_missing==1:self.say('  这一只的战斗画面里没看到「香水步数」，下一只再确认一次')
          if self._lure_missing>=2:
           self._lure_missing=0;raise RuntimeError('香水没开：连续 2 只战斗画面里都没看到「香水步数」，已停止；开好香水再按开始')
         lure_seen=False
        else:due=False;self.say(f'第{rounds}轮完成（PP果定点：不回院，一直刷）')
        stable=0
        ui=self.blocking_ui(im,data)
        if ui and self.lure_prompt(data,ui):ui=None
        if ui:raise RuntimeError(f'逃跑后画面被「{screen_state.LABELS[ui]}」挡住了，先处理再继续')
        if due:
         if self.pp_run:self.recover_pp()
         else:raise RuntimeError('六次遭遇完成，PP恢复未启用，已停止')
        if self.channel_run and self.elapsed>=self.next_switch:self.switch();state='ready';deadline=time.monotonic()+45
        elif walk:state='walk';walk_since=time.monotonic();deadline=time.monotonic()+45
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
