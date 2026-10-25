'use strict';
if (window.top !== window.self) { document.documentElement.replaceChildren(); throw new Error('Panel cannot be framed'); }
const $ = id => document.getElementById(id);
const profiles = {yandex:['26-4','26-3','25','24'],chrome:['149','148','147','146','145'],firefox:['151','150','149'],safari:['26-5','26-4']};
let key = sessionStorage.getItem('fptn-ui-key') || '';
let pending = false, lastBusy = false, loaded = false, polling = false, timer;
let previewToken = '';
function notice(message, error=false) {
  $('notice').textContent = message; $('notice').classList.toggle('error', error); $('notice').hidden = false;
  clearTimeout(timer); timer=setTimeout(()=>{$('notice').hidden=true;},error?14000:6500);
}
function loggedOut() {key='';previewToken='';sessionStorage.removeItem('fptn-ui-key');$('token').value='';$('logs').textContent='';$('dashboard').hidden=true;$('logout').hidden=true;$('login').hidden=false;loaded=false;}
async function api(query, body) {
  const response = await fetch('api.cgi'+(query?'?'+query:''), {
    method:body?'POST':'GET', cache:'no-store', credentials:'omit',
    headers:{Authorization:'Bearer '+key,...(body?{'Content-Type':'application/json'}:{})},
    ...(body?{body:JSON.stringify(body)}:{})
  });
  let data;
  try { data=await response.json(); } catch { throw new Error('Сервер вернул некорректный ответ. Проверьте настройку CGI.'); }
  if(response.status===401){loggedOut();throw new Error(data.error||'Неверный код доступа.');}
  if(!response.ok) throw new Error(data.error||'Ошибка запроса');
  return data;
}
function fillServers(servers, selected) {
  $('server').replaceChildren(...servers.map(name=>new Option(name,name)));
  if(servers.includes(selected)) $('server').value=selected;
}
function fillProfiles(selected) {
  const browser=$('browser').value;
  $('profile').replaceChildren(...profiles[browser].map(version=>new Option(version.replaceAll('-','.'),'sni-spoofing-'+browser+'-'+version)));
  if(selected && [...$('profile').options].some(o=>o.value===selected)) $('profile').value=selected;
}
function showSni(){ $('sni-fields').hidden=$('mode').value!=='sni'; }
async function loadSettings() {
  const data=await api('settings');
  fillServers(data.servers,data.server);
  $('strategy').value=data.connection.strategy;
  const bypass=data.connection.bypass;
  $('mode').value=bypass==='obfuscation'?'obfuscation':'sni';
  if(bypass!=='obfuscation') $('browser').value=bypass.split('-')[2];
  fillProfiles(bypass); $('sni').value=data.connection.sni; showSni();
  previewToken='';$('token').value='';loaded=true;
}
function setBusy(busy) {
  document.querySelectorAll('[data-command],#preview').forEach(el=>el.disabled=busy||pending);
  $('save').disabled=busy||pending||!loaded;
}
async function poll() {
  if(!key||polling) return;
  polling=true;
  try {
    const data=await api('status');
    $('status-text').textContent=data.status;
    const ready=data.status.includes('READY получен');
    $('indicator').className='dot'+(data.busy?' busy':ready?' active':'');
    $('status-title').textContent=data.busy?'Выполняем операцию…':ready?'Клиент работает':data.status.includes('остановлен')?'Клиент остановлен':'Состояние службы';
    $('memory').textContent=data.rssKiB?'ОЗУ '+(data.rssKiB/1024).toFixed(1)+' МиБ':'';
    const finished=(lastBusy||pending)&&!data.busy&&data.result;
    lastBusy=data.busy;
    if(finished) {
      pending=false;notice(data.result.message,!data.result.ok);
      if(data.result.ok) await loadSettings();
    }
    setBusy(data.busy);
  } catch(error) {if(key) notice(error.message,true);}
  finally {polling=false;}
}
async function openPanel() {
  await api('status');
  sessionStorage.setItem('fptn-ui-key',key);$('key').value='';
  $('login').hidden=true;$('dashboard').hidden=false;$('logout').hidden=false;
  try {await loadSettings();} catch(error) {notice(error.message,true);}
  await poll();
}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();key=$('key').value.trim();try{await openPanel();}catch(error){notice(error.message,true);}});
$('logout').addEventListener('click',()=>{loggedOut();$('token').value='';$('logs').textContent='';});
$('browser').addEventListener('change',()=>fillProfiles());$('mode').addEventListener('change',showSni);
$('preview').addEventListener('click',async()=>{
  const token=$('token').value.trim();if(!token){notice('Вставьте новый токен.',true);return;}
  $('preview').disabled=true;
  try{const data=await api('',{action:'preview',token});fillServers(data.servers,$('server').value);previewToken=token;loaded=true;notice('Серверы прочитаны. Теперь можно сохранить настройки.');}
  catch(error){notice(error.message,true);}finally{setBusy(lastBusy);}
});
async function command(body) {
  if(pending||lastBusy) return;
  pending=true;setBusy(true);
  try{await api('',body);notice('Операция запущена. Ожидаем результат…');await poll();}
  catch(error){pending=false;setBusy(lastBusy);notice(error.message,true);}
}
document.querySelectorAll('[data-command]').forEach(button=>button.addEventListener('click',()=>command({action:button.dataset.command})));
$('settings-form').addEventListener('submit',event=>{
  event.preventDefault();const token=$('token').value.trim();
  if(token&&token!==previewToken){notice('Сначала нажмите «Прочитать серверы» для нового токена.',true);return;}
  const connection={strategy:$('strategy').value,bypass:$('mode').value==='obfuscation'?'obfuscation':$('profile').value,sni:$('sni').value.trim()||'google.com'};
  command({action:'save',server:$('server').value,token,connection});
});
$('refresh-logs').addEventListener('click',async()=>{try{$('logs').textContent=(await api('logs')).log||'Журнал пока пуст.';}catch(error){notice(error.message,true);}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
setInterval(()=>{if(!document.hidden)poll();},5000);
fillProfiles();if(key)openPanel().catch(error=>notice(error.message,true));
