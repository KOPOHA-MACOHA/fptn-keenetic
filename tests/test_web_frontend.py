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
const stopped={status:'FPTN остановлен',uiVersion:'0.18.4',clientVersion:'0.4.0',upstreamVersion:'0.4.6',clientBuildRevision:'a'.repeat(40),revision:'1234567890',busy:false,report:null,service:{state:'stopped',message:'Служба остановлена',selectedServer:'',connected:false,supervisorAlive:false,canStart:true,canStop:false,canRestart:false}};
const win={location:{href:''}};win.top=win;win.self=win;
let authState={configured:false,mode:null,rootAvailable:true};const requests=[];
const context={AbortController,console,document,window:win,Option:class{constructor(text,value){this.text=text;this.value=value;}},sessionStorage:{getItem(){return '';},setItem(){},removeItem(){}},setInterval(){},setTimeout(){return 1;},clearTimeout(){},fetch:async(url,options={})=>{
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
 const unconfigured={...stopped,configured:false,interfaceReady:false};context.data=unconfigured;run('applyStatus(data)');
 assert(!elements.onboarding.hidden&&!elements['setup-interface'].hidden&&elements.start.disabled&&elements['check-servers'].disabled);
 const savedServers=settings.servers,savedServer=settings.server;settings.configured=false;settings.servers=[];settings.server='';await run('loadSettings()');
 assert(!elements['token-details'].hidden&&elements['token-summary'].textContent.includes('Добавить'));assert(elements['server-list'].children.length===0);
 settings.configured=true;settings.servers=savedServers;settings.server=savedServer;context.data={...stopped,configured:true,interfaceReady:true};run('applyStatus(data)');await run('loadSettings()');
 assert(elements.onboarding.hidden&&elements['setup-interface'].hidden&&!elements.start.disabled);

 await run('loadUpdates()');assert(!elements['install-update'].hidden&&!elements['install-update'].disabled);assert(elements['update-status'].textContent.includes('0.9.1'));
 assert(elements.versions.textContent.includes('0.4.0')&&elements.versions.textContent.includes('исходный FPTN 0.4.6')&&elements.versions.textContent.includes('aaaaaaa'));
 assert(elements.browser.options.some(option=>option.value==='edge'));elements.browser.value='chrome';elements.browser.dispatch('change');assert(elements.profile.options.some(option=>option.value==='sni-spoofing-chrome-150'));
 assert(elements.strategy.options.some(option=>option.value==='quad-rolling-tunnel'));
 elements['fallback-profile-1'].value='sni-spoofing-edge-151';elements['fallback-profile-2'].value='sni-spoofing-chrome-150';elements['fallback-enabled'].checked=true;assert(run('collectSettings().fallback.profiles').join(',')==='sni-spoofing-edge-151,sni-spoofing-chrome-150');elements['fallback-enabled'].checked=false;
 settings.connection.ipv6=true;await run('loadSettings()');assert(elements.ipv6.checked&&run('collectSettings().connection.ipv6'));settings.connection.ipv6=false;await run('loadSettings()');
 assert(elements['mtu-mode'].value==='auto'&&elements.mtu.value==='1420'&&elements['out-interface'].value==='');
 assert(elements['server-list'].children.length===2,'roles must be rendered');
 assert(elements['server-list'].children[0].children[1].textContent==='☆');
 const running=structuredClone(stopped);running.service={state:'running',message:'Подключён',selectedServer:'CZ',connected:true,supervisorAlive:true,canStart:false,canStop:true,canRestart:true};
 context.data=running;run('applyStatus(data)');
 assert(elements.start.disabled&&!elements.stop.disabled,'running: stop only');
 assert(elements['active-server'].textContent==='CZ'&&elements['active-server-label'].textContent==='Подключено к');
 // Automatic fallback updates reality, not the user's saved preference or draft.
 running.service.preferredServer='CZ';running.service.selectionMode='manual';running.service.selectedServer='DE';
 running.service.lastSwitch={from:'CZ',to:'DE',reason:'health-failed',at:100};
 running.service.health={state:'missed',failures:2,threshold:3,nextCheckAt:200};
 context.data=running;run('applyStatus(data)');
 assert(elements['active-server'].textContent==='DE'&&elements.server.value==='CZ');
 assert(elements['selection-summary'].textContent.includes('CZ')&&elements['selection-summary'].textContent.includes('другой'));
 assert(elements['switch-summary'].textContent.includes('CZ → DE')&&elements['health-status'].textContent.includes('2/3'));
 running.service.selectedServer='CZ';delete running.service.lastSwitch;delete running.service.health;
 settings.capabilities.dataPlaneHealth=true;settings.connection.health={enabled:true,interval:120,timeout:4,failures:2};await run('loadSettings()');
 assert(elements['health-enabled'].checked&&run('collectSettings().connection.health.interval')===120);
 settings.capabilities.dataPlaneHealth=false;delete settings.connection.health;await run('loadSettings()');
 assert(elements['health-enabled'].disabled&&!elements['health-unsupported'].hidden);
 running.panelOrigin='http://192.168.1.1:1111';running.portChange={id:'a'.repeat(32),oldOrigin:running.panelOrigin,newOrigin:'http://192.168.1.1:2222',deadline:500};
 win.location.origin='http://192.168.1.1:1111';context.data=running;run('applyStatus(data);setControls()');
 assert(!elements['port-transition'].hidden&&elements['port-link'].href==='http://192.168.1.1:2222'&&elements['port-confirm'].hidden);
 win.location.origin='http://192.168.1.1:2222';run('setControls()');assert(!elements['port-confirm'].hidden);
 delete running.portChange;delete running.panelOrigin;context.data=running;run('applyStatus(data);setControls()');

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
        self.assertIn('data-ui-version="0.18.4"',result.stdout)
        self.assertIn('Cache-Control: no-store',result.stdout)

    def test_real_ui_status_buttons_roles_drafts_and_versions(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'test.js';script.write_text(NODE)
            result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_update_modal_blocks_until_result_and_handles_disconnect_rollback_and_ambiguity(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        harness=NODE.split('(async()=>{',1)[0]
        scenario=r'''
const base={...stopped,configured:true,interfaceReady:true,revision:'a'.repeat(40)};
run("beginUpdate({revision:'c'.repeat(40),version:'0.19.0'})");
assert(!elements['update-modal'].hidden&&elements['panel-shell'].inert);
assert(!elements['update-reload'].hidden&&elements['update-close'].hidden);
context.data={...base,busy:true,operation:'update'};run('applyStatus(data)');
run('showOffline(new Error("network"))');assert(elements['panel-shell'].inert&&elements['update-close'].hidden);
assert(elements['update-message'].textContent.includes('недоступна'));
context.data={...base,revision:'c'.repeat(40),uiVersion:'0.19.0',result:{ok:true,action:'update',message:'installed'}};run('applyStatus(data)');
assert(!elements['update-reload'].hidden&&elements['update-progress'].hidden);
assert(elements['update-title'].textContent==='Обновление установлено');assert(elements['panel-shell'].inert);
elements['update-close'].dispatch('click');assert(!elements['panel-shell'].inert);
run("beginUpdate({revision:'c'.repeat(40)});updateWatch.accepted=true");
context.data={...base,result:{ok:false,message:'rolled back'}};run('applyStatus(data)');
assert(elements['update-title'].textContent==='Обновление не завершено');assert(elements['update-message'].textContent==='rolled back');
elements['update-close'].dispatch('click');
run("beginUpdate({revision:'c'.repeat(40)});updateWatch.uncertain=true;renderUpdate('unknown')");
context.data={...base,result:{ok:false,message:'old result'}};run('applyStatus(data)');
assert(!run('updateWatch.finished')&&elements['panel-shell'].inert&&elements['update-close'].hidden&&!elements['update-reload'].hidden);
// Repairing a client can keep the same UI revision: an old success is not proof.
run("finishUpdate(false,'reset')");elements['update-close'].dispatch('click');
run("beginUpdate({revision:'a'.repeat(40)});updateWatch.uncertain=true;renderUpdate('unknown')");
context.data={...base,result:{ok:true,action:'update',message:'old success'}};run('applyStatus(data)');
assert(!run('updateWatch.finished')&&elements['panel-shell'].inert);
context.data={...base,busy:true,operation:'update',operationState:{stale:true,canRecover:true,message:'interrupted'}};run('applyStatus(data)');
assert(run('updateWatch.finished')&&!elements['update-close'].hidden);
console.log('Update modal: confirmed completion, rollback, interruption and uncertain POST: PASS');
'''
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'modal.js';script.write_text(harness+scenario)
            result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_update_title_distinguishes_client_panel_and_combined_release(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        harness=NODE.split('(async()=>{',1)[0]
        scenario=r'''
context.data={...stopped,uiVersion:'0.18.4',clientVersion:'0.7.0'};run('lastData=data');
run("beginUpdate({revision:'c'.repeat(40),version:'0.18.4',clientVersion:'0.8.0'})");
assert(elements['update-title'].textContent==='Обновление клиента до 0.8.0');
assert(elements['update-detail'].textContent.includes('Клиент: 0.8.0')&&elements['update-detail'].textContent.includes('Панель: 0.18.4'));
assert(!elements['update-reload'].hidden&&elements['update-close'].hidden&&elements['panel-shell'].inert);
context.fetch=()=>{throw Error('reload must never submit or cancel an operation');};
elements['update-reload'].dispatch('click');assert(win.location.href.startsWith('/?v='));assert(!run('updateWatch.finished'));
run("finishUpdate(false,'reset')");elements['update-close'].dispatch('click');
context.data={...stopped,clientVersion:'0.8.0'};run('lastData=data');
run("beginUpdate({revision:'c'.repeat(40),version:'0.18.5',clientVersion:'0.8.0'})");
assert(elements['update-title'].textContent==='Обновление панели до 0.18.5');
run("finishUpdate(false,'reset')");elements['update-close'].dispatch('click');
context.data={...stopped,clientVersion:'0.7.0'};run('lastData=data');
run("beginUpdate({revision:'c'.repeat(40),version:'0.18.5',clientVersion:'0.8.0'})");
assert(elements['update-title'].textContent==='Обновление клиента до 0.8.0 и панели до 0.18.5');
console.log('Separate client/panel targets and safe reload while update runs: PASS');
'''
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'targets.js';script.write_text(harness+scenario)
            result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_api_timeouts_release_polling_and_do_not_repeat_uncertain_update(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        harness=NODE.split('(async()=>{',1)[0]
        scenario=r'''
(async()=>{
 const tick=()=>new Promise(r=>setImmediate(r));await tick();
 let abortTimer,cleared=0,calls=0;
 context.setTimeout=(fn,ms)=>{assert(ms===15000);abortTimer=fn;return 7;};
 context.clearTimeout=id=>{assert(id===7);cleared++;};
 run("key='a'.repeat(64);sessionReady=true");
 const stalled=(url,options)=>{calls++;return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));};
 context.fetch=stalled;
 let waiting=run('poll()');assert(run('polling'));abortTimer();await waiting;
 assert(!run('polling')&&cleared===1&&calls===1);assert(elements['status-message'].textContent.includes('15 секунд'));
 // A response can also hang after headers: the deadline must cover its JSON body.
 context.fetch=async(url,options)=>({ok:true,status:200,json:()=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('body aborted')),{once:true}))});
 waiting=run('poll()');await tick();abortTimer();await waiting;assert(!run('polling')&&cleared===2);
 context.fetch=async()=>({ok:true,status:200,json:async()=>structuredClone(stopped)});
 await run('poll()');assert(run('lastData.revision')===stopped.revision&&cleared===3,'polling must recover without reload');
 // Timing out the POST means acceptance is unknown. Never re-submit or announce success.
 context.fetch=stalled;calls=0;context.target={version:'0.18.4',clientVersion:'0.8.0',revision:'c'.repeat(40)};run('updateInfo=target');
 waiting=run("command({action:'update'})");abortTimer();await waiting;
 assert(calls===1&&run('updateWatch.uncertain')&&!run('updateWatch.finished'));
 assert(elements['panel-shell'].inert&&!elements['update-reload'].hidden&&elements['update-close'].hidden);
 console.log('Header/body deadlines, polling recovery, and no repeated update POST: PASS');
})().catch(error=>{console.error(error);process.exit(1);});
'''
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'timeouts.js';script.write_text(harness+scenario)
            result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_navigation_token_favicon_and_update_controls_have_expected_locations(self):
        html=(ROOT/'web/index.html').read_text()
        self.assertLess(html.index('id="updates"'),html.index('class="tabs"'))
        self.assertLess(html.index('id="token-details"'),html.index('id="selection-mode"'))
        self.assertNotIn('<details id="token-details"',html)
        self.assertGreater(html.index('class="routing footer-info"'),html.index('id="view-logs"'))
        self.assertIn('favicon.svg?v=',html)
        import xml.etree.ElementTree as ET
        self.assertEqual(ET.fromstring((ROOT/'web/favicon.svg').read_text()).tag,'{http://www.w3.org/2000/svg}svg')

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
