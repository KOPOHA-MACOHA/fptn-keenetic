"""Full removal erases owned state and interface, preserving other applications."""
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
        for source,target in [('fptn-web-auth.sh','web-auth.sh'),('fptn-web-auth.php','web-auth.php'),('fptn-web-operations.php','web-operations.php'),('fptn-private-state.php','private-state.php')]:shutil.copyfile(ROOT/'scripts'/source,lib/target)
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
        ndmc=self.bin/'ndmc'
        ndmc.write_text(f'''#!/bin/sh
printf '%s\\n' "$2" >> '{self.root}/ndmc-remove-trace'
case "$2" in
 'show interface')
   echo 'id: OpkgTun7'
   [ -f '{self.root}/deleted-interface' ] || echo 'id: OpkgTun0' ;;
 'show interface OpkgTun0')
   if [ -f '{self.root}/foreign-interface' ]; then echo 'description: Foreign'; else echo 'description: FPTN'; fi
   echo 'id: OpkgTun0'; echo 'type: OpkgTun'; echo 'address: 10.0.0.1'; echo 'mask: 255.255.255.255' ;;
 'no interface OpkgTun0') [ ! -f '{self.root}/delete-fails' ] || exit 1; touch '{self.root}/deleted-interface' ;;
 'system configuration save') [ ! -f '{self.root}/save-fails' ] ;;
 *) exit 1 ;;
esac
''');ndmc.chmod(0o700)
        return root
    def test_uninstall_removes_owned_interface_credentials_and_archives_preserving_other_apps(self):
        root=self.prepare()
        for folder in ['etc/fptn/web-install-backup.abc123','var/run/fptn-web/sessions','var/run/fptn-capabilities','var/log/fptn','etc/opkg','var/opkg-lists']:(root/folder).mkdir(parents=True,exist_ok=True)
        for name in ['etc/fptn/web-install-backup.abc123/token','var/run/fptn-web/sessions/session','var/run/fptn-capabilities/cache','var/log/fptn/fptn-client-cli.log','var/log/fptn/fptn-client-cli.1.log','var/opkg-lists/fptn_keenetic','bin/fptnctl.previous']:(root/name).write_text('private')
        (root/'etc/opkg/fptn-keenetic.conf').write_text('# Managed by FPTN package channel\n')
        (root/'var/log/fptn/other.log').write_text('keep')
        outside=self.root/'external';outside.write_text('preserve')
        (root/'etc/fptn/external').symlink_to(outside)
        result=self.cmd('uninstall');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        for name in ['bin/fptnctl','libexec/fptn/fptn-client-cli','share/fptn-web/index.html','etc/lighttpd/conf.d/95-fptn.conf','etc/init.d/S90fptn','etc/fptn','var/run/fptn-web','var/run/fptn-capabilities','var/log/fptn/fptn-client-cli.log','var/log/fptn/fptn-client-cli.1.log','etc/opkg/fptn-keenetic.conf','var/opkg-lists/fptn_keenetic','bin/fptnctl.previous']:
            self.assertFalse((root/name).exists(),name)
        for name in ['share/nfqws/index.html','share/fptn-web/custom-file','libexec/fptn/foreign-file','etc/lighttpd/conf.d/90-smartdns.conf']:
            self.assertTrue((root/name).exists(),name)
        self.assertEqual((root/'etc/lighttpd/lighttpd.conf').read_text(),'shared config')
        self.assertEqual(outside.read_text(),'preserve');self.assertEqual((root/'var/log/fptn/other.log').read_text(),'keep')
        trace=(self.root/'ndmc-remove-trace').read_text()
        self.assertIn('no interface OpkgTun0',trace);self.assertIn('system configuration save',trace)
        self.assertNotIn('no interface OpkgTun7',trace)
        self.assertFalse(list((root/'tmp').glob('fptn-private-*')))

    def test_foreign_fragment_prevents_removal(self):
        root=self.prepare();(root/'etc/lighttpd/conf.d/95-fptn.conf').write_text('foreign')
        result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0)
        self.assertTrue((root/'libexec/fptn/fptn-client-cli').exists());self.assertTrue((root/'etc/fptn/autostart').exists())
    def test_active_operation_prevents_removal(self):
        root=self.prepare();lock=root/'var/run/fptn-web/action';lock.mkdir(parents=True);(lock/'action').write_text('save')
        result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0)
        self.assertTrue((root/'bin/fptnctl').exists());self.assertTrue((lock/'action').exists())

    def test_foreign_interface_prevents_removal_before_stop(self):
        root=self.prepare();(self.root/'foreign-interface').touch()
        result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertTrue((root/'etc/fptn/token').exists());self.assertTrue((root/'bin/fptnctl').exists())
        self.assertFalse((self.root/'deleted-interface').exists())
        self.assertTrue((root/'etc/lighttpd/conf.d/95-fptn.conf').exists())

    def test_firmware_delete_or_save_failure_keeps_configuration_for_retry(self):
        for failure in ['delete-fails','save-fails']:
            with self.subTest(failure=failure):
                root=self.prepare();marker=self.root/failure;marker.touch()
                result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertTrue((root/'etc/fptn/token').exists());self.assertTrue((root/'bin/fptnctl').exists())
                marker.unlink()
                result=self.cmd('uninstall');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertFalse((root/'etc/fptn').exists())
                # Isolate the second fixture in this test.
                shutil.rmtree(root);(self.root/'deleted-interface').unlink(missing_ok=True)

    def test_symlinked_configuration_directory_is_not_traversed(self):
        root=self.prepare();conf=root/'etc/fptn';external=self.root/'external-conf'
        conf.rename(external);conf.symlink_to(external,target_is_directory=True)
        result=self.cmd('uninstall');self.assertNotEqual(result.returncode,0)
        self.assertEqual((external/'token').read_text(),'secret-token\n')
        self.assertFalse((self.root/'deleted-interface').exists())

    def test_removes_saved_nonzero_interface_without_guessing_zero(self):
        root=self.prepare();(root/'etc/fptn/native-interface').write_text('OpkgTun7\n')
        ndmc=self.bin/'ndmc';ndmc.write_text(ndmc.read_text().replace('OpkgTun7','OpkgTun8').replace('OpkgTun0','OpkgTun7'))
        result=self.cmd('uninstall');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        trace=(self.root/'ndmc-remove-trace').read_text()
        self.assertIn('no interface OpkgTun7',trace);self.assertNotIn('no interface OpkgTun0',trace);self.assertNotIn('no interface OpkgTun8',trace)

    def test_running_owned_client_is_stopped_before_data_removal(self):
        root=self.prepare()
        try:
            started=self.cmd('start');self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            record=(root/'var/run/fptn-keenetic/client.pid').read_text().split()
            result=self.cmd('uninstall');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            stat=Path('/proc/'+record[0]+'/stat')
            if stat.exists():self.assertEqual(stat.read_text().rsplit(') ',1)[1].split()[0],'Z')
            self.assertFalse((root/'var/run/fptn-keenetic').exists());self.assertFalse((root/'etc/fptn').exists())
        finally:
            if (root/'etc/fptn').exists():self.cmd('stop')
