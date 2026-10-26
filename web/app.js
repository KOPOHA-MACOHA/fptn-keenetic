'use strict';
const UI_VERSION = '0.5.0';
const $ = id => document.getElementById(id);
if (window.top !== window.self) { document.documentElement.replaceChildren(); throw new Error('Panel cannot be framed'); }
const profiles = {yandex:['26-4','26-3','25','24'],chrome:['149','148','147','146','145'],firefox:['151','150','149'],safari:['26-5','26-4']};
let key=sessionStorage.getItem('fptn-ui-session')||'';
let pending=false,lastBusy=false,loaded=false,polling=false,timer,dirty=false,savedSnapshot='',versionMismatch=false;
let previewToken='',serverNames=[],favorites=new Set(),excluded=new Set(),lastData=null;
const healthNodes=new Map();
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(timer);timer=setTimeout(()=>{$('notice').hidden=true;},error?14000:6500);}
function loggedOut(){key='';previewToken='';sessionStorage.removeItem('fptn-ui-session');$('token').value='';$('logs').textContent='';$('dashboard').hidden=true;$('logout').hidden=true;$('login').hidden=false;loaded=false;lastData=null;}
async function api(query,body){
  const response=await fetch('api.cgi'+(query?'?'+query:''),{method:body?'POST':'GET',cache:'no-store',credentials:'omit',headers:{Authorization:'Bearer '+key,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  let data;try{data=await response.json();}catch{throw new Error('Не удалось прочитать ответ панели. Обновите страницу.');}
  if(response.status===401){loggedOut();throw new Error(data.error||'Войдите по логину и паролю.');}
  if(!response.ok)throw new Error(data.error||'Ошибка запроса');return data;
}
function collectSettings(){return {server:$('server').value,token:$('token').value.trim(),connection:{strategy:$('strategy').value,bypass:$('mode').value==='obfuscation'?'obfuscation':$('profile').value,sni:$('sni').value.trim()||'google.com'},policy:{mode:$('selection-mode').value,favorites:[...favorites].sort(),excluded:[...excluded].sort()}};}
function updateDirty(){
  dirty=loaded&&JSON.stringify(collectSettings())!==savedSnapshot;
  $('unsaved').hidden=!dirty;
  const running=Boolean(lastData?.service?.supervisorAlive);
  $('unsaved-text').textContent=running?'Изменения не применены. Нажмите «Применить и переподключить» — клиент переключится на выбранные параметры.':'Изменения не сохранены. Сохраните настройки, затем нажмите «Запустить».';
  $('save').textContent=running?'Применить и переподключить':'Сохранить настройки';
  $('save-help').textContent=running?'После применения работающий клиент автоматически переподключится. Повторно нажимать «Запустить» не нужно.':'Сохранение не запускает остановленную службу. Для подключения затем нажмите «Запустить».';
  setControls();
}
function setControls(){
  const busy=pending||lastBusy;const service=lastData?.service;const known=Boolean(service)&&!versionMismatch;
  $('autostart-toggle').disabled=busy||!known;
  $('start').disabled=busy||!known||!service.canStart;
  $('stop').disabled=busy||!known||!service.canStop;
  $('restart').disabled=busy||!known||!service.canRestart;
  $('save').disabled=busy||!known||!loaded||!dirty;
  $('preview').disabled=busy||versionMismatch;
  $('check-servers').disabled=busy||!known||!loaded||dirty;
  document.querySelectorAll('#dashboard input,#dashboard select,#dashboard textarea,.server-role').forEach(el=>{el.disabled=busy||versionMismatch;});
  if(!busy&&!versionMismatch)showSelection();
}
function fillServers(servers,selected){serverNames=servers;favorites=new Set([...favorites].filter(name=>servers.includes(name)));excluded=new Set([...excluded].filter(name=>servers.includes(name)));renderServers(selected);}
function renderServers(selected=$('server').value){
  const allowed=serverNames.filter(name=>!excluded.has(name));
  $('server').replaceChildren(...allowed.map(name=>new Option((favorites.has(name)?'★ ':'')+name,name)));
  if(allowed.includes(selected))$('server').value=selected;
  healthNodes.clear();
  $('server-list').replaceChildren(...serverNames.map(name=>{
    const row=document.createElement('div');row.className='server-row'+(excluded.has(name)?' excluded':'');
    const description=document.createElement('div');description.className='server-description';
    const title=document.createElement('span');title.textContent=name;title.className='server-name';
    const health=document.createElement('small');health.className='server-health';description.append(title,health);healthNodes.set(name,health);
    const star=document.createElement('button');star.type='button';star.textContent=favorites.has(name)?'★':'☆';star.className='server-role'+(favorites.has(name)?' favorite':'');star.title=favorites.has(name)?'Убрать из избранных':'Добавить в избранное';star.setAttribute('aria-label',star.title+': '+name);star.setAttribute('aria-pressed',String(favorites.has(name)));
    star.addEventListener('click',()=>{if(favorites.has(name))favorites.delete(name);else{favorites.add(name);excluded.delete(name);}renderServers();updateDirty();});
    const deny=document.createElement('button');deny.type='button';deny.textContent='×';deny.className='server-role'+(excluded.has(name)?' denied':'');deny.title=excluded.has(name)?'Разрешить сервер':'Исключить из любых проверок';deny.setAttribute('aria-label',deny.title+': '+name);deny.setAttribute('aria-pressed',String(excluded.has(name)));
    deny.addEventListener('click',()=>{if(excluded.has(name))excluded.delete(name);else{excluded.add(name);favorites.delete(name);}renderServers();updateDirty();});
    row.append(description,star,deny);return row;
  }));
  $('server-count').textContent=allowed.length+' разрешено · '+favorites.size+' избранных';updateHealth();showSelection();
}
function updateHealth(){
  const report=lastData?.report;const results=new Map((report?.results||[]).map(item=>[item.name,item]));
  for(const [name,node] of healthNodes){
    const result=results.get(name);node.className='server-health';
    if(excluded.has(name)){node.textContent='Исключён · не проверяется';continue;}
    if(!result){node.textContent='Не проверялся';continue;}
    if(result.ok){const ms=Number(result.latency_ms);node.textContent='Вход доступен'+(Number.isFinite(ms)?' · '+Math.round(ms)+' мс':'');node.classList.add('available');}
    else{node.textContent=[401,403].includes(result.code)?'Ошибка авторизации':'Вход не удался';node.classList.add('unavailable');}
  }
  if(report){const time=new Date(report.checked_at*1000).toLocaleTimeString('ru-RU');$('check-summary').textContent=(report.complete?'Последняя проверка: ':'Проверяем серверы… ')+time+'. Это время входа FPTN, не ICMP ping и не тест передачи через туннель.';}
  else $('check-summary').textContent='Серверы ещё не проверены. Проверка использует сохранённый токен и метод обхода; действующий туннель остаётся запущенным.';
}
function showSelection(){const auto=$('selection-mode').value==='auto';$('manual-server').hidden=auto;$('server').disabled=auto;}
function fillProfiles(selected){const browser=$('browser').value;$('profile').replaceChildren(...profiles[browser].map(version=>new Option(version.replaceAll('-','.'),'sni-spoofing-'+browser+'-'+version)));if(selected&&[...$('profile').options].some(o=>o.value===selected))$('profile').value=selected;}
function showSni(){$('sni-fields').hidden=$('mode').value!=='sni';}
async function loadSettings(){
  const data=await api('settings');const policy=data.policy||{mode:'manual',favorites:[],excluded:[]};favorites=new Set(policy.favorites);excluded=new Set(policy.excluded);$('selection-mode').value=policy.mode;fillServers(data.servers,data.server);
  $('strategy').value=data.connection.strategy;const bypass=data.connection.bypass;$('mode').value=bypass==='obfuscation'?'obfuscation':'sni';if(bypass!=='obfuscation')$('browser').value=bypass.split('-')[2];fillProfiles(bypass);$('sni').value=data.connection.sni;showSni();previewToken='';$('token').value='';loaded=true;savedSnapshot=JSON.stringify(collectSettings());updateDirty();
}
function applyStatus(data){
  if(!data.service||typeof data.service.state!=='string')throw new Error('Старая версия API. Обновите панель и управляющий скрипт.');
  lastData=data;versionMismatch=data.uiVersion!==UI_VERSION;
  $('versions').textContent='Панель '+UI_VERSION+' · клиент '+data.clientVersion+(data.revision?' · '+data.revision.slice(0,7):'');
  $('version-mismatch').hidden=!versionMismatch;
  if(versionMismatch)$('version-mismatch-text').textContent='Версия страницы '+UI_VERSION+' отличается от API '+data.uiVersion+'. Обновите страницу.';
  $('autostart-state').textContent=data.autostart?'Автозапуск включён':'Автозапуск выключен';
  $('autostart-toggle').textContent=data.autostart?'Выключить автозапуск':'Включить автозапуск';
  const service=data.service;
  const titles={stopped:'Служба остановлена',starting:'Запускаем службу',selecting:'Выбираем сервер',retrying:'Ожидаем повторного подключения',reconnecting:'Восстанавливаем соединение',stale:'Служба требует восстановления'};
  $('status-title').textContent=service.state==='running'?(service.connected?'Туннель подключён':'Клиент запущен'):titles[service.state]||'Состояние неизвестно';
  $('indicator').className='dot'+(service.connected?' active':['starting','selecting','retrying','reconnecting'].includes(service.state)?' busy':'');
  $('status-message').textContent=service.message;$('status-text').textContent=data.status;
  $('active-server').textContent=service.selectedServer||'Нет активного сервера';
  $('memory').textContent=data.rssKiB?'ОЗУ '+(data.rssKiB/1024).toFixed(1)+' МиБ':'';
  const operations={start:'Запуск клиента',stop:'Остановка клиента',restart:'Переподключение',save:'Применение настроек',check:'Проверка входа на серверы'};
  $('operation').hidden=!data.busy;$('operation').textContent=(operations[data.operation]||'Выполнение операции')+'…';
  updateHealth();updateDirty();
}
function showOffline(error){lastData=null;$('status-title').textContent='Нет связи с панелью';$('status-message').textContent=error.message;$('indicator').className='dot';setControls();}
async function poll(){
  if(!key||polling)return;polling=true;
  try{const data=await api('status');const finished=(lastBusy||pending)&&!data.busy&&data.result;lastBusy=data.busy;applyStatus(data);
    if(finished){pending=false;notice(data.result.message,!data.result.ok);if(data.result.ok&&data.result.action==='save')await loadSettings();}
    setControls();
  }catch(error){if(key)showOffline(error);}finally{polling=false;}
}
async function openPanel(){
  const data=await api('status');sessionStorage.setItem('fptn-ui-session',key);$('password').value='';$('login').hidden=true;$('dashboard').hidden=false;$('logout').hidden=false;lastBusy=data.busy;applyStatus(data);
  try{await loadSettings();}catch(error){notice(error.message,true);$('settings-error').hidden=false;$('settings-error').textContent='Список серверов не загружен: '+error.message;}
  setControls();
}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();
  const button=$('login-submit');button.disabled=true;
  try{const response=await fetch('api.cgi?login',{method:'POST',cache:'no-store',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('username').value,password:$('password').value})});
    $('password').value='';const data=await response.json();if(!response.ok)throw new Error(data.error||'Вход не выполнен.');key=data.key;await openPanel();
  }catch(error){notice(error.message,true);}finally{button.disabled=false;}
});
$('logout').addEventListener('click',async()=>{try{await api('logout',{});}catch(error){notice(error.message,true);}finally{loggedOut();}});
$('reload-ui').addEventListener('click',()=>{window.location.href='/?v='+Date.now();});
$('browser').addEventListener('change',()=>{fillProfiles();updateDirty();});$('mode').addEventListener('change',()=>{showSni();updateDirty();});$('selection-mode').addEventListener('change',()=>{showSelection();updateDirty();});
for(const id of ['server','strategy','profile','sni','token'])$(id).addEventListener('input',updateDirty);
for(const id of ['server','strategy','profile','sni'])$(id).addEventListener('change',updateDirty);
$('preview').addEventListener('click',async()=>{const token=$('token').value.trim();if(!token){notice('Вставьте новый токен.',true);return;}$('preview').disabled=true;try{const data=await api('',{action:'preview',token});fillServers(data.servers,$('server').value);previewToken=token;loaded=true;updateDirty();$('settings-error').hidden=true;notice('Серверы прочитаны. Примените настройки.');}catch(error){notice(error.message,true);}finally{setControls();}});
async function command(body){if(pending||lastBusy||versionMismatch)return;pending=true;setControls();try{await api('',body);notice('Операция запущена. Состояние обновляется автоматически.');await poll();}catch(error){pending=false;setControls();notice(error.message,true);}}
document.querySelectorAll('[data-command]').forEach(button=>button.addEventListener('click',()=>command({action:button.dataset.command})));
$('autostart-toggle').addEventListener('click',()=>command({action:lastData?.autostart?'disable':'enable'}));
$('check-servers').addEventListener('click',()=>{if(dirty){notice('Сначала примените изменения настроек.',true);return;}command({action:'check'});});
$('settings-form').addEventListener('submit',event=>{event.preventDefault();const settings=collectSettings();if(settings.token&&settings.token!==previewToken){notice('Сначала нажмите «Прочитать серверы» для нового токена.',true);return;}const allowed=serverNames.filter(name=>!excluded.has(name));if(!allowed.length||allowed.length>64){notice('Оставьте от 1 до 64 разрешённых серверов.',true);return;}command({action:'save',...settings});});
$('refresh-logs').addEventListener('click',async()=>{try{$('logs').textContent=(await api('logs')).log||'Журнал пока пуст.';}catch(error){notice(error.message,true);}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});setInterval(()=>{if(!document.hidden)poll();},5000);
fillProfiles();setControls();if(key)openPanel().catch(error=>notice(error.message,true));
