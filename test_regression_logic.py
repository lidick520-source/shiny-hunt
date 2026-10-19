"""Platform-independent core decision checks; no game input."""
import ast, pathlib, re, unittest
import numpy as np
from PIL import Image
source=ast.parse(pathlib.Path(__file__).with_name('app.py').read_text())
functions={'original_flash_match','map_return_evidence','hud_only_map_text'}
body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in functions]
namespace={'re':re,'np':np}
exec(compile(ast.Module(body=body,type_ignores=[]),'<isolated>', 'exec'),namespace)
class Regression(unittest.TestCase):
 def test_own_shiny_not_single_character(self):
  f=namespace['original_flash_match'];self.assertFalse(f('','闪',True));self.assertTrue(f('闪 光','',True));self.assertFalse(f('闪光','',False))
 def test_battle_cannot_be_map(self):
  f=namespace['map_return_evidence'];im=Image.new('RGB',(1024,768),'white')
  self.assertFalse(f(im,{'text':'轻飘飘 Lv.30','lines':[]},None,True))
  self.assertFalse(f(im,{'text':'成功逃脱！','lines':[]},None,True))
  self.assertFalse(f(im,{'text':'地图','lines':[]},None,False))
  self.assertTrue(f(im,{'text':'地图','lines':[]},None,True))
 def test_hud_only_map_text_from_real_frames(self):
  f=namespace['hud_only_map_text']
  # 02:22:49 real map frame where the image match failed (another player on the patch)
  self.assertTrue(f({'text':'《帆巴市（地）（DriftveilCity）Ch.3E6$21,350,355（星期三，01:31，夏56•（283共X'}))
  # real battle frames
  self.assertFalse(f({'text':"帆巴市（地）（DriftveilCity）Ch.3言6$21,350,355（星期三，00:39，轻飘飘'幽水Lv.29杏轻飘飘幽水Lv.29双弹瓦斯飘？毒使出了烟雾球！"}))
  self.assertFalse(f({'text':'帆巴市（地）Ch.3 $21,350,355 成功逃脱！'}))
  self.assertFalse(f({'text':'帆巴市 Ch.3 $21,350,355 Tottilee派出了双弹瓦斯飘？毒！'}))
  # black transition frame / empty OCR never counts
  self.assertFalse(f({'text':''}));self.assertFalse(f({'text':'其X'}))
if __name__=='__main__':unittest.main()
