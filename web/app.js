'use strict';
const UI_VERSION = '0.18.3';
const $ = id => document.getElementById(id);
if (window.top !== window.self) { document.documentElement.replaceChildren(); throw new Error('Panel cannot be framed'); }
let profiles={},clientCapabilities=null;
const browserLabels={chrome:'Chrome',firefox:'Firefox',yandex:'Yandex',safari:'Safari'};
const strategyLabels={'rolling-tunnel':'Скользящий туннель','dual-rolling-tunnel':'Двойной скользящий туннель','triple-rolling-tunnel':'Тройной скользящий туннель'};
let updateInfo=null,updateWatch=null;
let activeTab='status';
let authConfigured=true,authMode='panel',accountData=null,accountPending=false;
let key=sessionStorage.getItem('fptn-ui-session')||'';
let sessionReady=false;
let pending=false,lastBusy=false,loaded=false,polling=false,timer,dirty=false,savedSnapshot='',versionMismatch=false;
let savedFallback={enabled:false,profiles:[],domains:[],budget:60};
let previewToken='',serverNames=[],favorites=new Set(),excluded=new Set(),lastData=null;
const healthNodes=new Map();
const fallbackProfiles=['fallback-profile-1','fallback-profile-2'];
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(timer);timer=setTimeout(()=>{$('notice').hidden=true;},error?14000:6500);}
function showAuthLoading(){
  sessionReady=false;$('login').hidden=true;$('dashboard').hidden=true;$('logout').hidden=true;
  $('auth-loading').hidden=false;$('auth-loading-message').textContent='Проверяем сессию…';$('auth-retry').hidden=true;
}
function showAuthError(error){showAuthLoading();$('auth-loading-message').textContent=error.message;$('auth-retry').hidden=false;}
async function showLogin(){
  showAuthLoading();
  try{await loadAuthInfo();$('auth-loading').hidden=true;$('login').hidden=false;}
  catch(error){showAuthError(error);}
}
function loggedOut(){if(updateWatch&&!updateWatch.finished)finishUpdate(false,'Сессия завершена. Перезагрузите панель и войдите снова, чтобы проверить результат.');key='';previewToken='';sessionStorage.removeItem('fptn-ui-session');accountData=null;for(const id of ['account-current-password','account-password','account-confirm','account-root-password'])$(id).value='';$('token').value='';$('logs').textContent='';loaded=false;lastData=null;showLogin();}
async function initializePanel(){
  showAuthLoading();
  if(!key){await showLogin();return;}
  try{await openPanel();}
  catch(error){if(key)showAuthError(error);}
}

