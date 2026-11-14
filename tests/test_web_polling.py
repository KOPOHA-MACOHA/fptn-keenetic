"""Exercise the actual panel scheduler with a deterministic clock and fetch gates."""
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from test_web_frontend import NODE, ROOT

HARNESS = NODE.split('(async()=>{', 1)[0].replace(
    'addEventListener(){},querySelectorAll',
    'listeners:{},addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);},querySelectorAll')
CLOCK = r'''
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));};
let now=0,nextTimer=1000;const timers=new Map();
context.setTimeout=(fn,ms)=>{const id=nextTimer++;timers.set(id,{fn,at:now+ms,ms});return id;};
context.clearTimeout=id=>timers.delete(id);
async function advance(ms){
 const end=now+ms;
 for(let guard=0;guard<100;guard++){
  const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
  if(!next){now=end;await flush();return;}
  now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();
 }
 throw Error('Timer loop did not settle');
}
function visibility(hidden){document.hidden=hidden;for(const fn of document.listeners.visibilitychange||[])fn();}
let current=structuredClone(stopped),statusCalls=0,posts=0,offline=false;
const originalFetch=context.fetch;
context.fetch=async(url,options={})=>{
 if(url.includes('?status')){statusCalls++;if(offline)throw Error('offline');return {ok:true,status:200,json:async()=>structuredClone(current)};}
 if(options.body&&!url.includes('?')){posts++;return {ok:true,status:200,json:async()=>({ok:true})};}
 return originalFetch(url,options);
};
'''


class PollingTest(unittest.TestCase):
    def scenario(self, code):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        script = HARNESS + '\n(async()=>{\nawait new Promise(r=>setImmediate(r));\n' + CLOCK + code + r'''
})().catch(error=>{console.error(error);process.exit(1);});
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'polling.js'
            path.write_text(script)
            result = subprocess.run([node, str(path), str(ROOT/'web/index.html'), str(ROOT/'web/app.js')],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_adaptive_intervals_hidden_tab_and_session_expiry(self):
        self.scenario(r'''
run("key='a'.repeat(64)");await run('openPanel()');statusCalls=0;
assert([...timers.values()].some(t=>t.ms===20000));
await advance(60000);assert(statusCalls===3,'stable panel: three GETs/minute instead of twelve');
visibility(true);assert(timers.size===0,'hidden panel must have no polling timer');
const before=statusCalls;await advance(300000);assert(statusCalls===before);
visibility(false);await flush();assert(statusCalls===before+1,'return must refresh immediately');
current.service.state='reconnecting';current.service.connected=false;await run('poll()');
statusCalls=0;await advance(6000);assert(statusCalls===3,'connection recovery must poll every two seconds');
current.service.state='running';current.service.connected=true;await advance(2000);
statusCalls=0;await advance(20000);assert(statusCalls===1,'completed recovery must return to idle interval');
current.service.state='waiting-network';await run('poll()');assert([...timers.values()].some(t=>t.ms===10000));
current.service.state='stale';await run('poll()');assert([...timers.values()].some(t=>t.ms===20000));
current.service.state='retrying';current.service.retryAt=Math.floor(Date.now()/1000)+60;await run('poll()');assert([...timers.values()].some(t=>t.ms===20000),'long reconnect backoff must not busy-poll');
current.service.retryAt=Math.floor(Date.now()/1000)+1;await run('poll()');assert([...timers.values()].some(t=>t.ms===2000));delete current.service.retryAt;
current.busy=true;current.operationState={stale:true};await run('poll()');assert([...timers.values()].some(t=>t.ms===20000),'dead operations must not busy-poll');
current.busy=false;delete current.operationState;await run('poll()');
offline=true;await run('poll()');statusCalls=0;await advance(30000);assert(statusCalls===3,'API failure must use the bounded retry interval');
offline=false;current.service.state='stopped';await advance(10000);assert([...timers.values()].some(t=>t.ms===20000));
run("beginUpdate({revision:'d'.repeat(40),version:'0.18.12'})");
current.busy=true;current.operation='update';await run('poll()');
visibility(true);const duringUpdate=statusCalls;await advance(60000);assert(statusCalls===duringUpdate&&timers.size===0);
current.busy=false;current.revision='d'.repeat(40);current.result={ok:true,action:'update',message:'installed'};
visibility(false);await flush();assert(run('updateWatch.finished'),'resuming must observe an update completed while hidden');
let expiredCalls=0;context.fetch=async(url,options)=>{if(url.includes('?status')){expiredCalls++;return {ok:false,status:401,json:async()=>({error:'expired'})};}return originalFetch(url,options);};
await run('poll()');await flush();await advance(60000);assert(run('key')===''&&expiredCalls===1,'expired session must stop polling');
console.log('Adaptive intervals, hidden timer cancellation, immediate resume and auth expiry: PASS');
''')

    def test_single_flight_and_stale_get_cannot_finish_new_command(self):
        self.scenario(r'''
