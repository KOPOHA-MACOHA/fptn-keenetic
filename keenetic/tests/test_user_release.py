"""User releases contain installation files only and reuse validated runtime hashes."""
import copy
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('user_release',ROOT/'scripts/publish-user-release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)

class UserReleaseTest(unittest.TestCase):
 def setUp(self):
  self.runtime='a'*40;self.evidence={'schema':1,'runtimeRevision':self.runtime,'platforms':{}}
  self.assets=[]
  for platform,(arch,feed) in release.feeds.ipk.PLATFORMS.items():
   name='fptn-keenetic_0.18.4-128_'+arch+'.ipk'
   self.evidence['platforms'][platform]={'filename':name,'version':'0.18.4-128','revision':self.runtime,
      'architecture':arch,'feed':feed,'sha256':'b'*64,'indexes':{'Packages':'c'*64,'Packages.gz':'d'*64}}
   self.assets.append({'name':name,'digest':'sha256:'+'b'*64})
  self.assets.extend({'name':n,'digest':'sha256:'+'e'*64} for n in release.pub.ARCHIVES)
  self.assets.extend({'name':n,'digest':'sha256:'+'f'*64} for n in ['brotli','Packages.gz','ipk-evidence.json','runtime-evidence.json'])
 def test_selects_exactly_three_ipks_and_three_complete_archives(self):
  version,selected=release.select_assets({'assets':self.assets},self.evidence,self.runtime)
  self.assertEqual(version,'0.18.4-128');self.assertEqual(len(selected),6)
  self.assertTrue(all(n.endswith(('.ipk','.tar.gz')) for n in selected));self.assertNotIn('brotli',selected)
 def test_mixed_versions_and_corrupt_or_incomplete_files_stop_publication(self):
  wrong=copy.deepcopy(self.evidence);row=wrong['platforms']['aarch64'];row['version']='0.18.4-129';row['filename']=row['filename'].replace('-128_','-129_')
  with self.assertRaises(ValueError):release.select_assets({'assets':self.assets},wrong,self.runtime)
  with self.assertRaises(ValueError):release.select_assets({'assets':self.assets[1:]},self.evidence,self.runtime)
  broken=copy.deepcopy(self.assets);broken[0]['digest']='sha256:'+'0'*64
  with self.assertRaises(ValueError):release.select_assets({'assets':broken},self.evidence,self.runtime)
 def test_notes_distinguish_panel_client_and_package_with_installation_guidance(self):
  text=release.notes(ROOT,{'version':'0.18.4','clientVersion':'0.8.0'},'0.18.4-128')
  for expected in ['0.18.4','0.8.0','0.18.4-128','sh -s opkg','SHA256SUMS','на свой страх и риск']:
   self.assertIn(expected,text)
 def test_decoder_cannot_be_republished_as_latest_by_its_maintenance_workflow(self):
  text=(ROOT/'.github/workflows/publish-decoder.yml').read_text()
  self.assertIn('--prerelease --latest=false',text);self.assertNotIn('  push:',text)
