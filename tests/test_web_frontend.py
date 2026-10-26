"""Run the actual UI script against a small DOM substitute, without a listener."""
from pathlib import Path
import shutil
import os
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
NODE=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const initial={'mtu-mode':'auto','auth-choice':'panel','fallback-budget':'60',browser:'yandex',mode:'obfuscation','selection-mode':'manual',strategy:'dual-rolling-tunnel',sni:'google.com'};
class Element{
 constructor(id){this.id=id;this.value=initial[id]||'';this.children=[];this.listeners={};this.dataset={};this.disabled=false;this.hidden=false;this.textContent='';this.className='';this.classList={add:()=>{},toggle:()=>{}};}
 replaceChildren(...nodes){this.children=nodes;if(['server','profile'].includes(this.id))this.value=nodes[0]?.value||'';}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(name,value){this[name]=value;}
 addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
 dispatch(name){for(const fn of this.listeners[name]||[])fn({preventDefault(){}});}
 focus(){this.focused=true;}
 get options(){return this.children;}
}
const ids=[...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
const elements=Object.fromEntries(ids.map(id=>[id,new Element(id)]));
for(const tag of html.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g))elements[tag[1]].hidden=/\shidden(?:[\s=>])/.test(tag[0]);
for(const id of ['start','stop','restart'])elements[id].dataset.command=id;
const document={hidden:false,documentElement:new Element('root'),getElementById:id=>{assert(elements[id],'Missing actual HTML element '+id);return elements[id];},createElement:tag=>new Element(tag),addEventListener(){},querySelectorAll(selector){
 if(selector==='[data-command]')return ['start','stop','restart'].map(id=>elements[id]);
 if(selector.includes('#dashboard input'))return ['server','profile','mode','strategy','selection-mode','sni','token','browser'].map(id=>elements[id]);
 throw new Error('Unexpected selector '+selector);
}};
const settings={capabilities:{schema:1,clientVersion:'0.4.0',methods:['obfuscation','sni-spoofing-chrome-149','sni-spoofing-chrome-150','sni-spoofing-firefox-151','sni-spoofing-yandex-26-4','sni-spoofing-safari-26-5','sni-spoofing-edge-151'],strategies:['rolling-tunnel','dual-rolling-tunnel','triple-rolling-tunnel','quad-rolling-tunnel']},server:'CZ',servers:['CZ','DE'],connection:{bypass:'obfuscation',strategy:'dual-rolling-tunnel',sni:'google.com'},policy:{mode:'manual',favorites:[],excluded:[]}};
const stopped={status:'FPTN остановлен',uiVersion:'0.12.1',clientVersion:'0.4.0',upstreamVersion:'0.4.6',clientBuildRevision:'a'.repeat(40),revision:'1234567890',busy:false,report:null,service:{state:'stopped',message:'Служба остановлена',selectedServer:'',connected:false,supervisorAlive:false,canStart:true,canStop:false,canRestart:false}};
const win={location:{href:''}};win.top=win;win.self=win;
let authState={configured:false,mode:null,rootAvailable:true};const requests=[];
const context={console,document,window:win,Option:class{constructor(text,value){this.text=text;this.value=value;}},sessionStorage:{getItem(){return '';},setItem(){},removeItem(){}},setInterval(){},setTimeout(){return 1;},clearTimeout(){},fetch:async(url,options={})=>{
 let data=settings;
 if(url.includes('?auth-info'))data=structuredClone(authState);
 else if(url.includes('?setup')){const body=JSON.parse(options.body);requests.push({url,body});authState={configured:true,mode:body.mode,rootAvailable:true};data=body.mode==='entware'?{key:'f'.repeat(64)}:{ok:true};}
 else if(url.includes('?login')){requests.push({url,body:JSON.parse(options.body)});data={key:'f'.repeat(64)};}
 else if(url.includes('?account')){
   if(options.body){const body=JSON.parse(options.body);requests.push({url,body});authState={configured:true,mode:body.mode,rootAvailable:true};data={ok:true};}
   else data={mode:'panel',username:'mine',rootAvailable:true};
 }
 else if(url.includes('?updates'))data={checked:true,available:true,version:'0.9.1',clientVersion:'0.4.0'};
 else if(url.includes('?status'))data=stopped;
 else if(url.includes('?logout'))data={ok:true};
 return {ok:true,status:200,json:async()=>structuredClone(data)};
}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
 assert(elements.start.disabled&&elements.stop.disabled,'unknown status must disable controls');
 context.data=stopped;run('applyStatus(data)');await run('loadSettings()');
 assert(!elements.start.disabled&&elements.stop.disabled,'stopped: start only');
 await run('loadUpdates()');assert(!elements['install-update'].hidden&&!elements['install-update'].disabled);assert(elements['update-status'].textContent.includes('0.9.1'));
 assert(elements.versions.textContent.includes('0.4.0')&&elements.versions.textContent.includes('исходный FPTN 0.4.6')&&elements.versions.textContent.includes('aaaaaaa'));
 assert(elements.browser.options.some(option=>option.value==='edge'));elements.browser.value='chrome';elements.browser.dispatch('change');assert(elements.profile.options.some(option=>option.value==='sni-spoofing-chrome-150'));
 assert(elements.strategy.options.some(option=>option.value==='quad-rolling-tunnel'));
 elements['fallback-profile-1'].value='sni-spoofing-edge-151';elements['fallback-profile-2'].value='sni-spoofing-chrome-150';elements['fallback-enabled'].checked=true;assert(run('collectSettings().fallback.profiles').join(',')==='sni-spoofing-edge-151,sni-spoofing-chrome-150');elements['fallback-enabled'].checked=false;
 assert(elements['mtu-mode'].value==='auto'&&elements.mtu.value==='1420'&&elements['out-interface'].value==='');
 assert(elements['server-list'].children.length===2,'roles must be rendered');
 assert(elements['server-list'].children[0].children[1].textContent==='☆');
 const running=structuredClone(stopped);running.service={state:'running',message:'Подключён',selectedServer:'CZ',connected:true,supervisorAlive:true,canStart:false,canStop:true,canRestart:true};
 context.data=running;run('applyStatus(data)');
 assert(elements.start.disabled&&!elements.stop.disabled,'running: stop only');
 assert(elements['active-server'].textContent==='CZ');
 running.service.effectiveConnection={bypass:'sni-spoofing-chrome-149',sni:'example.net',strategy:'dual-rolling-tunnel'};context.data=running;run('applyStatus(data)');assert(elements['effective-connection'].textContent.includes('chrome-149')&&elements['effective-connection'].textContent.includes('example.net'));assert(elements.mode.value==='obfuscation','effective fallback must not overwrite saved settings');
 elements['mtu-mode'].value='custom';elements['mtu-mode'].dispatch('change');elements.mtu.value='1360';elements['out-interface'].value='eth2.2';elements.mtu.dispatch('input');
 assert(run('collectSettings().connection.mtu')===1360&&run('collectSettings().connection.outInterface')==='eth2.2');
 elements.server.value='DE';elements.server.dispatch('change');
 assert(!elements.unsaved.hidden&&!elements.save.disabled,'draft must be visible and applicable');
 assert(elements.save.textContent==='Применить и переподключить');
 assert(elements['active-server'].textContent==='CZ','selection must not pretend to switch the active connection');
 assert(elements['check-servers'].disabled,'check must not use unsaved connection parameters');
 assert(elements['install-update'].disabled,'update must not discard unsaved settings');
 elements['server-list'].children[0].children[1].dispatch('click');
 assert(run('favorites.has("CZ")'));
 elements['server-list'].children[0].children[2].dispatch('click');
 assert(run('excluded.has("CZ")&&!favorites.has("CZ")'));
 assert(elements['server-list'].children[0].children[0].children[1].textContent.includes('не проверяется'));
 await run('loadSettings()');assert(elements.unsaved.hidden);
 running.report={checked_at:123,complete:true,results:[{name:'CZ',ok:true,latency_ms:42,code:200},{name:'DE',ok:false,latency_ms:5000,code:0}]};context.data=running;run('applyStatus(data)');
 assert(run('healthNodes.get("CZ").textContent').includes('42 мс'));
 assert(run('healthNodes.get("DE").textContent')==='Вход не удался');
 context.data={...running,uiVersion:'old'};run('applyStatus(data)');
 assert(!elements['version-mismatch'].hidden&&elements.start.disabled&&elements.stop.disabled);
 run('showOffline(new Error("offline"))');assert(elements['status-title'].textContent==='Нет связи с панелью');assert(elements.start.disabled&&elements.stop.disabled);
 // Real browser first-run events, followed by the normal login form.
 authState={configured:false,mode:null,rootAvailable:true};await run('loadAuthInfo()');
 assert(elements['login-title'].textContent==='Первый запуск');assert(!elements['confirm-wrap'].hidden);
 assert(elements['login-submit'].textContent==='Создать учётную запись');
 elements.username.value='mine';elements.password.value='abcd';elements['confirm-password'].value='abcd';
 await elements['login-form'].listeners.submit[0]({preventDefault(){}});
 assert(requests.at(-1).url==='api.cgi?setup');assert(requests.at(-1).body.confirm==='abcd');
 assert(elements['login-title'].textContent==='Вход в панель');assert(elements['confirm-wrap'].hidden);
 assert(elements.password.value===''&&elements['confirm-password'].value==='');
 elements.password.value='abcd';await elements['login-form'].listeners.submit[0]({preventDefault(){}});
 assert(requests.at(-1).url==='api.cgi?login');assert(!elements.dashboard.hidden);
 // Existing root credentials use the same first-run screen without a password creation prompt.
 authState={configured:false,mode:null,rootAvailable:true};await run('loadAuthInfo()');
 elements['auth-choice'].value='entware';elements['auth-choice'].dispatch('change');
 assert(elements.username.value==='root'&&elements.username.readOnly);assert(elements['confirm-wrap'].hidden);
 assert(elements['password-label'].textContent==='Пароль root Entware');
 elements.password.value='existing-root-pass';await elements['login-form'].listeners.submit[0]({preventDefault(){}});
 assert(requests.at(-1).body.mode==='entware');assert(!elements.dashboard.hidden);
 // Credential changes use the synchronous private endpoint and clear secrets on exit.
 await run('loadAccount()');elements['account-mode'].value='panel';elements['account-mode'].dispatch('change');
 elements['account-current-password'].value='oldpass';elements['account-username'].value='next';elements['account-password'].value='newpass';elements['account-confirm'].value='newpass';
 await elements['account-form'].listeners.submit[0]({preventDefault(){}});
 assert(requests.at(-1).url==='api.cgi?account');assert(requests.at(-1).body.currentPassword==='oldpass');
 assert(elements.dashboard.hidden&&elements['account-password'].value===''&&elements['account-current-password'].value==='');
 // A dead web operation exposes recovery while keeping normal commands disabled.
 context.data={...stopped,busy:true,operationState:{stale:true,canRecover:true,message:'Сохранение прервано'}};
 run('lastBusy=true;applyStatus(data)');assert(!elements['recover-operation'].hidden&&!elements['recover-operation'].disabled);
 assert(elements.start.disabled&&elements.stop.disabled);assert(elements.operation.textContent==='Сохранение прервано');
 console.log('Browser account changes, recovery controls, setup, root login and status: PASS');
})().catch(error=>{console.error(error);process.exit(1);});
'''


class FrontendTest(unittest.TestCase):
    def test_asset_urls_and_payload_checksums_match_actual_files(self):
        import sys
        subprocess.run([sys.executable,str(ROOT/'scripts/package-web.py'),'--check'],check=True,capture_output=True)

    def test_entry_renders_without_path_or_external_utilities(self):
        sh=shutil.which('sh')
        result=subprocess.run([sh,str(ROOT/'web/index.cgi')],env={**os.environ,'PATH':'','SCRIPT_FILENAME':'/wrong/inherited/path'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('data-ui-version="0.12.1"',result.stdout)
        self.assertIn('Cache-Control: no-store',result.stdout)

    def test_real_ui_status_buttons_roles_drafts_and_versions(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'test.js';script.write_text(NODE)
            result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_startup_session_never_flashes_login_and_preserves_key_on_network_error(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        html=(ROOT/'web/index.html').read_text()
        self.assertRegex(html,r'<section[^>]*id="login"[^>]*\shidden[\s>]')
        harness=NODE.split('(async()=>{',1)[0]
        harness=harness.replace("getItem(){return '';}","getItem(){return process.env.SCENARIO==='none'?'':'s'.repeat(64);}")
        harness=harness.replace('removeItem(){}','removeItem(){removed=true;}')
        harness=harness.replace('let authState={configured:false,mode:null,','let authState={configured:true,mode:"panel",')
        harness=harness.replace('const context={', 'let removed=false,firstStatus=false,firstInfo=false,resolveStatus,resolveInfo;const statusGate=new Promise(r=>resolveStatus=r),infoGate=new Promise(r=>resolveInfo=r);\nconst context={',1)
        harness=harness.replace(' let data=settings;', ''' if(url.includes('?status')&&!firstStatus){firstStatus=true;await statusGate;if(process.env.SCENARIO==='expired')return {ok:false,status:401,json:async()=>({error:'expired'})};if(process.env.SCENARIO==='offline')throw new Error('network unavailable');}
 if(url.includes('?auth-info')&&!firstInfo){firstInfo=true;await infoGate;}
 let data=settings;''',1)
        scenario=r'''
