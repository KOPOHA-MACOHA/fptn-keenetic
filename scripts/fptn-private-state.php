<?php
// Private operation scratch space and bounded cleanup; never served by lighttpd.
declare(strict_types=1);
function privateUid(): int { return (int)fileowner('/proc/'.getmypid()); }
function privateDirectory(string $path, bool $create = false): void {
    if ($create && !file_exists($path) && !is_link($path)) {
        if (!mkdir($path,0700)) throw new RuntimeException('Не удалось создать временный каталог.');
    }
    clearstatcache(true,$path);$s=@lstat($path);
    if (!$s || ($s['mode']&0170000)!==0040000 || $s['uid']!==privateUid() || ($s['mode']&0022)!==0)
        throw new RuntimeException('Не подтверждено владение каталогом FPTN.');
}
function privateBase(): string {
    global $root;
    // The alternate prefix is used only by isolated filesystem tests.
    $parent=$root==='/opt'?'/tmp':$root.'/tmp';
    if($root!=='/opt')privateDirectory($parent,true);
    return $parent.'/fptn-private-'.hash('sha256',$root);
}
function privatePath(string $id, bool $create = true): string {
    if (!preg_match('/\A[a-f0-9]{32}\z/D',$id)) throw new RuntimeException('Некорректная операция.');
    $base=privateBase();
    if (!$create && !file_exists($base) && !is_link($base)) return $base.'/'.$id;
    privateDirectory($base,$create);$path=$base.'/'.$id;
    if (!$create && !file_exists($path) && !is_link($path)) return $path;
    privateDirectory($path,$create);return $path;
}
function privateWrite(string $path,string $data): void {
    $new=$path.'.new';
    if (is_link($new)) throw new RuntimeException('Неверный временный файл.');
    if (file_put_contents($new,$data,LOCK_EX)===false || !chmod($new,0600) || !rename($new,$path))
        throw new RuntimeException('Не удалось сохранить временные данные.');
}
function privateRead(string $path): ?string {
    clearstatcache(true,$path);$s=@lstat($path);
    if (!$s) return null;
    if (($s['mode']&0170000)!==0100000 || $s['uid']!==privateUid() || ($s['mode']&0022)!==0 || $s['size']>300001)
        throw new RuntimeException('Неверный временный файл FPTN.');
    $data=file_get_contents($path);if ($data===false) throw new RuntimeException('Не удалось прочитать временные данные.');
    return $data;
}
function privateCheckTree(string $path,int $device,int &$count,int $depth=0): void {
    if (++$count>20000 || $depth>64) throw new RuntimeException('Слишком большой каталог FPTN.');
    $s=@lstat($path);if (!$s || $s['dev']!==$device || !in_array($s['mode']&0170000,[0040000,0100000,0120000],true))
        throw new RuntimeException('Неожиданная файловая система или тип файла.');
    if (($s['mode']&0170000)===0040000) foreach(new DirectoryIterator($path) as $entry)
        if (!$entry->isDot()) privateCheckTree($entry->getPathname(),$device,$count,$depth+1);
}
function privateCheckRemoval(string $path): void {
    privateDirectory($path);$canonical=realpath($path);
    $mounts=@file('/proc/self/mountinfo',FILE_IGNORE_NEW_LINES);
    if (!$mounts) throw new RuntimeException('Не удалось проверить точки монтирования.');
    foreach($mounts as $line) {
        $fields=explode(' ',$line);if(count($fields)<6)throw new RuntimeException('Не удалось прочитать точки монтирования.');
        $mount=preg_replace_callback('/\\\\([0-7]{3})/',function($m){return chr(octdec($m[1]));},$fields[4]);
        if($mount===$canonical || strpos($mount,$canonical.'/')===0) throw new RuntimeException('В каталоге FPTN есть точка монтирования.');
    }
    $s=lstat($path);$count=0;privateCheckTree($path,$s['dev'],$count);
}
function privateErase(string $path): void {
    $s=@lstat($path);if(!$s)return;
    if(($s['mode']&0170000)!==0040000){if(!unlink($path))throw new RuntimeException('Не удалось удалить файл FPTN.');return;}
    foreach(new DirectoryIterator($path) as $entry)if(!$entry->isDot())privateErase($entry->getPathname());
    if(!rmdir($path))throw new RuntimeException('Не удалось удалить каталог FPTN.');
}
function privateClear(string $id): void {
    $path=privatePath($id,false);
    if(file_exists($path)){privateCheckRemoval($path);privateErase($path);}
    @rmdir(privateBase());
}
function privateRedactRequest(array $body): array {
    return array_intersect_key($body,array_flip(['action','server','connection','policy','fallback','port']));
}
function privateMigrateOperation(string $directory,string $id): void {
    $path=privatePath($id);
    $legacy=privateRead($directory.'/old-token');
    if($legacy!==null){privateWrite($path.'/old-token',$legacy);touch($directory.'/old-token-present');unlink($directory.'/old-token');}
    $request=privateRead($directory.'/request.json');
    if($request!==null){
        $body=json_decode($request,true);
        if(is_array($body)){
            if(is_string($body['token']??null))privateWrite($path.'/request-token',$body['token']);
            privateWrite($directory.'/request.json',json_encode(privateRedactRequest($body),JSON_THROW_ON_ERROR));
        }
    }
}
function privateScrubTree(string $path): void {
    foreach(new DirectoryIterator($path) as $entry){
        if($entry->isDot())continue;
        $file=$entry->getPathname();$name=$entry->getFilename();
        if(in_array($name,['token','token.new','old-token','web-auth.json','web-auth.json.new','old-web-auth.json','web-key','failed-request.json'],true)){
            privateErase($file);continue;
        }
        if($entry->isLink())continue;
        if($entry->isDir()){privateScrubTree($file);continue;}
        if($name==='request.json'){
            $data=privateRead($file);$body=$data===null?null:json_decode($data,true);
            if(is_array($body))privateWrite($file,json_encode(privateRedactRequest($body),JSON_THROW_ON_ERROR));
            else unlink($file);
        }
    }
}
function privateScrubHistory(): void {
    global $conf;
    if(!is_dir($conf))return;
    privateDirectory($conf);
    foreach(new DirectoryIterator($conf) as $entry){
        if($entry->isDot())continue;$name=$entry->getFilename();
        if($name!=='web-recovery' && !preg_match('/\A(?:web-install|uninstall)-backup\.[A-Za-z0-9]{6}\z/D',$name))continue;
        $path=$entry->getPathname();privateCheckRemoval($path);privateScrubTree($path);
    }
}