async function api(query,body){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch('api.cgi'+(query?'?'+query:''),{signal:controller.signal,method:body?'POST':'GET',cache:'no-store',credentials:'omit',headers:{Authorization:'Bearer '+key,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
    let data;try{data=await response.json();}catch{throw new Error('Не удалось прочитать ответ панели. Обновите страницу.');}
    if(response.status===401){loggedOut();throw new Error(data.error||'Войдите по логину и паролю.');}
    if(!response.ok){const error=new Error(data.error||'Ошибка запроса');error.status=response.status;throw error;}return data;
  }catch(error){
    if(controller.signal.aborted)throw new Error('Панель не ответила за 15 секунд. Обновите страницу, чтобы проверить состояние.');
    throw error;
  }finally{clearTimeout(timeout);}
}
function collectSettings(){return {server:$('server').value,token:$('token').value.trim(),connection:{health:{enabled:$('health-enabled').checked,interval:Number($('health-interval').value),timeout:Number($('health-timeout').value),failures:Number($('health-failures').value)},ipv6:$('ipv6').checked,mtu:$('mtu-mode').value==='custom'?Number($('mtu').value):0,outInterface:$('out-interface').value.trim(),strategy:$('strategy').value,bypass:$('mode').value==='obfuscation'?'obfuscation':$('profile').value,sni:$('sni').value.trim()||'google.com'},fallback:$('fallback-enabled').checked?{enabled:$('fallback-enabled').checked,profiles:[...new Set(fallbackProfiles.map(id=>$(id).value).filter(Boolean))],domains:$('fallback-domains').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean),budget:Number($('fallback-budget').value)}:{...savedFallback,enabled:false},policy:{mode:$('selection-mode').value,favorites:[...favorites].sort(),excluded:[...excluded].sort()}};}
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
  const busy=pending||lastBusy||accountPending||Boolean(updateWatch&&!updateWatch.finished);const service=lastData?.service;const known=Boolean(service)&&!versionMismatch;
  $('recover-operation').disabled=pending||versionMismatch||!lastData?.operationState?.canRecover;
  $('check-update').disabled=busy||!known;
  $('install-update').disabled=busy||!known||dirty||!updateInfo?.available;
  $('account-save').disabled=busy||!known||!accountData;
  $('autostart-toggle').disabled=busy||!known||lastData?.configured===false||lastData?.interfaceReady===false||(!lastData?.autostart&&lastData?.interfaceNeedsSave===true);
  $('setup-interface').disabled=busy||!known||(Boolean(service?.supervisorAlive)&&!lastData?.interfaceNeedsSave);
  $('start').disabled=busy||!known||!service.canStart||lastData?.configured===false||lastData?.interfaceReady===false;
  $('stop').disabled=busy||!known||!service.canStop;
  $('restart').disabled=busy||!known||!service.canRestart;
  $('save').disabled=busy||!known||!loaded||!dirty;
  $('preview').disabled=busy||versionMismatch;
  $('check-servers').disabled=busy||!known||!loaded||dirty||lastData?.configured===false;
  document.querySelectorAll('#dashboard input,#dashboard select,#dashboard textarea,.server-role').forEach(el=>{el.disabled=busy||versionMismatch;});
  if(!busy&&!versionMismatch){showSelection();showMtu();}
  $('health-enabled').disabled=busy||versionMismatch||!clientCapabilities?.dataPlaneHealth;
  for(const id of ['health-interval','health-timeout','health-failures'])$(id).disabled=busy||versionMismatch||!clientCapabilities?.dataPlaneHealth||!$('health-enabled').checked;
  $('port-save').disabled=busy||!known||dirty;
  $('download-diagnostics').disabled=!known;
  const moving=lastData?.portChange;
  const here=window.location.origin;
  $('port-confirm').hidden=!moving||moving.newOrigin.replace(/:80$/,'')!==here;
  $('port-confirm').disabled=pending||versionMismatch;
  $('port-rollback').hidden=!moving||busy;
  $('port-rollback').disabled=busy||!known;

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
    else{node.textContent=[401,403].includes(result.code)?'Доступ отклонён: проверьте токен или подписку':'Вход не удался';node.classList.add('unavailable');}
  }
  if(report){const time=new Date(report.checked_at*1000).toLocaleTimeString('ru-RU');$('check-summary').textContent=(report.complete?'Последняя проверка: ':'Проверяем серверы… ')+time+'. Это время входа FPTN, не ICMP ping и не тест передачи через туннель.';}
  else $('check-summary').textContent='Серверы ещё не проверены. Проверка использует сохранённый токен и метод обхода; действующий туннель остаётся запущенным.';
}
function showSelection(){const auto=$('selection-mode').value==='auto';$('manual-server').hidden=auto;$('server').disabled=auto;}
function profileLabel(method){const match=method.match(/^sni-spoofing-([a-z][a-z0-9]*)-([0-9]+(?:-[0-9]+)*)$/);return match?(browserLabels[match[1]]||match[1][0].toUpperCase()+match[1].slice(1))+' '+match[2].replaceAll('-','.'):method;}
function compareProfileVersions(a,b){const left=a.version.split('-').map(Number),right=b.version.split('-').map(Number);for(let i=0;i<Math.max(left.length,right.length);i++){const difference=(right[i]||0)-(left[i]||0);if(difference)return difference;}return 0;}
function renderCapabilities(caps){
  if(caps?.schema!==1||!Array.isArray(caps.methods)||!Array.isArray(caps.strategies))throw new Error('Не удалось прочитать возможности установленного клиента.');
  clientCapabilities=caps;profiles={};
  for(const method of caps.methods){const match=method.match(/^sni-spoofing-([a-z][a-z0-9]*)-([0-9]+(?:-[0-9]+)*)$/);if(match)(profiles[match[1]]||=[]).push({id:method,version:match[2]});}
  for(const values of Object.values(profiles))values.sort(compareProfileVersions);
  $('browser').replaceChildren(...Object.keys(profiles).map(name=>new Option(browserLabels[name]||name[0].toUpperCase()+name.slice(1),name)));
  $('strategy').replaceChildren(...caps.strategies.map(name=>new Option(strategyLabels[name]||name,name)));
  const sniMode=[...$('mode').options].find(option=>option.value==='sni');if(sniMode)sniMode.disabled=!Object.keys(profiles).length;
  for(const id of fallbackProfiles)$(id).replaceChildren(new Option('Не использовать',''),...Object.values(profiles).flat().map(item=>new Option(profileLabel(item.id),item.id)));
}
function fillProfiles(selected){
  const values=profiles[$('browser').value]||[];$('profile').replaceChildren(...values.map(item=>new Option(item.version.replaceAll('-','.'),item.id)));
  if(selected&&values.some(item=>item.id===selected))$('profile').value=selected;
}
function setFallbackProfiles(selected){
  fallbackProfiles.forEach((id,index)=>{const value=selected[index]||'';if(value&&![...$(id).options].some(option=>option.value===value)){const unavailable=new Option('Недоступен в этой версии: '+profileLabel(value),value);unavailable.disabled=true;$(id).append(unavailable);}$(id).value=value;});
}
function showMtu(){const custom=$('mtu-mode').value==='custom';$('mtu-custom').hidden=!custom;$('mtu').disabled=!custom;}
function showSni(){$('sni-fields').hidden=$('mode').value!=='sni';}
async function loadSettings(){
  const data=await api('settings');$('token-summary').textContent=data.configured===false?'Добавить токен FPTN':'Заменить токен';$('token-help').textContent=data.configured===false?'Вставьте токен из приложения FPTN. Он останется только на роутере.':'Текущий токен сохранён на роутере и не возвращается в браузер.';if(data.configured===false)selectTab('settings');renderCapabilities(data.capabilities);const policy=data.policy||{mode:'manual',favorites:[],excluded:[]};favorites=new Set(policy.favorites);excluded=new Set(policy.excluded);$('selection-mode').value=policy.mode;fillServers(data.servers,data.server);
  $('mtu-mode').value=data.connection.mtu>0?'custom':'auto';$('mtu').value=String(data.connection.mtu||data.interfaceMtu||1420);$('mtu-auto-option').textContent='Как в Keenetic'+(data.interfaceMtu?' ('+data.interfaceMtu+')':'');showMtu();$('out-interface').value=data.connection.outInterface||'';
  const health=data.connection.health||{enabled:false,interval:60,timeout:5,failures:3};
  $('health-enabled').checked=health.enabled;$('health-interval').value=String(health.interval);$('health-timeout').value=String(health.timeout);$('health-failures').value=String(health.failures);
  $('health-unsupported').hidden=Boolean(data.capabilities?.dataPlaneHealth);
  $('ipv6').checked=data.connection.ipv6===true;$('strategy').value=data.connection.strategy;const bypass=data.connection.bypass;$('mode').value=bypass==='obfuscation'?'obfuscation':'sni';if(bypass!=='obfuscation')$('browser').value=bypass.split('-')[2];fillProfiles(bypass);$('sni').value=data.connection.sni;showSni();const fallback=data.fallback||{enabled:false,profiles:[],domains:[],budget:60};savedFallback=JSON.parse(JSON.stringify(fallback));$('fallback-enabled').checked=fallback.enabled;setFallbackProfiles(fallback.profiles);$('fallback-domains').value=(fallback.domains.length?fallback.domains:[data.connection.sni]).join('\n');$('fallback-budget').value=String(fallback.budget);$('fallback-fields').hidden=!fallback.enabled;previewToken='';$('token').value='';loaded=true;savedSnapshot=JSON.stringify(collectSettings());updateDirty();
}
function applyStatus(data){
  if(!data.service||typeof data.service.state!=='string')throw new Error('Старая версия API. Обновите панель и управляющий скрипт.');
  lastData=data;versionMismatch=data.uiVersion!==UI_VERSION;
  $('versions').textContent='Панель '+UI_VERSION+' · клиент Keenetic '+data.clientVersion+' · исходный FPTN '+(data.upstreamVersion||'не определён')+(data.clientBuildRevision?' · сборка '+data.clientBuildRevision.slice(0,7):'');
  $('version-mismatch').hidden=!versionMismatch;
  if(versionMismatch)$('version-mismatch-text').textContent='Версия страницы '+UI_VERSION+' отличается от API '+data.uiVersion+'. Обновите страницу.';
  $('autostart-state').textContent=data.autostart?'Автозапуск включён':'Автозапуск выключен';
  $('autostart-toggle').textContent=data.autostart?'Выключить автозапуск':'Включить автозапуск';
  const service=data.service;
  $('native-interface-name').textContent=data.service?.nativeInterface||'OpkgTun (ещё не создан)';
  const needToken=data.configured===false,needInterface=data.interfaceReady===false,needSave=data.interfaceNeedsSave===true;
  $('onboarding').hidden=!needToken&&!needInterface&&!needSave;
  $('onboarding-message').textContent=needToken?'Добавьте токен в разделе «Сервер и токен», прочитайте серверы и сохраните настройки. Для подключения нужен штатный интерфейс FPTN.':needInterface?'Создайте интерфейс FPTN, затем нажмите «Запустить». После подключения назначьте нужные домены и CIDR этому интерфейсу в Keenetic.':needSave?'Интерфейс создан. Сохраните конфигурацию Keenetic, чтобы он восстановился после перезагрузки.':'';
  $('setup-interface').hidden=!needInterface&&!needSave;$('setup-interface-help').hidden=!needInterface&&!needSave;
  $('setup-interface').textContent=needSave?'Сохранить конфигурацию Keenetic':'Создать интерфейс и сохранить';
  const titles={'waiting-network':'Ожидаем интернет',stopped:'Служба остановлена',starting:'Запускаем службу',selecting:'Выбираем сервер',retrying:'Ожидаем повторного подключения',reconnecting:'Восстанавливаем соединение',stale:'Служба требует восстановления'};
  $('status-title').textContent=service.state==='running'?(service.connected?'Туннель подключён':'Клиент запущен'):titles[service.state]||'Состояние неизвестно';
  $('indicator').className='dot'+(service.connected?' active':['starting','selecting','retrying','reconnecting','waiting-network'].includes(service.state)?' busy':'');
  const effective=service.effectiveConnection;$('effective-connection').textContent=effective?(service.state==='running'?'Используется: ':'Попытка: ')+(effective.bypass==='obfuscation'?'маскировка трафика':effective.bypass.replace('sni-spoofing-','')+' · SNI '+effective.sni)+(effective.mtu?' · MTU '+effective.mtu:'')+(effective.outInterface?' · '+effective.outInterface:''):'Параметры активного подключения появятся после переподключения.';
  $('status-message').textContent=service.message+(service.state==='retrying'&&service.retryAt?' Следующая попытка: '+new Date(service.retryAt*1000).toLocaleTimeString('ru-RU'):'');$('status-text').textContent=data.status;
  $('active-server-label').textContent=service.connected?'Подключено к':['starting','selecting','reconnecting'].includes(service.state)?'Текущая попытка':'Сервер';
  $('active-server').textContent=service.selectedServer||'Нет активного сервера';
  const preferred=service.preferredServer||'';
  $('selection-summary').textContent=service.selectionMode==='auto'?'Сохранён режим: автовыбор, избранные сначала.':preferred?'Предпочтительный сервер: '+preferred+(service.selectedServer&&service.selectedServer!==preferred?'. Сейчас используется другой сервер после автовыбора.':''):'';
  const change=service.lastSwitch;
  const causes={initial:'Первое подключение',reconnect:'Повторное подключение','connection-lost':'Соединение потеряно','health-failed':'Не прошла проверка передачи через VPN'};
  $('switch-summary').textContent=change?'Последнее подключение: '+new Date(change.at*1000).toLocaleTimeString('ru-RU')+' · '+(causes[change.reason]||'Переподключение')+(change.from&&change.from!==change.to?' · '+change.from+' → '+change.to:''):'';
  const health=service.health;
  const healthTitles={scheduled:'Проверка запланирована',checking:'Проверяем передачу через VPN',ok:'Передача через VPN подтверждена',missed:'Ответ не получен',failed:'Проверка VPN не прошла; выбираем другой сервер','checking-uplink':'Проверяем исходящее подключение',inconclusive:'Нет ответа и через исходящее подключение; сервер по этой проверке не меняем',disabled:'Автопроверка отключена'};
  $('health-status').textContent=health?(healthTitles[health.state]||'Состояние проверки неизвестно')+(health.failures?' · неудач: '+health.failures+'/'+health.threshold:'')+(health.nextCheckAt&&['ok','missed','scheduled','inconclusive'].includes(health.state)?' · следующая: '+new Date(health.nextCheckAt*1000).toLocaleTimeString('ru-RU'):''):'';
  if(data.panelOrigin){$('panel-address').textContent=data.panelOrigin;if(!$('panel-port').value)$('panel-port').value=data.panelOrigin.split(':').pop();}
  const moving=data.portChange;$('port-transition').hidden=!moving;
  if(moving){$('port-link').href=moving.newOrigin;$('port-transition-text').textContent='Новый адрес: '+moving.newOrigin+'. Подтвердите до '+new Date(moving.deadline*1000).toLocaleTimeString('ru-RU');}

  $('memory').textContent=data.rssKiB?'ОЗУ '+(data.rssKiB/1024).toFixed(1)+' МиБ':'';
  $('recover-operation').hidden=!data.operationState?.stale;
  const operations={'setup-interface':'Создание интерфейса FPTN',recover:'Восстановление управления',start:'Запуск клиента',stop:'Остановка клиента',restart:'Переподключение',save:'Применение настроек',check:'Проверка входа на серверы','change-port':'Смена порта панели','check-update':'Проверка обновлений',update:'Установка обновления'};
  if(data.busy&&data.operation==='check-update')$('update-status').textContent='Проверяем наличие обновлений…';
  $('operation').hidden=!data.busy||['update','check-update'].includes(data.operation);$('operation').textContent=(operations[data.operation]||'Выполнение операции')+'…';
  if(data.operationState?.stale)$('operation').textContent=data.operationState.message;
  observeUpdate(data);
  updateHealth();updateDirty();
}
function showOffline(error){if(updateWatch&&!updateWatch.finished)renderUpdate('Панель временно недоступна. Ожидаем восстановления связи…');lastData=null;$('status-title').textContent='Нет связи с панелью';$('status-message').textContent=error.message;$('indicator').className='dot';setControls();}
async function poll(){
  if(!key||!sessionReady||polling)return;polling=true;
  try{const data=await api('status');const finished=(lastBusy||pending)&&!data.busy&&data.result;lastBusy=data.busy;applyStatus(data);
    if(finished){pending=false;notice(data.result.message,!data.result.ok);if(data.result.ok&&data.result.action==='save')await loadSettings();if(data.result.action==='check-update'||(data.result.action==='update'&&!data.result.ok))await loadUpdates();}
    setControls();
  }catch(error){if(key)showOffline(error);}finally{polling=false;}
}
async function openPanel(){
  const data=await api('status');sessionStorage.setItem('fptn-ui-session',key);sessionReady=true;$('password').value='';$('auth-loading').hidden=true;$('login').hidden=true;$('dashboard').hidden=false;$('logout').hidden=false;lastBusy=data.busy;applyStatus(data);
  try{await loadSettings();}catch(error){notice(error.message,true);$('settings-error').hidden=false;$('settings-error').textContent='Список серверов не загружен: '+error.message;}
  if(!key)return;
  try{await loadUpdates();}catch(error){notice(error.message,true);}
  if(!key)return;
  try{await loadAccount();}catch(error){notice(error.message,true);}
  setControls();
}
function showAccountMode(){
  const panel=$('account-mode').value==='panel';$('account-panel-fields').hidden=!panel;
  const rootPassword=!panel&&accountData?.mode!=='entware';$('account-root-fields').hidden=!rootPassword;
  for(const id of ['account-username','account-password','account-confirm'])$(id).required=panel;
  $('account-root-password').required=rootPassword;
}
async function loadAccount(){
  accountData=await api('account');$('account-current').textContent=accountData.mode==='entware'?'Используется root Entware':'Учётная запись панели: '+accountData.username;
  $('account-mode').value=accountData.mode;$('account-username').value=accountData.mode==='panel'?accountData.username:'admin';
  $('account-root-option').disabled=!accountData.rootAvailable;showAccountMode();
}
$('account-mode').addEventListener('change',showAccountMode);
$('account-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending||accountPending||lastBusy||versionMismatch||!accountData)return;
  accountPending=true;setControls();
  const body={mode:$('account-mode').value,currentPassword:$('account-current-password').value};
  if(body.mode==='panel'){body.username=$('account-username').value;body.password=$('account-password').value;body.confirm=$('account-confirm').value;}
  else if(accountData.mode!=='entware')body.rootPassword=$('account-root-password').value;
  try{await api('account',body);if(body.mode==='panel')$('username').value=body.username;loggedOut();notice('Вход изменён. Войдите с новыми данными.');}
  catch(error){notice(error.message,true);}
  finally{for(const id of ['account-current-password','account-password','account-confirm','account-root-password'])$(id).value='';accountPending=false;setControls();}
});
function updateLoginForm(){
  const root=(authConfigured?authMode:$('auth-choice').value)==='entware';
  $('auth-choice-wrap').hidden=authConfigured;
  $('login-title').textContent=authConfigured?'Вход в панель':'Первый запуск';
  $('login-description').textContent=authConfigured?(root?'Введите существующий пароль root Entware.':'Войдите с логином и паролем панели.'):'Создайте учётную запись панели или используйте существующий пароль root Entware.';
  $('username').readOnly=root;if(root)$('username').value='root';else if(!authConfigured&&(!$('username').value||$('username').value==='root'))$('username').value='admin';
  $('password-label').textContent=root?'Пароль root Entware':'Пароль';
  $('password').autocomplete=authConfigured||root?'current-password':'new-password';
  $('password').maxLength=authConfigured||root?256:72;
  $('confirm-wrap').hidden=authConfigured||root;$('confirm-password').required=!authConfigured&&!root;
  $('login-submit').textContent=authConfigured?'Войти':root?'Войти как root Entware':'Создать учётную запись';
}
async function loadAuthInfo(){
  const response=await fetch('api.cgi?auth-info',{cache:'no-store',credentials:'omit'});const data=await response.json();
  if(!response.ok)throw new Error(data.error||'Не удалось проверить настройки входа.');
  authConfigured=data.configured;authMode=data.mode||'panel';
  $('auth-root-option').disabled=!data.rootAvailable;$('root-unavailable').hidden=data.rootAvailable;
  updateLoginForm();$('login-submit').disabled=false;
}
$('auth-choice').addEventListener('change',()=>{$('password').value='';$('confirm-password').value='';updateLoginForm();});
$('login-form').addEventListener('submit',async event=>{event.preventDefault();
  const button=$('login-submit');button.disabled=true;
  try{
    const body={username:$('username').value,password:$('password').value};
    if(!authConfigured){body.mode=$('auth-choice').value;body.confirm=$('confirm-password').value;}
    const response=await fetch('api.cgi?'+(authConfigured?'login':'setup'),{method:'POST',cache:'no-store',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    $('password').value='';$('confirm-password').value='';const data=await response.json();if(!response.ok)throw new Error(data.error||'Вход не выполнен.');
    if(data.key){key=data.key;await openPanel();}
    else{await loadAuthInfo();notice('Учётная запись создана. Войдите с заданным логином и паролем.');$('password').focus();}
  }catch(error){notice(error.message,true);}finally{button.disabled=false;}
});
$('logout').addEventListener('click',async()=>{try{await api('logout',{});}catch(error){notice(error.message,true);}finally{loggedOut();}});
$('reload-ui').addEventListener('click',()=>{window.location.href='/?v='+Date.now();});
$('browser').addEventListener('change',()=>{fillProfiles();updateDirty();});$('mode').addEventListener('change',()=>{showSni();updateDirty();});$('selection-mode').addEventListener('change',()=>{showSelection();updateDirty();});
$('mtu-mode').addEventListener('change',()=>{showMtu();updateDirty();});
for(const id of ['server','strategy','profile','sni','token','mtu','out-interface','ipv6'])$(id).addEventListener('input',updateDirty);
for(const id of ['server','strategy','profile','sni','mtu','out-interface'])$(id).addEventListener('change',updateDirty);
for(const id of ['health-enabled','health-interval','health-timeout','health-failures'])$(id).addEventListener('change',updateDirty);
$('fallback-enabled').addEventListener('change',()=>{$('fallback-fields').hidden=!$('fallback-enabled').checked;updateDirty();});
for(const id of fallbackProfiles)$(id).addEventListener('change',updateDirty);
for(const id of ['fallback-domains','fallback-budget'])$(id).addEventListener('change',updateDirty);
$('fallback-domains').addEventListener('input',updateDirty);
$('setup-interface').addEventListener('click',()=>command({action:'setup-interface'}));
$('preview').addEventListener('click',async()=>{const token=$('token').value.trim();if(!token){notice('Вставьте новый токен.',true);return;}$('preview').disabled=true;try{const data=await api('',{action:'preview',token});fillServers(data.servers,$('server').value);previewToken=token;loaded=true;updateDirty();$('settings-error').hidden=true;notice('Серверы прочитаны. Примените настройки.');}catch(error){notice(error.message,true);}finally{setControls();}});

function selectTab(name){
  activeTab=name;
  for(const tab of ['status','settings','system','logs']){
    $('view-'+tab).hidden=tab!==name;$('tab-'+tab).classList.toggle('active',tab===name);
    $('tab-'+tab).setAttribute('aria-current',tab===name?'page':'false');
  }
}
function renderUpdate(message){
  $('update-modal').hidden=false;$('panel-shell').inert=true;
  $('update-message').textContent=message;
  $('update-progress').hidden=Boolean(updateWatch?.finished);
  $('update-reload').hidden=false;$('update-close').hidden=!updateWatch?.finished;
}
function beginUpdate(target){
  if(updateWatch&&!updateWatch.finished)return;
  updateWatch={revision:target?.revision||null,originalRevision:lastData?.revision||null,version:target?.version||'',clientVersion:target?.clientVersion||'',accepted:false,uncertain:false,seenBusy:false,finished:false};
  const changes=[];
  if(updateWatch.clientVersion&&updateWatch.clientVersion!==lastData?.clientVersion)changes.push('клиента до '+updateWatch.clientVersion);
  if(updateWatch.version&&updateWatch.version!==UI_VERSION)changes.push('панели до '+updateWatch.version);
  $('update-title').textContent=changes.length?'Обновление '+changes.join(' и '):'Обновление сборки FPTN';
  $('update-detail').textContent='Клиент: '+(updateWatch.clientVersion||'уточняется')+' · Панель: '+(updateWatch.version||UI_VERSION)+'. Обновление страницы не отменяет принятую фоновую установку.';
  renderUpdate('Отправляем запрос на обновление…');$('update-dialog').focus();
}
function finishUpdate(ok,message){
  if(!updateWatch)return;
  updateWatch.finished=true;updateWatch.ok=ok;pending=false;
  $('update-title').textContent=ok?'Обновление установлено':'Обновление не завершено';
  $('update-detail').textContent=ok?'Обновите страницу, чтобы увидеть актуальные версии и состояние службы.':'Если операция была прервана, используйте «Восстановить управление» после закрытия окна.';
  renderUpdate(message);$('update-reload').focus();
}
function observeUpdate(data){
  if(data.busy&&data.operation==='update'){
    if(!updateWatch||updateWatch.finished)beginUpdate(updateInfo);
    updateWatch.accepted=true;updateWatch.uncertain=false;updateWatch.seenBusy=true;
    renderUpdate(updateWatch.revision&&data.revision===updateWatch.revision?'Файлы панели обновлены. Завершаем установку и проверку службы…':'Выполняются загрузка, проверка и установка обновления…');
    if(data.operationState?.stale)finishUpdate(false,data.operationState.message||'Операция прервана. Требуется восстановление управления.');
    return;
  }
  if(!updateWatch||updateWatch.finished||data.busy)return;
  if(!updateWatch.accepted){
    if(updateWatch.uncertain&&updateWatch.revision&&updateWatch.revision!==updateWatch.originalRevision&&data.revision===updateWatch.revision&&data.result?.action==='update'&&data.result.ok===true)finishUpdate(true,data.result.message||'Новая версия установлена.');
    return;
  }
  const result=data.result;
  if(result?.ok===false){finishUpdate(false,result.message||'Установка завершилась ошибкой.');return;}
  if(result?.action==='update'&&result.ok===true){
    if(updateWatch.revision&&data.revision!==updateWatch.revision){finishUpdate(false,'Операция завершилась, но ожидаемая версия не подтверждена. Перезагрузите панель и проверьте версию.');return;}
    finishUpdate(true,result.message||'Новая версия установлена.');
  }else renderUpdate('Ожидаем подтверждения результата установки…');
}
for(const name of ['status','settings','system','logs'])$('tab-'+name).addEventListener('click',()=>selectTab(name));
$('update-reload').addEventListener('click',()=>{window.location.href='/?v='+Date.now();});
$('update-close').addEventListener('click',()=>{
  if(!updateWatch?.finished)return;
  $('update-modal').hidden=true;$('panel-shell').inert=false;updateWatch=null;setControls();$('check-update').focus();
});
$('update-dialog').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();if(updateWatch?.finished)$('update-close').click();}
  if(event.key==='Tab'){
    const buttons=[$('update-reload'),$('update-close')].filter(el=>!el.hidden&&!el.disabled);
    if(!buttons.length){event.preventDefault();$('update-dialog').focus();return;}
    if(event.shiftKey&&document.activeElement===buttons[0]){event.preventDefault();buttons.at(-1).focus();}
    else if(!event.shiftKey&&document.activeElement===buttons.at(-1)){event.preventDefault();buttons[0].focus();}
  }
});

