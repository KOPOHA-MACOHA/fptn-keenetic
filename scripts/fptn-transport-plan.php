<?php
// Private planning helper: no network scanning, protocol changes, or credential output.
function clientCapabilities(): array {
    global $root;
    static $cached=null;if($cached!==null)return $cached;
    $helper=$root.'/libexec/fptn/client-capabilities.sh';if(!is_file($helper))$helper=__DIR__.'/fptn-client-capabilities.sh';
    $process=proc_open(['sh',$helper],[0=>['file','/dev/null','r'],1=>['pipe','w'],2=>['file','/dev/null','w']],$pipes);
    if(!is_resource($process))failAuth('Не удалось прочитать возможности клиента.');
    $text=stream_get_contents($pipes[1],65537);fclose($pipes[1]);$code=proc_close($process);
    $value=json_decode((string)$text,true);
    if($code!==0 || strlen((string)$text)>65536 || !is_array($value) || ($value['schema']??null)!==1 || !is_array($value['methods']??null))failAuth('Не удалось прочитать возможности клиента.');
    return $cached=$value;
}
function fallbackSettings($value): array {
    if (!is_array($value) || !is_bool($value['enabled']??null) || !is_array($value['profiles']??null) || !is_array($value['domains']??null)) failAuth('Некорректные настройки резервных методов.');
    $supported=clientCapabilities()['methods'];
    if (count($value['profiles'])>2 || count($value['domains'])>2 || !in_array($value['budget']??null,[30,60,90,120],true)) failAuth('Выберите до двух профилей, до двух SNI и лимит 30–120 секунд.');
    foreach($value['profiles'] as $profile) if(!is_string($profile) || !preg_match('/\Asni-spoofing-[a-z][a-z0-9]*-[0-9]+(?:-[0-9]+)*\z/D',$profile) || strlen($profile)>128 || ($value['enabled'] && !in_array($profile,$supported,true))) failAuth('Неизвестный запасной профиль.');
    $domains=[];
    foreach($value['domains'] as $domain){
        if(!is_string($domain) || strlen($domain)>253 || !preg_match('/\A[a-zA-Z0-9.-]+\z/D',$domain)) failAuth('Укажите корректные домены SNI.');
        $labels=explode('.',$domain);if(count($labels)<2) failAuth('В домене SNI нужна точка.');
        foreach($labels as $label) if(strlen($label)>63 || !preg_match('/\A[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\z/D',$label)) failAuth('Некорректная часть домена SNI.');
        $domains[]=strtolower($domain);
    }
    $profiles=array_values(array_unique($value['profiles']));$domains=array_values(array_unique($domains));
    if($value['enabled'] && (!$profiles || !$domains)) failAuth('Для подбора нужен хотя бы один профиль и один SNI.');
    return ['enabled'=>$value['enabled'],'profiles'=>$profiles,'domains'=>$domains,'budget'=>$value['budget']];
}
function readObject(string $path, int $limit=65536): array {
    $text=@file_get_contents($path,false,null,0,$limit+1);
    if($text===false || strlen($text)>$limit) failAuth('Не удалось прочитать настройки подключения.');
    $value=json_decode($text,true);if(!is_array($value)) failAuth('Повреждены настройки подключения.');return $value;
}
function defaultFallbackProfiles(): array {
    $latest=[];
    foreach(clientCapabilities()['methods'] as $method)if(preg_match('/\Asni-spoofing-([a-z][a-z0-9]*)-([0-9-]+)\z/D',$method,$m)){
        $version=str_replace('-','.',$m[2]);
        if(!isset($latest[$m[1]]) || version_compare($version,$latest[$m[1]][1],'>'))$latest[$m[1]]=[$method,$version];
    }
    return array_slice(array_values(array_map(fn($item)=>$item[0],$latest)),0,2);
}
function configuredFallback(): array {
    global $conf;
    return fallbackSettings(is_file($conf.'/fallback.json') ? readObject($conf.'/fallback.json') : ['enabled'=>false,'profiles'=>defaultFallbackProfiles(),'domains'=>[],'budget'=>60]);
}
if($action==='transport-settings'){reply(configuredFallback());exit;}
if($action==='transport-validate'){
    $body=json_decode((string)file_get_contents('php://stdin',false,null,0,131073),true);if(!is_array($body))failAuth('Некорректные настройки подбора.');$settings=fallbackSettings($body['fallback']??$body);
    if($settings['enabled'] && isset($body['policy'])){
        $policy=$body['policy'];$denied=array_map(fn($n)=>strtolower(trim($n)),$policy['excluded']??[]);
        $favorites=array_filter($policy['favorites']??[],fn($n)=>!in_array(strtolower(trim($n)),$denied,true));
        if(count($favorites)<1 || count($favorites)>8) failAuth('Для резервного подбора оставьте от 1 до 8 разрешённых избранных серверов.');
    }
    reply($settings);exit;
}
if($action==='transport-cache'){
    $plan=readObject($root.'/var/run/fptn-keenetic/transport-plan.json',1048576);
    $effective=readObject($root.'/var/run/fptn-keenetic/effective-connection.json');
    $server=trim((string)@file_get_contents($root.'/var/run/fptn-keenetic/active-server'));
    if(!in_array($server,$plan['favorites']??[],true)) {reply(['ok'=>true]);exit;}
    $cache=['key'=>$plan['key'],'server'=>$server,'bypass'=>$effective['bypass'],'sni'=>$effective['sni'],'savedAt'=>time()];
    $path=$conf.'/last-good-transport.json';
    if(file_put_contents($path.'.new',json_encode($cache)."\n")===false || !rename($path.'.new',$path)) failAuth('Не удалось сохранить рабочий метод.');
    reply(['ok'=>true]);exit;
}
if($action!=='transport-plan') failAuth('Неизвестная операция подбора.');
$settings=configuredFallback();
if(!$settings['enabled']) {reply(['enabled'=>false]);exit;}
$base=readObject($conf.'/connection.json');$policy=readObject($conf.'/server-policy.json');
$raw=@file_get_contents($conf.'/token',false,null,0,262145);
if($raw===false || strlen($raw)>262144) failAuth('Не удалось прочитать токен для списка серверов.');
$clean=preg_replace('/\s+/','',$raw);$compressed=false;
foreach(['fptnb:','fptnb//','fptn://','fptn:'] as $prefix) if(strncmp($clean,$prefix,strlen($prefix))===0){$clean=substr($clean,strlen($prefix));$compressed=strncmp($prefix,'fptnb',5)===0;break;}
$payload=base64_decode(strtr($clean,'-_','+/'),true);
if($payload===false) failAuth('Не удалось декодировать список серверов.');
if($compressed){
    $in=tmpfile();fwrite($in,$payload);rewind($in);
    $process=proc_open(['nice','-n','10','timeout','-s','TERM','5',$root.'/libexec/fptn/brotli','-d','-c'],[0=>$in,1=>['pipe','w'],2=>['file','/dev/null','w']],$pipes);
    if(!is_resource($process)) failAuth('Не удалось запустить декодер токена.');
    $payload=stream_get_contents($pipes[1],1048577);fclose($pipes[1]);$code=proc_close($process);fclose($in);
    if($code!==0 || $payload===false || strlen($payload)>1048576) failAuth('Не удалось распаковать список серверов.');
}
$token=json_decode($payload,true);
if(!is_array($token) || !is_array($token['servers']??null) || count($token['servers'])>512) failAuth('В токене нет корректного списка серверов.');
$names=[];foreach($token['servers'] as $server){$name=$server['name']??null;if(!is_string($name)||strlen($name)>512||$name==='') failAuth('Некорректное имя сервера.');$names[]=$name;}
$denied=array_map(fn($n)=>strtolower(trim($n)),$policy['excluded']??[]);
$allowed=array_values(array_filter(array_unique($names),fn($n)=>!in_array(strtolower(trim($n)),$denied,true)));
if(count($allowed)>64 || !$allowed) failAuth('Слишком много разрешённых серверов или все исключены.');
$favKeys=array_map(fn($n)=>strtolower(trim($n)),$policy['favorites']??[]);
$favorites=array_values(array_filter($allowed,fn($n)=>in_array(strtolower(trim($n)),$favKeys,true)));
if(!$favorites || count($favorites)>8) failAuth('Для подбора нужен список из 1–8 разрешённых избранных.');
$key=hash('sha256',json_encode([$base,$policy,$settings,hash('sha256',$raw)]));
$cached=json_decode((string)@file_get_contents($conf.'/last-good-transport.json',false,null,0,65537),true);$cache=is_array($cached)?$cached:[];
$cacheValid=($cache['key']??'')===$key && in_array($cache['server']??null,$favorites,true)
    && (($cache['bypass']??'')==='obfuscation'||in_array($cache['bypass']??'',$settings['profiles'],true)||($cache['bypass']??'')===$base['bypass'])
    && (in_array($cache['sni']??'',$settings['domains'],true)||($cache['sni']??'')===$base['sni']);
