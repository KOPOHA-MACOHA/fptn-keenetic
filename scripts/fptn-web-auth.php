<?php
// Standalone local helper. The web server exposes api.cgi, never this file.
// Works with PHP CGI/CLI, uses core functions only, and never changes Entware users.
declare(strict_types=1);
umask(0077);
$root = getenv('FPTN_PREFIX') ?: '/opt';
$conf = $root . '/etc/fptn';
$run = $root . '/var/run/fptn-web';
$credentials = $conf . '/web-auth.json';
$action = getenv('FPTN_AUTH_ACTION') ?: 'info';
function reply(array $data): void { echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), "\n"; }
function failAuth(string $message): void {
    global $action;
    if ($action === 'verify' || $action === 'logout') fwrite(STDERR, $message . "\n");
    else reply(['error' => $message]);
    exit(1);
}
set_exception_handler(function ($error): void { failAuth('Не удалось проверить вход. Проверьте настройки панели.'); });
function config(): ?array {
    global $credentials;
    if (!file_exists($credentials)) return null;
    $data = json_decode((string)file_get_contents($credentials), true);
    if (!is_array($data) || !is_string($data['username'] ?? null)) failAuth('Повреждены настройки входа. Выполните fptnctl web-reset-login.');
    $mode = $data['mode'] ?? 'panel'; // Accept existing panel 0.5 credentials.
    if (!in_array($mode, ['panel', 'entware'], true) || ($mode === 'panel' && !is_string($data['hash'] ?? null))) failAuth('Некорректные настройки входа.');
    $data['mode'] = $mode;
    return $data;
}
function rootHash(): ?string {
    global $root;
    // Same source and crypt verification as nfqws-keenetic-web; never /etc/shadow.
    $path = is_file($root . '/etc/shadow') ? $root . '/etc/shadow' : $root . '/etc/passwd';
    $lines = @file($path, FILE_IGNORE_NEW_LINES);
    if ($lines === false) return null;
    foreach ($lines as $line) {
        $parts = explode(':', $line);
        if (($parts[0] ?? '') !== 'root') continue;
        $hash = $parts[1] ?? '';
        if ($hash === '' || $hash === 'x' || $hash[0] === '!' || $hash[0] === '*') return null;
        return $hash;
    }
    return null;
}
function fingerprint(array $data): string {
    global $credentials;
    $hash = hash_file('sha256', $credentials);
    return $data['mode'] === 'entware' ? hash('sha256', $hash . ':' . (rootHash() ?? 'locked')) : $hash;
}
function input(): array {
    $text = file_get_contents('php://stdin', false, null, 0, 4097);
    if ($text === false || strlen($text) > 4096) failAuth('Слишком большой запрос.');
    $data = json_decode($text, true);
    if (!is_array($data)) failAuth('Некорректный запрос.');
    return $data;
}
function password(array $body): string {
    $password = $body['password'] ?? null;
    if (!is_string($password) || $password === '' || strlen($password) > 256 || strpos($password, "\0") !== false) failAuth('Введите пароль.');
    return $password;
}
function rootPassword(string $password): bool {
    $hash = rootHash();
    if ($hash === null) return false;
    $actual = crypt($password, $hash);
    return strlen($actual) > 1 && hash_equals($hash, $actual);
}
function makeSession(array $data): array {
    global $run;
    $folder = $run . '/sessions';
    if (!is_dir($folder) && !mkdir($folder, 0700, true) && !is_dir($folder)) failAuth('Не удалось создать сессию.');
    $now = time(); $count = 0;
    foreach (glob($folder . '/*') ?: [] as $file) {
        $expiry = (int)explode(' ', trim((string)file_get_contents($file)))[0];
        if ($expiry <= $now) unlink($file); else $count++;
    }
    if ($count >= 32) failAuth('Слишком много сессий. Выйдите из другой вкладки.');
    $token = bin2hex(random_bytes(32));
    $record = ($now + 43200) . ' ' . fingerprint($data) . "\n";
    if (file_put_contents($folder . '/' . hash('sha256', $token), $record, LOCK_EX) === false) failAuth('Не удалось сохранить сессию.');
    return ['key' => $token, 'expiresIn' => 43200];
}
if (strpos($action, 'op-') === 0) {
    $operations=__DIR__.'/web-operations.php';
    if (!is_file($operations)) $operations=__DIR__.'/fptn-web-operations.php';
    require $operations; exit;
}
if ($action === 'account') {
    $data=config();if ($data===null) failAuth('Вход не настроен.');
    reply(['mode'=>$data['mode'],'username'=>$data['username'],'rootAvailable'=>rootHash()!==null]);exit;
}
if ($action === 'info') {
    if (!function_exists('password_hash') || !function_exists('crypt') || !function_exists('random_bytes')) failAuth('PHP не поддерживает проверку паролей.');
    $data = config();
    reply(['configured' => $data !== null, 'mode' => $data['mode'] ?? null, 'rootAvailable' => rootHash() !== null]);
    exit;
}
if ($action === 'verify' || $action === 'logout') {
    $token = trim((string)file_get_contents('php://stdin', false, null, 0, 129));
    if (!preg_match('/\A[a-f0-9]{64}\z/D', $token)) exit(1);
    $file = $run . '/sessions/' . hash('sha256', $token);
    $data = config();
    if ($data === null || !is_file($file)) exit(1);
    $parts = explode(' ', trim((string)file_get_contents($file)), 2);
    if ((int)$parts[0] <= time() || !hash_equals(fingerprint($data), $parts[1] ?? '')) { @unlink($file); exit(1); }
    if ($action === 'logout') @unlink($file);
    exit;
}
if (!is_dir($run) && !mkdir($run, 0700, true) && !is_dir($run)) failAuth('Не удалось создать каталог состояния.');
chmod($run, 0700);
// Kernel flock is released even when PHP is killed: no stale auth lock after a crash.
$lock = fopen($run . '/auth.lock', 'c');
if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) failAuth('Вход уже проверяется. Повторите через несколько секунд.');
if ($action === 'reset') {
    if (file_exists($credentials)) unlink($credentials);
    foreach (glob($run . '/sessions/*') ?: [] as $file) unlink($file);
    @unlink($run . '/auth-delay'); @unlink($run . '/auth-failures');
    reply(['ok' => true, 'message' => 'Откройте панель: создание логина и пароля снова доступно.']); exit;
}
if (!in_array($action, ['login', 'setup', 'change'], true)) failAuth('Неизвестное действие.');
$now = time();
$until = (int)@file_get_contents($run . '/auth-delay');
if ($now < $until) failAuth('Слишком много попыток. Подождите 30 секунд.');
$body = input(); $data = config();
if ($action === 'change') {
    if ($data === null) failAuth('Вход не настроен.');
    $current=password(['password'=>$body['currentPassword']??null]);
    $valid=$data['mode']==='entware' ? rootPassword($current) : password_verify($current,$data['hash']);
    if (!$valid) {
        $count=(int)@file_get_contents($run.'/auth-failures')+1;
        file_put_contents($run.'/auth-failures',(string)$count);
        if($count>=5){file_put_contents($run.'/auth-delay',(string)($now+30));@unlink($run.'/auth-failures');}
        failAuth('Неверный текущий пароль.');
    }
    if (($body['mode']??'panel')==='entware') $body['password']=$body['rootPassword']??($data['mode']==='entware'?$current:'');
}
if ($action === 'setup' || $action === 'change') {
    if ($action === 'setup' && $data !== null) failAuth('Учётная запись уже настроена. Войдите по логину и паролю.');
    $mode = $body['mode'] ?? 'panel';
    $pass = password($body);
    if ($mode === 'entware') {
        if (!rootPassword($pass)) {
            $count = (int)@file_get_contents($run . '/auth-failures') + 1;
            file_put_contents($run . '/auth-failures', (string)$count);
            if ($count >= 5) { file_put_contents($run . '/auth-delay', (string)($now + 30)); @unlink($run . '/auth-failures'); }
            failAuth('Неверный пароль root Entware или вход root недоступен.');
        }
        $data = ['mode' => 'entware', 'username' => 'root'];
    } elseif ($mode === 'panel') {
        $username = $body['username'] ?? '';
        if (!is_string($username) || !preg_match('/\A[a-zA-Z0-9_.-]{1,64}\z/D', $username)) failAuth('Укажите логин: латинские буквы, цифры, точка, дефис или подчёркивание.');
        if (strlen($pass) > 72) failAuth('Пароль отдельной учётной записи: не более 72 байт.');
        if (!is_string($body['confirm'] ?? null) || !hash_equals($pass, $body['confirm'])) failAuth('Пароли не совпадают.');
        $data = ['mode' => 'panel', 'username' => $username, 'hash' => password_hash($pass, PASSWORD_BCRYPT, ['cost' => 10])];
    } else failAuth('Неизвестный способ входа.');
    if (!is_dir($conf) && !mkdir($conf, 0700, true) && !is_dir($conf)) failAuth('Не удалось создать каталог настроек.');
    $json = json_encode($data, JSON_UNESCAPED_UNICODE);
    $temp = $credentials . '.new';
    if (file_put_contents($temp, $json . "\n", LOCK_EX) === false || !chmod($temp, 0600) || !rename($temp, $credentials)) failAuth('Не удалось сохранить учётную запись.');
    @unlink($run . '/auth-delay'); @unlink($run . '/auth-failures');
    // A newly created panel account is followed by the normal login screen.
    // Existing Entware root credentials can enter immediately after verification.
    if ($action === 'change') {
        foreach(glob($run.'/sessions/*')?:[] as $file) unlink($file);
        reply(['ok'=>true,'message'=>'Настройки входа сохранены. Войдите снова.']);exit;
    }
    reply($mode === 'entware' ? makeSession($data) : ['ok' => true, 'configured' => true, 'mode' => 'panel']); exit;
}
if ($data === null) failAuth('Сначала настройте вход в панели.');
$pass = password($body); $username = $body['username'] ?? '';
$stamp = fingerprint($data);
$valid = $data['mode'] === 'entware' ? rootPassword($pass) : password_verify($pass, $data['hash']);
if (!is_string($username) || $username !== $data['username'] || !$valid) {
    $count = (int)@file_get_contents($run . '/auth-failures') + 1;
    file_put_contents($run . '/auth-failures', (string)$count);
    if ($count >= 5) { file_put_contents($run . '/auth-delay', (string)($now + 30)); @unlink($run . '/auth-failures'); }
    failAuth('Неверный логин или пароль.');
}
if (!hash_equals($stamp, fingerprint(config() ?? []))) failAuth('Настройки входа изменились. Повторите вход.');
@unlink($run . '/auth-delay'); @unlink($run . '/auth-failures');
reply(makeSession($data));
