"""Bound ordinary/incident snapshots on disk, do not remove shiny evidence."""
from pathlib import Path
import time

LIMITS={'normal':50,'error':100}
CAP_BYTES=200*1024*1024

def prune(root):
    root=Path(root)
    entries={tag:sorted((p for p in (root/tag).glob('*.png') if p.is_file()),key=lambda x:x.stat().st_mtime) if (root/tag).exists() else [] for tag in LIMITS}
    for tag,limit in LIMITS.items():
        for p in entries[tag][:-limit]:p.unlink(missing_ok=True)
    all_files=sorted((p for tag in LIMITS for p in entries[tag] if p.exists()),key=lambda x:x.stat().st_mtime)
    total=sum(p.stat().st_size for p in all_files)
    for p in all_files:
        if total<=CAP_BYTES:break
        size=p.stat().st_size;p.unlink(missing_ok=True);total-=size

def save(root, image, kind='normal'):
    if kind not in ('normal','error','shiny'):raise ValueError(kind)
    folder=Path(root)/kind;folder.mkdir(parents=True,exist_ok=True)
    filename=f'{time.time_ns()}.png'
    image.save(folder/filename,optimize=True)
    prune(root)
    return folder/filename

def trim_log(path, days=7):
    path=Path(path)
    if not path.exists():return
    if path.stat().st_size<5*1024*1024:return
    import datetime
    threshold=(datetime.datetime.now()-datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    lines=path.read_text(encoding='utf8',errors='replace').splitlines()
    kept=[line for line in lines if line[:10]>=threshold]
    path.write_text('\n'.join(kept)[-1024*1024:]+'\n',encoding='utf8')