$plans=[];$seen=[];
$append=function(string $stage,array $eligible,array $connection,bool $budgeted) use(&$plans,&$seen,$names):void {
    $eligible=array_values(array_filter($eligible,function($name) use(&$seen,$connection){$key=$connection['bypass']."\0".$connection['sni']."\0".$name;if(isset($seen[$key]))return false;$seen[$key]=true;return true;}));
    if(!$eligible)return;
    $p=['mode'=>'auto','favorites'=>$eligible,'excluded'=>array_values(array_diff($names,$eligible))];
    if(strlen(json_encode($p))>65536) failAuth('Список серверов слишком велик для резервного подбора.');
    $plans[]=['stage'=>$stage,'eligible'=>$eligible,'budgeted'=>$budgeted,'connection'=>$connection,'policy'=>$p];
};
$manual=trim((string)@file_get_contents($conf.'/server'));
foreach($allowed as $candidate)if(strtolower(trim($candidate))===strtolower($manual)){$manual=$candidate;break;}
$manualMode=($policy['mode']??'')==='manual' && getenv('FPTN_FORCE_AUTO')!=='yes' && in_array($manual,$allowed,true);
if($manualMode){$connection=$base;if($cacheValid&&$cache['server']===$manual){$connection['bypass']=$cache['bypass'];$connection['sni']=$cache['sni'];}$append('manual',[$manual],$connection,in_array($manual,$favorites,true));}
elseif($cacheValid){$connection=$base;$connection['bypass']=$cache['bypass'];$connection['sni']=$cache['sni'];$append('cached',[$cache['server']],$connection,true);}
$append('favorites',$favorites,$base,true);
$plain=$base;$plain['bypass']='obfuscation';$append('favorites',$favorites,$plain,true);
foreach($settings['profiles'] as $profile)foreach($settings['domains'] as $domain){$connection=$base;$connection['bypass']=$profile;$connection['sni']=$domain;$append('favorites',$favorites,$connection,true);}
$append('others',array_values(array_diff($allowed,$favorites)),$base,false);
$count=count(array_filter($plans,fn($p)=>$p['budgeted']));
foreach($plans as &$plan)$plan['timeout']=$plan['budgeted']?min(30,max(8,(int)floor($settings['budget']/max(1,$count)))):($plan['stage']==='manual'?60:540);unset($plan);
reply(['enabled'=>true,'key'=>$key,'budget'=>$settings['budget'],'favorites'=>$favorites,'plans'=>$plans]);
