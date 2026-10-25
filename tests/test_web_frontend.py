"""Run the actual UI script against a small DOM substitute, without a listener."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
NODE=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const initial={browser:'yandex',mode:'obfuscation','selection-mode':'manual',strategy:'dual-rolling-tunnel',sni:'google.com'};
class Element{
 constructor(id){this.id=id;this.value=initial[id]||'';this.children=[];this.listeners={};this.dataset={};this.disabled=false;this.hidden=false;this.textContent='';this.className='';this.classList={add:()=>{},toggle:()=>{}};}
 replaceChildren(...nodes){this.children=nodes;if(['server','profile'].includes(this.id))this.value=nodes[0]?.value||'';}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(name,value){this[name]=value;}
 addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
 dispatch(name){for(const fn of this.listeners[name]||[])fn({preventDefault(){}});}
 get options(){return this.children;}
}
const ids=[...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
const elements=Object.fromEntries(ids.map(id=>[id,new Element(id)]));
for(const id of ['start','stop','restart'])elements[id].dataset.command=id;
const document={hidden:false,documentElement:new Element('root'),getElementById:id=>{assert(elements[id],'Missing actual HTML element '+id);return elements[id];},createElement:tag=>new Element(tag),addEventListener(){},querySelectorAll(selector){
 if(selector==='[data-command]')return ['start','stop','restart'].map(id=>elements[id]);
 if(selector.includes('#dashboard input'))return ['server','profile','mode','strategy','selection-mode','sni','token','browser'].map(id=>elements[id]);
 throw new Error('Unexpected selector '+selector);
}};
const settings={server:'CZ',servers:['CZ','DE'],connection:{bypass:'obfuscation',strategy:'dual-rolling-tunnel',sni:'google.com'},policy:{mode:'manual',favorites:[],excluded:[]}};
const stopped={status:'FPTN остановлен',uiVersion:'0.4.0',clientVersion:'0.4.0',revision:'1234567890',busy:false,report:null,service:{state:'stopped',message:'Служба остановлена',selectedServer:'',connected:false,supervisorAlive:false,canStart:true,canStop:false,canRestart:false}};
const win={location:{href:''}};win.top=win;win.self=win;
const context={console,document,window:win,Option:class{constructor(text,value){this.text=text;this.value=value;}},sessionStorage:{getItem(){return '';},setItem(){},removeItem(){}},setInterval(){},setTimeout(){return 1;},clearTimeout(){},fetch:async()=>({ok:true,status:200,json:async()=>structuredClone(settings)})};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
 assert(elements.start.disabled&&elements.stop.disabled,'unknown status must disable controls');
 context.data=stopped;run('applyStatus(data)');await run('loadSettings()');
 assert(!elements.start.disabled&&elements.stop.disabled,'stopped: start only');
 assert(elements.versions.textContent.includes('0.4.0'));
 assert(elements['server-list'].children.length===2,'roles must be rendered');
 assert(elements['server-list'].children[0].children[1].textContent==='☆');
 const running=structuredClone(stopped);running.service={state:'running',message:'Подключён',selectedServer:'CZ',connected:true,supervisorAlive:true,canStart:false,canStop:true,canRestart:true};
 context.data=running;run('applyStatus(data)');
 assert(elements.start.disabled&&!elements.stop.disabled,'running: stop only');
 assert(elements['active-server'].textContent==='CZ');
 elements.server.value='DE';elements.server.dispatch('change');
 assert(!elements.unsaved.hidden&&!elements.save.disabled,'draft must be visible and applicable');
 assert(elements.save.textContent==='Применить и переподключить');
 assert(elements['active-server'].textContent==='CZ','selection must not pretend to switch the active connection');
 assert(elements['check-servers'].disabled,'check must not use unsaved connection parameters');
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
 console.log('UI states, visible roles, drafts, health and version mismatch: PASS');
})().catch(error=>{console.error(error);process.exit(1);});
'''


class FrontendTest(unittest.TestCase):
    def test_asset_urls_and_payload_checksums_match_actual_files(self):
        import sys
        subprocess.run([sys.executable,str(ROOT/'scripts/package-web.py'),'--check'],check=True,capture_output=True)

    def test_real_ui_status_buttons_roles_drafts_and_versions(self):
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'test.js';script.write_text(NODE)
            result=subprocess.run([node,str(script),str(ROOT/'web/index.html'),str(ROOT/'web/app.js')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
