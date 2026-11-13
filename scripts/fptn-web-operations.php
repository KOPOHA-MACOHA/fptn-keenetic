<?php
// Included by the private PHP helper, never directly served by lighttpd.
$private=__DIR__.'/private-state.php';if(!is_file($private))$private=__DIR__.'/fptn-private-state.php';
require $private;
$directory = $run . '/action';
// Never hold the operation mutex while waiting for a caller to finish stdin.
$pendingRequest=$action==='op-reserve' ? json_decode((string)file_get_contents('php://stdin',false,null,0,300001),true) : null;
if (!is_dir($run)) mkdir($run, 0700, true);
$mutex = fopen($run . '/operation.lock', 'c');
if (!$mutex || !flock($mutex, LOCK_EX)) failAuth('Не удалось проверить операцию.');
function processRecord(int $pid): ?array {
    $stat = @file_get_contents('/proc/' . $pid . '/stat');
    if ($stat === false) return null;
    $end = strrpos($stat, ')');
    if ($end === false) return null;
    $parts = preg_split('/\s+/', trim(substr($stat, $end + 1)));
    if (count($parts) < 20 || in_array($parts[0], ['Z','X'], true)) return null;
    return ['pid'=>$pid,'start'=>$parts[19],'boot'=>trim((string)file_get_contents('/proc/sys/kernel/random/boot_id'))];
}
function operationState(): array {
    global $directory;
    if (!is_dir($directory)) return ['busy'=>false,'stale'=>false,'canRecover'=>false,'action'=>'','message'=>''];
    $name = trim((string)@file_get_contents($directory . '/action'));
    $owner = json_decode((string)@file_get_contents($directory . '/owner.json'), true);
    $state = ['busy'=>true,'stale'=>false,'canRecover'=>false,'action'=>$name,'message'=>'Операция выполняется.'];
    if (!is_array($owner) || !preg_match('/\A[a-f0-9]{32}\z/D', $owner['id'] ?? '')) {
        $state['message']='Операция старой версии без журнала. Автоматическое удаление её блокировки запрещено.';return $state;
    }
    $live = processRecord((int)($owner['pid'] ?? 0));
    if ($live && $live['start'] === ($owner['start'] ?? null) && $live['boot'] === ($owner['boot'] ?? null)) return $state;
    // The worker can be killed while a foreground controller/probe still runs.
    // Inspect only the operation marker, never expose process environments.
    $uid = fileowner('/proc/' . getmypid());
    $marker = 'FPTN_OPERATION_ID=' . $owner['id'];
    foreach (glob('/proc/[0-9]*', GLOB_ONLYDIR) ?: [] as $process) {
        if (@fileowner($process) !== $uid) continue;
        $record=processRecord((int)basename($process));
        // Existing unrelated daemons cannot have inherited a newly generated ID.
        // In particular, do not require access to protected older processes.
        if (!$record || (float)$record['start'] < (float)($owner['reservedStart'] ?? '0')) continue;
        $env = @file_get_contents($process . '/environ');
        if ($env === false) { $state['message']='Не удалось проверить дочерние процессы. Повторите позже.'; return $state; }
        if (in_array($marker, explode("\0", $env), true)) { $state['message']='Дочерний процесс операции ещё работает.'; return $state; }
    }
    $state['stale']=true;$state['canRecover']=true;
    $state['message']=is_file($directory . '/rollback-needed') ? 'Сохранение прервано. Можно восстановить прежние настройки.' : ($name==='update' ? 'Обновление прервано. Восстановите управление и повторите установку.' : 'Операция прервана. Можно восстановить управление.');
    return $state;
}
function writeOwner(string $id, string $name): void {
    global $directory;
    $pid = (int)getenv('FPTN_OWNER_PID');$record=processRecord($pid);
    if ($record === null) failAuth('Не удалось определить процесс операции.');
    $previous=json_decode((string)@file_get_contents($directory.'/owner.json'),true);
    $record['reservedStart']=is_array($previous) && ($previous['id']??'')===$id
        ? ($previous['reservedStart']??'0') : processRecord(getmypid())['start'];
    $record['id']=$id;$record['action']=$name;
    $temp=$directory . '/owner.new';
    if (file_put_contents($temp,json_encode($record)."\n") === false || !rename($temp,$directory.'/owner.json')) failAuth('Не удалось записать журнал операции.');
}
if ($action === 'op-state') { reply(operationState());exit; }
if ($action === 'op-own') {
    $owner=json_decode((string)@file_get_contents($directory.'/owner.json'),true);
    if (!is_array($owner) || !hash_equals($owner['id'], getenv('FPTN_OPERATION_ID') ?: '')) failAuth('Изменилась операция.');
    privateMigrateOperation($directory,$owner['id']);
    writeOwner($owner['id'],trim((string)file_get_contents($directory.'/action')));reply(['ok'=>true]);exit;
}
if (in_array($action,['op-private-dir','op-private-clean','op-sanitize-history','op-purge-check','op-purge'],true)) {
    $owner=json_decode((string)@file_get_contents($directory.'/owner.json'),true);
    $id=getenv('FPTN_OPERATION_ID')?:'';
    if(!is_array($owner)||!hash_equals($owner['id']??'',$id))failAuth('Изменилась операция.');
    if($action==='op-private-dir'){reply(['path'=>privatePath($id)]);exit;}
    if($action==='op-private-clean'){privateClear($id);reply(['ok'=>true]);exit;}
    if($action==='op-sanitize-history'){privateScrubHistory();reply(['ok'=>true]);exit;}
    $paths=[$conf,$run,$root.'/var/run/fptn-capabilities',$root.'/var/run/fptn-keenetic',privateBase()];
    foreach($paths as $path)if(file_exists($path)||is_link($path))privateCheckRemoval($path);
    $files=[];
    foreach(['fptnctl.previous','fptnctl-backup.*'] as $pattern)
        foreach(glob($root.'/bin/'.$pattern)?:[] as $file)
            if(basename($file)==='fptnctl.previous'||preg_match('/\Afptnctl-backup\.[A-Za-z0-9]{6}\z/D',basename($file)))$files[]=$file;
    // Log files belong to this client only; never erase other applications' logs.
    $logs=$root==='/opt'?'/var/log/fptn':$root.'/var/log/fptn';
    if(is_dir($logs)&&!is_link($logs))foreach(glob($logs.'/fptn-client-cli*.log')?:[] as $file)
        if(preg_match('/\Afptn-client-cli(?:\.[0-9]+)?\.log\z/D',basename($file)))$files[]=$file;
    $feed=$root.'/etc/opkg/fptn-keenetic.conf';
    if(is_file($feed)&&!is_link($feed)&&strpos((string)file_get_contents($feed),'# Managed by FPTN package channel')!==false){
        $files[]=$feed;
        foreach(['var/opkg-lists','var/lib/opkg/lists'] as $lists){
            foreach(['fptn_keenetic','fptn_keenetic.sig'] as $name)if(file_exists($root.'/'.$lists.'/'.$name))$files[]=$root.'/'.$lists.'/'.$name;
        }
    }
    foreach($files as $file){$st=@lstat($file);if(!$st||!in_array($st['mode']&0170000,[0100000,0120000],true))throw new RuntimeException('Неожиданный файл FPTN.');}
    if($action==='op-purge'){
        foreach($files as $file)if(!unlink($file))throw new RuntimeException('Не удалось удалить файл FPTN.');
        foreach($paths as $path)if(file_exists($path))privateErase($path);
        @rmdir($logs);
    }
    reply(['ok'=>true]);exit;
}
if ($action === 'op-reserve') {
    if (is_dir($directory)) failAuth('Дождитесь операции или восстановите управление в панели.');
    $body=$pendingRequest;
    $name=$body['action']??'';
    if (!in_array($name,['start','stop','restart','save','check','enable','disable','update','check-update','setup-interface','change-port','cancel-port'],true)) failAuth('Некорректная операция.');
    $id=bin2hex(random_bytes(16));$prepared=$run.'/reserve-'.$id;
    if (!mkdir($prepared,0700)) failAuth('Не удалось создать журнал операции.');
    $target=$directory;$directory=$prepared;
    writeOwner($id,$name);
    file_put_contents($prepared.'/action',$name."\n");
    if(is_string($body['token']??null))privateWrite(privatePath($id).'/request-token',$body['token']);
    file_put_contents($prepared.'/request.json',json_encode(privateRedactRequest($body)));
    if (is_dir($target) || !rename($prepared,$target)) failAuth('Параллельно началась другая операция.');
    @unlink($run.'/result.json');reply(['id'=>$id]);exit;
}
if ($action === 'op-recover') {
    $state=operationState();
    if (!$state['canRecover']) failAuth($state['message'] ?: 'Нет прерванной операции.');
    $old=json_decode((string)file_get_contents($directory.'/owner.json'),true);
    privateMigrateOperation($directory,$old['id']);
    $archive=$conf.'/web-recovery/'.$old['id'];
    if (!is_dir($archive)) mkdir($archive,0700,true);
    $allowed=['owner.json','action','request.json','was-running','rollback-needed','old-token-present','old-server','old-connection.json','old-server-policy.json','old-fallback.json','old-interface-mtu','install-backup'];
    foreach($allowed as $name){$file=$directory.'/'.$name;if(is_file($file)&&!is_link($file))copy($file,$archive.'/'.$name);}
    privateScrubHistory();
    $id=bin2hex(random_bytes(16));
    $previous=privatePath($old['id'],false);$next=privatePath($id,false);
    if(is_dir($previous)&&!rename($previous,$next))failAuth('Не удалось перенести временные данные.');
    writeOwner($id,'recover');
    file_put_contents($directory.'/action',"recover\n");
    @unlink($run.'/result.json');reply(['id'=>$id,'backup'=>$archive]);exit;
}
failAuth('Неизвестная операция.');