(async()=>{
 const tick=()=>new Promise(r=>setImmediate(r));
 assert(elements.login.hidden&&elements.dashboard.hidden&&!elements['auth-loading'].hidden,'initial paint must show only neutral loading');
 const scenario=process.env.SCENARIO;
 if(scenario==='none'){
   assert(firstInfo&&!firstStatus);resolveInfo();await tick();
   assert(!elements.login.hidden&&elements.dashboard.hidden&&elements['auth-loading'].hidden);
 }else{
   assert(firstStatus&&!firstInfo);resolveStatus();await tick();
   if(scenario==='valid'){
     assert(elements.login.hidden&&!elements.dashboard.hidden&&elements['auth-loading'].hidden);assert(!firstInfo&&!removed);
   }else if(scenario==='expired'){
     assert(firstInfo&&removed);assert(elements.login.hidden&&elements.dashboard.hidden&&!elements['auth-loading'].hidden);
     resolveInfo();await tick();assert(!elements.login.hidden&&elements['auth-loading'].hidden);
   }else{
     assert(!removed&&run('key')==='s'.repeat(64));assert(elements.login.hidden&&elements.dashboard.hidden&&!elements['auth-retry'].hidden);
     await elements['auth-retry'].listeners.click[0]();
     assert(elements.login.hidden&&!elements.dashboard.hidden&&elements['auth-loading'].hidden);
   }
 }
 console.log('startup '+scenario+': PASS');
})().catch(error=>{console.error(error);process.exit(1);});
'''
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'session.js';script.write_text(harness+scenario)
            for case in ['valid','none','expired','offline']:
                with self.subTest(case=case):
                    result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],env={**os.environ,'SCENARIO':case},text=True,capture_output=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
