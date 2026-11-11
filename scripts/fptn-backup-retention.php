<?php
// Private, one-shot installer maintenance; not a CGI endpoint or a resident task.
declare(strict_types=1);
umask(0077);
set_exception_handler(function (Throwable $error): void {
    fwrite(STDERR, "FPTN: обслуживание резервных копий пропущено: " . $error->getMessage() . "\n");
    exit(1);
});
function requireBackup(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}
function privateStat(string $path, int $uid, bool $directory = false): array {
    clearstatcache(true, $path);
    $s = @lstat($path);
    requireBackup(is_array($s) && ($s['mode'] & 0170000) === ($directory ? 0040000 : 0100000)
        && $s['uid'] === $uid && ($s['mode'] & 0022) === 0,
        'не подтверждено владение служебным файлом или каталогом');
    return $s;
}
function readBackupJson(string $path, int $uid): array {
    $s = privateStat($path, $uid);
    requireBackup($s['size'] <= 4096, 'неверный размер журнала');
    $data = json_decode((string)file_get_contents($path), true);
    requireBackup(is_array($data), 'повреждён журнал');
    return $data;
}
function writeBackupJson(string $path, array $data): void {
    $temporary = $path . '.new.' . bin2hex(random_bytes(8));
    $file = @fopen($temporary, 'x');
    requireBackup($file !== false, 'не удалось создать журнал');
    try {
        $text = json_encode($data, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
        requireBackup(fwrite($file, $text) === strlen($text) && fflush($file), 'не удалось записать журнал');
        if (function_exists('fsync')) requireBackup(fsync($file), 'не удалось сохранить журнал');
        requireBackup(chmod($temporary, 0600) && rename($temporary, $path), 'не удалось заменить журнал');
    } finally {
        fclose($file);
        if (file_exists($temporary)) @unlink($temporary);
    }
}
function backupPath(string $path, string $conf, int $uid): string {
    $name = basename($path);
    requireBackup((bool)preg_match('/\Aweb-install-backup\.[A-Za-z0-9]{6}\z/D', $name), 'неверное имя резервной копии');
    privateStat($path, $uid, true);
    requireBackup(realpath($path) === $conf . '/' . $name && !is_link($path), 'копия находится вне собственного каталога');
    return $conf . '/' . $name;
}
function backupReference(string $path, string $conf, int $uid): string {
    $s = privateStat($path, $uid);
    requireBackup($s['size'] <= 4096, 'повреждена ссылка восстановления');
    $target = trim((string)file_get_contents($path));
    // Missing targets cannot be deleted, but malformed/outside references stop GC.
    requireBackup((bool)preg_match('/\Aweb-install-backup\.[A-Za-z0-9]{6}\z/D', basename($target))
        && realpath(dirname($target)) === $conf, 'неверная ссылка восстановления');
    return $conf . '/' . basename($target);
}
function backupProtected(string $run, string $conf, int $uid): array {
    $protected = [];
    $active = $run . '/action/install-backup';
    if (file_exists($active) || is_link($active)) $protected[backupReference($active, $conf, $uid)] = true;
    $recovery = $conf . '/web-recovery';
    if (!file_exists($recovery) && !is_link($recovery)) return $protected;
    privateStat($recovery, $uid, true);
    foreach (new DirectoryIterator($recovery) as $entry) {
        if ($entry->isDot()) continue;
        if (!preg_match('/\A[a-f0-9]{32}\z/D', $entry->getFilename())) continue;
        privateStat($entry->getPathname(), $uid, true);
        $reference = $entry->getPathname() . '/install-backup';
        if (file_exists($reference) || is_link($reference)) $protected[backupReference($reference, $conf, $uid)] = true;
    }
    return $protected;
}
function checkBackupMounts(string $path): void {
    $mounts = @file('/proc/self/mountinfo', FILE_IGNORE_NEW_LINES);
    requireBackup(is_array($mounts), 'не удалось проверить точки монтирования');
    foreach ($mounts as $line) {
        $fields = explode(' ', $line);
        requireBackup(count($fields) >= 6, 'не удалось прочитать точки монтирования');
        $mount = preg_replace_callback('/\\\\([0-7]{3})/', function ($m) { return chr(octdec($m[1])); }, $fields[4]);
        requireBackup($mount !== $path && strpos($mount, $path . '/') !== 0, 'в копии найдена точка монтирования');
    }
}
function backupTreeCheck(string $path, int $device, int &$count, int $depth = 0): void {
    requireBackup(++$count <= 10000 && $depth <= 64, 'слишком большое дерево копии');
    $s = @lstat($path);
    requireBackup(is_array($s) && $s['dev'] === $device, 'в копии найдена другая файловая система');
    $type = $s['mode'] & 0170000;
    requireBackup(in_array($type, [0040000, 0100000, 0120000], true), 'неожиданный тип файла в копии');
    if ($type !== 0040000) return; // Never follow a symlink.
    foreach (new DirectoryIterator($path) as $entry) {
        if (!$entry->isDot()) backupTreeCheck($entry->getPathname(), $device, $count, $depth + 1);
    }
}
function removeBackupTree(string $path, bool $root = false): void {
    $s = @lstat($path);
    requireBackup(is_array($s), 'файлы копии изменились во время очистки');
    if (($s['mode'] & 0170000) !== 0040000) {
        requireBackup(unlink($path), 'не удалось удалить файл старой копии');
        return;
    }
    foreach (new DirectoryIterator($path) as $entry) {
        if ($entry->isDot() || ($root && $entry->getFilename() === '.fptn-backup.json')) continue;
        removeBackupTree($entry->getPathname());
    }
    // Keep the ownership receipt until all payload files have been removed, so
    // an interrupted removal can be retried without guessing directory ownership.
    if ($root) requireBackup(unlink($path . '/.fptn-backup.json'), 'не удалось удалить журнал старой копии');
    requireBackup(rmdir($path), 'не удалось удалить каталог старой копии');
}
$root = rtrim(getenv('FPTN_PREFIX') ?: '/opt', '/');
$uid = fileowner('/proc/' . getmypid());
requireBackup(is_int($uid), 'не удалось определить владельца процесса');
$confPath = $root . '/etc/fptn';
privateStat($confPath, $uid, true);
$conf = realpath($confPath);
requireBackup(is_string($conf), 'не найден каталог FPTN');
$run = $root . '/var/run/fptn-web';
$action = getenv('FPTN_BACKUP_ACTION') ?: '';
requireBackup(in_array($action, ['begin', 'commit', 'failed'], true), 'неизвестная операция журнала');
$operation = getenv('FPTN_OPERATION_ID') ?: '';
$revision = getenv('FPTN_BACKUP_REVISION') ?: '';
requireBackup((bool)preg_match('/\A[a-f0-9]{32}\z/D', $operation)
    && (bool)preg_match('/\A[a-f0-9]{40}\z/D', $revision), 'неверный идентификатор установки');
privateStat($run . '/action', $uid, true);
$owner = readBackupJson($run . '/action/owner.json', $uid);
requireBackup(($owner['id'] ?? null) === $operation, 'изменился владелец установки');
privateStat($run . '/action/action', $uid);
requireBackup(trim((string)file_get_contents($run . '/action/action')) === 'update', 'идёт другая операция');
$current = backupPath(getenv('FPTN_BACKUP_PATH') ?: '', $conf, $uid);
requireBackup(backupReference($run . '/action/install-backup', $conf, $uid) === $current, 'изменена ссылка текущей установки');
$lockPath = $conf . '/.web-backup-retention.lock';
if (file_exists($lockPath) || is_link($lockPath)) privateStat($lockPath, $uid);
$mutex = @fopen($lockPath, 'c');
requireBackup($mutex !== false && flock($mutex, LOCK_EX | LOCK_NB), 'журнал занят');
$statePath = $conf . '/.web-backup-state.json';
if (file_exists($statePath) || is_link($statePath)) $state = readBackupJson($statePath, $uid);
else {
    requireBackup($action === 'begin', 'нет журнала владельца копий');
    $state = ['schema'=>1, 'owner'=>bin2hex(random_bytes(16)), 'sequence'=>0];
}
requireBackup(($state['schema'] ?? null) === 1 && is_string($state['owner'] ?? null)
    && (bool)preg_match('/\A[a-f0-9]{32}\z/D', $state['owner'])
    && is_int($state['sequence'] ?? null) && $state['sequence'] >= 0 && $state['sequence'] < PHP_INT_MAX,
    'повреждён счётчик копий');
$receipt = $current . '/.fptn-backup.json';
if ($action === 'begin') {
    requireBackup(!file_exists($receipt) && !is_link($receipt), 'копия уже содержит журнал');
    $state['sequence']++;
    writeBackupJson($statePath, $state);
    writeBackupJson($receipt, ['schema'=>1, 'owner'=>$state['owner'], 'directory'=>basename($current),
        'operation'=>$operation, 'revision'=>$revision, 'sequence'=>$state['sequence'], 'state'=>'pending']);
    exit;
}
$record = readBackupJson($receipt, $uid);
requireBackup(($record['schema'] ?? null) === 1 && ($record['owner'] ?? null) === $state['owner']
    && ($record['operation'] ?? null) === $operation && ($record['revision'] ?? null) === $revision
    && ($record['directory'] ?? null) === basename($current) && ($record['state'] ?? null) === 'pending',
    'копия не принадлежит текущей установке');
if ($action === 'failed') {
    $record['state'] = 'failed'; writeBackupJson($receipt, $record); exit;
}
$installed = readBackupJson($conf . '/web-meta.json', $uid);
requireBackup(($installed['revision'] ?? null) === $revision, 'новая установка не подтверждена');
$record['state'] = 'success';
writeBackupJson($receipt, $record);
$protected = backupProtected($run, $conf, $uid);
$successful = [];
foreach (new DirectoryIterator($conf) as $entry) {
    if (!preg_match('/\Aweb-install-backup\.[A-Za-z0-9]{6}\z/D', $entry->getFilename())) continue;
    try {
        $path = backupPath($entry->getPathname(), $conf, $uid);
        $item = readBackupJson($path . '/.fptn-backup.json', $uid);
        if (($item['schema'] ?? null) !== 1 || ($item['owner'] ?? null) !== $state['owner']
            || ($item['directory'] ?? null) !== basename($path) || ($item['state'] ?? null) !== 'success'
            || !is_string($item['operation'] ?? null) || !preg_match('/\A[a-f0-9]{32}\z/D', $item['operation'])
            || !is_string($item['revision'] ?? null) || !preg_match('/\A[a-f0-9]{40}\z/D', $item['revision'])
            || !is_int($item['sequence'] ?? null) || $item['sequence'] < 1 || $item['sequence'] > $state['sequence']) continue;
    } catch (RuntimeException $error) { continue; } // Preserve unknown/legacy/damaged backups.
    requireBackup(!isset($successful[$item['sequence']]), 'дублируется номер копии');
    $successful[$item['sequence']] = $path;
}
krsort($successful, SORT_NUMERIC);
$removed = 0; $position = 0;
foreach ($successful as $path) {
    if (++$position <= 3 || isset($protected[$path])) continue;
    checkBackupMounts($path);
    $stat = privateStat($path, $uid, true); $count = 0;
    backupTreeCheck($path, $stat['dev'], $count);
    // Recheck the top directory immediately before removing its payload.
    requireBackup(privateStat($path, $uid, true)['ino'] === $stat['ino'], 'копия изменена во время очистки');
    removeBackupTree($path, true); $removed++;
}
echo "Резервные копии: удалено старых успешных — ", $removed, "; сохраняются три последних и защищённые копии.\n";
