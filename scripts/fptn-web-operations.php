<?php
// Included by the private PHP helper, never directly served by lighttpd.
$directory = $run . '/action';
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
        if (@fileowner($process) !== $uid || !processRecord((int)basename($process))) continue;
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
    $record['id']=$id;$record['action']=$name;
    $temp=$directory . '/owner.new';
    if (file_put_contents($temp,json_encode($record)."\n") === false || !rename($temp,$directory.'/owner.json')) failAuth('Не удалось записать журнал операции.');
}
if ($action === 'op-state') { reply(operationState());exit; }
if ($action === 'op-own') {
    $owner=json_decode((string)@file_get_contents($directory.'/owner.json'),true);
    if (!is_array($owner) || !hash_equals($owner['id'], getenv('FPTN_OPERATION_ID') ?: '')) failAuth('Изменилась операция.');
    writeOwner($owner['id'],trim((string)file_get_contents($directory.'/action')));reply(['ok'=>true]);exit;
}
if ($action === 'op-reserve') {
    if (is_dir($directory)) failAuth('Дождитесь операции или восстановите управление в панели.');
    $body=json_decode((string)file_get_contents('php://stdin',false,null,0,300001),true);
    $name=$body['action']??'';
    if (!in_array($name,['start','stop','restart','save','check','enable','disable','update'],true)) failAuth('Некорректная операция.');
    $id=bin2hex(random_bytes(16));$prepared=$run.'/reserve-'.$id;
    if (!mkdir($prepared,0700)) failAuth('Не удалось создать журнал операции.');
    $target=$directory;$directory=$prepared;
    writeOwner($id,$name);
    file_put_contents($prepared.'/action',$name."\n");file_put_contents($prepared.'/request.json',json_encode($body));
    if (is_dir($target) || !rename($prepared,$target)) failAuth('Параллельно началась другая операция.');
    @unlink($run.'/result.json');reply(['id'=>$id]);exit;
}
if ($action === 'op-recover') {
    $state=operationState();
    if (!$state['canRecover']) failAuth($state['message'] ?: 'Нет прерванной операции.');
    $old=json_decode((string)file_get_contents($directory.'/owner.json'),true);
    $archive=$conf.'/web-recovery/'.$old['id'];
    if (!is_dir($archive)) mkdir($archive,0700,true);
    foreach (glob($directory.'/*') ?: [] as $file) if(is_file($file)) copy($file,$archive.'/'.basename($file));
    $id=bin2hex(random_bytes(16));writeOwner($id,'recover');
    file_put_contents($directory.'/action',"recover\n");
    @unlink($run.'/result.json');reply(['id'=>$id,'backup'=>$archive]);exit;
}
failAuth('Неизвестная операция.');
