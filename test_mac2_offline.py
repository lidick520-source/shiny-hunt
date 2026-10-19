import unittest,tempfile,time
from pathlib import Path
from PIL import Image
from pp_recovery import PPProgress,RecoverySequence
from snapshot_policy import save,prune
class Host:
 def __init__(self):self.actions=[]
 def say(self,t):pass
 def press(self,k):self.actions.append(('press',k))
 def wait(self,s):self.actions.append(('wait',s))
 def hold_key(self,k,s):self.actions.append(('hold',k,s))
class Tests(unittest.TestCase):
 def test_six(self):
  p=PPProgress();self.assertEqual([p.encounter_done() for _ in range(6)],[False]*5+[True])
 def test_flow(self):
  p=PPProgress(completed=6);h=Host();seq=RecoverySequence();seq.execute(h,p)
  self.assertEqual(p.completed,0);self.assertFalse(p.recovering)
  self.assertEqual([a[1] for a in h.actions if a[0]=='press'],[0x34,0x20,0x20,0x20,0x20,0x20])
  self.assertEqual([a[1] for a in h.actions if a[0]=='hold'],[0x53,0x44,0x57,0x44,0x44])
 def test_manual_confirmation_keeps_progress(self):
  p=PPProgress(completed=6);h=Host()
  RecoverySequence().execute(h,p,auto_finish=False)
  self.assertTrue(p.recovering);self.assertEqual(p.completed,6)
  self.assertEqual(p.recovery_index,len(RecoverySequence().steps))
  p.finish();self.assertEqual(p.completed,0)
 def test_resume(self):
  p=PPProgress(completed=6);h=Host();p.begin();p.recovery_index=5
  RecoverySequence().execute(h,p)
  self.assertEqual(p.completed,0);self.assertEqual([a[1] for a in h.actions if a[0]=='press'],[0x20,0x20,0x20])
 def test_snapshot(self):
  with tempfile.TemporaryDirectory() as d:
   image=Image.new('RGB',(8,8))
   for i in range(54):save(d,image,'normal')
   self.assertEqual(len(list((Path(d)/'normal').glob('*.png'))),50)
   save(d,image,'shiny');prune(d)
   self.assertEqual(len(list((Path(d)/'shiny').glob('*.png'))),1)
if __name__=='__main__':unittest.main()