async function command(body){
  if(pending||versionMismatch||(updateWatch&&!updateWatch.finished)||(lastBusy&&!(body.action==='recover'&&lastData?.operationState?.canRecover)))return;
  if(body.action==='update')beginUpdate(updateInfo);
  pending=true;setControls();
  try{await api('',body);if(body.action==='update'&&!updateWatch?.finished){updateWatch.accepted=true;renderUpdate('Запрос принят. Ожидаем завершения установки…');}else if(body.action!=='check-update')notice('Операция запущена. Состояние обновляется автоматически.');await poll();}
  catch(error){
    if(body.action==='update'&&updateWatch&&!updateWatch.finished){
      if(error.status){finishUpdate(false,error.message);}
      else{updateWatch.uncertain=true;renderUpdate('Ответ не получен. Запрос мог быть принят — ожидаем состояния панели…');}
    }else{pending=false;notice(error.message,true);}
    setControls();
  }
}
document.querySelectorAll('[data-command]').forEach(button=>button.addEventListener('click',()=>command({action:button.dataset.command})));
$('recover-operation').addEventListener('click',()=>command({action:'recover'}));
$('autostart-toggle').addEventListener('click',()=>command({action:lastData?.autostart?'disable':'enable'}));
$('check-servers').addEventListener('click',()=>{if(dirty){notice('Сначала примените изменения настроек.',true);return;}command({action:'check'});});
$('settings-form').addEventListener('submit',event=>{event.preventDefault();const settings=collectSettings();if(settings.fallback.enabled&&settings.fallback.profiles.some(method=>!clientCapabilities?.methods.includes(method))){notice('Выберите запасные профили, доступные в установленном клиенте.',true);return;}if(settings.fallback.enabled&&(favorites.size<1||favorites.size>8||settings.fallback.profiles.length<1||settings.fallback.profiles.length>2||settings.fallback.domains.length<1||settings.fallback.domains.length>2)){notice('Для подбора выберите 1–8 избранных, 1–2 профиля и 1–2 SNI.',true);return;}if(settings.token&&settings.token!==previewToken){notice('Сначала нажмите «Прочитать серверы» для нового токена.',true);return;}const allowed=serverNames.filter(name=>!excluded.has(name));if(!allowed.length||allowed.length>64){notice('Оставьте от 1 до 64 разрешённых серверов.',true);return;}command({action:'save',...settings});});
$('port-form').addEventListener('submit',event=>{event.preventDefault();const port=Number($('panel-port').value);if(!Number.isInteger(port)||port<1||port>65535){notice('Введите TCP-порт 1–65535.',true);return;}if(dirty){notice('Сначала примените или отмените изменения подключения.',true);return;}if(lastData?.portChange){notice('Сначала завершите или отмените предыдущую смену порта.',true);return;}if(port===Number(lastData?.panelOrigin?.split(':').pop())){notice('Панель уже использует этот порт.');return;}command({action:'change-port',port});});
$('port-rollback').addEventListener('click',()=>command({action:'cancel-port'}));
$('port-confirm').addEventListener('click',async()=>{try{await api('',{action:'confirm-port',id:lastData?.portChange?.id});notice('Новый адрес подтверждён. Дождитесь завершения перезапуска панели.');}catch(error){notice(error.message,true);}});
$('download-diagnostics').addEventListener('click',async()=>{try{const data=await api('diagnostics');const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='fptn-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message,true);}});
$('refresh-logs').addEventListener('click',async()=>{try{$('logs').textContent=(await api('logs')).log||'Журнал пока пуст.';}catch(error){notice(error.message,true);}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});setInterval(()=>{if(!document.hidden)poll();},5000);
$('auth-retry').addEventListener('click',initializePanel);
selectTab('status');fillProfiles();setControls();initializePanel();

async function loadUpdates(){
  updateInfo=await api('updates');
  $('install-update').hidden=!updateInfo.available;
  $('update-status').textContent=!updateInfo.checked?'Обновления ещё не проверены.':updateInfo.available?'Доступна панель '+updateInfo.version+' · клиент Keenetic '+updateInfo.clientVersion+(updateInfo.upstreamVersion?' · исходный FPTN '+updateInfo.upstreamVersion:'')+(updateInfo.clientUpdate?' · новая сборка клиента':'')+'. Настройки и автозапуск сохраняются.':'Опубликованных обновлений нет.';
  setControls();
}
$('check-update').addEventListener('click',()=>command({action:'check-update'}));
$('install-update').addEventListener('click',()=>{if(dirty){notice('Сначала сохраните изменения настроек.',true);return;}if(updateInfo?.available)command({action:'update'});});
