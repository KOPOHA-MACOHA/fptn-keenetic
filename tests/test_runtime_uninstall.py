"""Removal targets only owned runtime files and leaves firmware/DNS/config intact."""
import os,shutil,unittest
from pathlib import Path
import test_native_service
ROOT=Path(__file__).resolve().parents[1]
class RuntimeUninstallTest(unittest.TestCase):
    setUp=test_native_service.NativeServiceTest.setUp
    cmd=test_native_service.NativeServiceTest.cmd
    prepare_native_client=test_native_service.NativeServiceTest.prepare_native_client
    def prepare(self):
        if not shutil.which('php') and not shutil.which('php-cgi'):self.skipTest('PHP required in Actions')
        root=self.prepare_native_client();lib=root/'libexec/fptn';conf=root/'etc/fptn'
        for source,target in [('fptn-web-auth.sh','web-auth.sh'),('fptn-web-auth.php','web-auth.php'),('fptn-web-operations.php','web-operations.php')]:shutil.copyfile(ROOT/'scripts'/source,lib/target)
        (lib/'.runtime-managed').touch();(lib/'foreign-file').write_text('keep')
        web=root/'share/fptn-web';web.mkdir(parents=True);(web/'.fptn-web-owned').touch();(web/'index.html').write_text('panel');(web/'custom-file').write_text('keep')
        other=root/'share/nfqws';other.mkdir();(other/'index.html').write_text('other panel')
        (conf/'web-auth.json').write_text('{"username":"mine","hash":"secret-hash"}')
        for directory in ['etc/lighttpd/conf.d','etc/init.d','bin']:(root/directory).mkdir(parents=True,exist_ok=True)
        (root/'etc/lighttpd/conf.d/95-fptn.conf').write_text('# Managed by fptn-web-ui\n')
        (root/'etc/lighttpd/lighttpd.conf').write_text('shared config')
        (root/'etc/lighttpd/conf.d/90-smartdns.conf').write_text('smartdns original')
        init=root/'etc/init.d/S80lighttpd';init.write_text('#!/bin/sh\nexit 1\n');init.chmod(0o700)
        fptn_init=root/'etc/init.d/S90fptn';fptn_init.write_text('# Managed by fptn-native\n');(conf/'autostart').touch()
        (root/'bin/fptnctl').write_text('placeholder')
        for name in ['lighttpd','pidof']:
            p=self.bin/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o700)
        return root
    def test_uninstall_preserves_credentials_firmware_markers_and_other_apps(self):
        root=self.prepare();result=self.cmd('uninstall');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for name in ['bin/fptnctl','libexec/fptn/fptn-client-cli','share/fptn-web/index.html','etc/lighttpd/conf.d/95-fptn.conf','etc/init.d/S90fptn','etc/fptn/autostart']:
            self.assertFalse((root/name).exists(),name)
        for name in ['etc/fptn/token','etc/fptn/server','etc/fptn/web-auth.json','etc/fptn/native-interface','etc/fptn/native-setup-complete','share/nfqws/index.html','share/fptn-web/custom-file','libexec/fptn/foreign-file','etc/lighttpd/conf.d/90-smartdns.conf']:
            self.assertTrue((root/name).exists(),name)
        self.assertEqual((root/'etc/lighttpd/lighttpd.conf').read_text(),'shared config')
    def test_foreign_fragment_prevents_removal(self):
        root=self.prepare();(root/'etc/lighttpd/conf.d/95-fptn.conf').write_text('foreign')
        result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0)
        self.assertTrue((root/'libexec/fptn/fptn-client-cli').exists());self.assertTrue((root/'etc/fptn/autostart').exists())
    def test_active_operation_prevents_removal(self):
        root=self.prepare();lock=root/'var/run/fptn-web/action';lock.mkdir(parents=True);(lock/'action').write_text('save')
        result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0)
        self.assertTrue((root/'bin/fptnctl').exists());self.assertTrue((lock/'action').exists())