current.service={...current.service,state:'running',connected:true,supervisorAlive:true};
run("key='a'.repeat(64)");await run('openPanel()');
let resolveOld,resolvePost;let outstanding=0,maxOutstanding=0;
context.fetch=(url,options={})=>{
 if(url.includes('?status')){
  statusCalls++;outstanding++;maxOutstanding=Math.max(maxOutstanding,outstanding);
  return new Promise(resolve=>resolveOld=()=>{outstanding--;resolve({ok:true,status:200,json:async()=>structuredClone(stopped)});});
 }
 if(options.body){posts++;return new Promise(resolve=>resolvePost=()=>resolve({ok:true,status:200,json:async()=>({ok:true})}));}
 return originalFetch(url,options);
};
const old=run('poll()');await flush();const calls=statusCalls;
visibility(true);visibility(false);await run('poll()');assert(statusCalls===calls&&maxOutstanding===1);
const command=run("command({action:'restart'})");await flush();assert(posts===1&&run('commandSending'));
resolveOld();await old;await flush();
assert(run('pending')&&run('lastData.service.connected'),'old idle GET must not clear pending or replace active state');
assert(![...timers.values()].some(t=>![6500,15000].includes(t.ms)),'no automatic GET while POST is in flight');
context.fetch=async(url,options={})=>{
 if(url.includes('?status')){statusCalls++;return {ok:true,status:200,json:async()=>({...structuredClone(current),busy:true,operation:'restart'})};}
 return originalFetch(url,options);
};
resolvePost();await command;assert(!run('commandSending')&&run('pending'));
assert([...timers.values()].some(t=>t.ms===2000),'accepted operation must be watched promptly');
context.fetch=async(url,options={})=>url.includes('?status')?{ok:true,status:200,json:async()=>({...structuredClone(current),busy:false,result:{ok:true,action:'restart',message:'restarted'}})}:originalFetch(url,options);
await advance(2000);assert(!run('pending')&&[...timers.values()].some(t=>t.ms===20000));
assert(posts===1,'GET retries and visibility changes must never replay a command');
let expireOld;
context.fetch=()=>new Promise(resolve=>expireOld=()=>resolve({ok:false,status:401,json:async()=>({error:'old session expired'})}));
const oldSession=run('poll()');await flush();
run("statusGeneration++;key='b'.repeat(64);sessionReady=true");expireOld();await oldSession;
assert(run('key')==='b'.repeat(64)&&run('sessionReady'),'401 from an old request must not log out a newer session');
console.log('Single-flight polling and stale GET/POST operation boundary: PASS');
''')

    def test_unchanged_status_skips_dom_work_but_updates_memory_and_modal(self):
        self.scenario(r'''
run("key='a'.repeat(64)");await run('openPanel()');
let writes=0,title=elements['status-title'].textContent;
Object.defineProperty(elements['status-title'],'textContent',{get(){return title;},set(v){title=v;writes++;}});
current.rssKiB=10240;await run('poll()');assert(writes===0&&elements.memory.textContent==='ОЗУ 10.0 МиБ');
await run('poll()');assert(writes===0);
current.service.state='starting';await run('poll()');assert(writes===1,'real state change must render');
context.data={...structuredClone(current),busy:false,revision:'a'.repeat(40),result:{ok:true,action:'update',message:'installed'}};
run('applyStatus(data)');run("beginUpdate({revision:'a'.repeat(40)});updateWatch.accepted=true");
run('applyStatus(data)');assert(run('updateWatch.finished'),'identical API response must still complete an accepted update');
run('showOffline(new Error("offline"));applyStatus(data)');assert(writes>=3,'offline recovery must invalidate the render cache');
console.log('Unchanged status/RSS rendering and update completion observation: PASS');
''')
