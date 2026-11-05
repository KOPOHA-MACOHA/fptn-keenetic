import json,subprocess,unittest
from unittest.mock import patch
import client_fixture,test_native_service
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class HealthSettingsTest(unittest.TestCase):
 setUp=test_native_service.NativeServiceTest.setUp
 cmd=test_native_service.NativeServiceTest.cmd
 prepare_native_client=test_native_service.NativeServiceTest.prepare_native_client
 def prepare(self,support=True):
  helptext=client_fixture.help_text()+ ('\n--keenetic-health-interval\n' if support else '')
  with patch('client_fixture.help_text',return_value=helptext):return self.prepare_native_client()
 def validate(self,health):
  return subprocess.run(['sh',str(ROOT/'scripts/fptn-native-service.sh'),'validate-settings'],input=json.dumps({'bypass':'obfuscation','strategy':'dual-rolling-tunnel','sni':'google.com','health':health}),env=self.env,text=True,capture_output=True,timeout=5)
 def test_defaults_and_flags_follow_actual_binary_capability(self):
  root=self.prepare()
  data=json.loads(self.cmd('get-settings').stdout);self.assertEqual(data['health'],{'enabled':True,'interval':60,'timeout':5,'failures':3})
  try:
   started=self.cmd('start');self.assertEqual(started.returncode,0,started.stdout+started.stderr)
   args=(self.root/'client-args').read_text().splitlines()
   self.assertEqual(args[args.index('--keenetic-health-interval')+1],'60')
   self.assertEqual(args[args.index('--keenetic-health-failures')+1],'3')
   status=json.loads(self.cmd('status-json').stdout);self.assertEqual(status['preferredServer'],'CZ');self.assertEqual(status['lastSwitch']['to'],'CZ')
  finally:self.cmd('stop')
 def test_old_binary_is_not_given_new_flags_or_allowed_to_enable_health(self):
  self.prepare(False);data=json.loads(self.cmd('get-settings').stdout);self.assertFalse(data['health']['enabled'])
  result=self.validate({'enabled':True,'interval':60,'timeout':5,'failures':3});self.assertNotEqual(result.returncode,0)
 def test_ranges_and_types_fail_before_configuration_mutations(self):
  self.prepare()
  baseline={'enabled':True,'interval':60,'timeout':5,'failures':3}
  for values in [{'interval':29},{'interval':3601},{'interval':True},{'timeout':1},{'timeout':16},{'failures':1},{'failures':11},{'enabled':'yes'}]:
   with self.subTest(values=values):self.assertNotEqual(self.validate({**baseline,**values}).returncode,0)
  self.assertNotEqual(self.validate(None).returncode,0)
  self.assertEqual(self.validate({**baseline,'enabled':False}).returncode,0)
