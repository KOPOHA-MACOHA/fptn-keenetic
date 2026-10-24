# FPTN для Keenetic / Entware

Экспериментальный перенос [FPTN](https://github.com/fptn-project/fptn).
Первая цель — **Hopper KN-3810 EAEU**, EN7528DU / MIPS 1004Kc,
MIPS32r2 little-endian, o32, soft-float; KeeneticOS Linux 4.9.

**Статус: инфраструктура сборки; работа VPN на Keenetic пока не подтверждена.**
Этот репозиторий не является официальным выпуском FPTN или Keenetic.

## Почему статическая сборка

На проверяемом устройстве Entware 2025.05: glibc 2.27, GCC runtime 8.4,
около 68 МиБ доступной памяти, без swap; `/dev/net/tun` присутствует.
FPTN требует C++20. Прототип использует новый компилятор из OpenWrt SDK,
но **не устанавливает пакеты OpenWrt**: выдаёт отдельные статические ELF с musl.
Они не заменяют `/opt/lib` и не используют системную `libstdc++`.
Базовая совместимость подтверждена на реальном Hopper KN-3810 EAEU:
2026-10-24 пользователь запустил probe из выпуска `probe-4ed5766` и получил
`probe: PASS`. Прошли потоки, atomic64, исключения и корутины C++20.
Работа VPN, TLS, DNS, маршрутизация и нагрузка этим тестом не проверялись.

## Что собирается

В [Actions](../../actions) запускается workflow `Build prototype`:

1. `probe` — небольшой статический C++20 бинарник: потоки, атомики,
   исключения, корутины. Проверяется ABI ELF и запуск под QEMU.
2. `client` — FPTN CLI с тем же SDK; проверяется ELF и `--help` под QEMU.

Артефакты: `keenetic-mipsel-probe` и `keenetic-mipsel-client`.
Архивы содержат SHA256 и сведения об исходниках.
**В клиенте стоит безусловный запрет подключения после разбора аргументов.**
`--help` и `--version` доступны; реальное VPN-подключение пока не реализовано
для Keenetic и намеренно заблокировано. Ни службы, ни автозапуска нет.

Не запускайте сторонний исходный CLI с токеном: в нём есть изменение default
route, firewall и DNS, не адаптированное к Keenetic. Существование TUN ещё
не доказывает совместимость маршрутизации или приемлемую скорость.

## Первый тест с телефона

Вставьте блок в Termius. Он скачивает только диагностический бинарник
из [предварительного выпуска](https://github.com/KOPOHA-MACOHA/fptn-keenetic/releases/tag/probe-4ed5766), проверяет SHA256 и запускает его:

```sh
(
set -eu
mkdir -p /opt/tmp
FPTN_TEST_DIR=$(mktemp -d /opt/tmp/fptn-probe.XXXXXX)
cd "$FPTN_TEST_DIR"
FPTN_URL="https://github.com/KOPOHA-MACOHA/fptn-keenetic/releases/download/probe-4ed5766/fptn-platform-probe"
if command -v curl >/dev/null 2>&1; then
  curl -fL --connect-timeout 15 --max-time 120 "$FPTN_URL" -o fptn-platform-probe
else
  wget -T 60 -O fptn-platform-probe "$FPTN_URL"
fi
echo "832674ff7859ddca7832971ac0acb41e711a173b4e88d84742333eb346c4dec2  fptn-platform-probe" | sha256sum -c -
chmod 700 fptn-platform-probe
./fptn-platform-probe
)
```

Программа не открывает TUN, не подключается к серверу и не меняет настройки.
Ожидается `probe: PASS`. Она подтверждает только базовую совместимость ABI,
а не скорость, стабильность VPN, TLS или работу сетевого стека.

## Сборка на компьютере

Требуется Docker BuildKit на Linux x86_64:

```sh
docker build --target probe-export --output type=local,dest=out/probe .
docker build --target client-export --output type=local,dest=out/client .
```

При работе через корпоративный HTTPS proxy можно передать CA:
`--secret id=proxy_ca,src=/path/to/combined-ca-bundle.pem`.
Сертификат используется только в шагах сборки и не включается в артефакты.

## Обновления и поддержка

`sources.lock.json` фиксирует FPTN и четыре зависимости, которые оригинал
скачивает с плавающих веток. `scripts/prepare-source.py` применяет небольшие
проверяемые преобразования и завершается ошибкой при изменении ожидаемой структуры.
Оригинальные исходники не копируются в этот репозиторий.

Обновление: изменить коммиты в lock-файле, запустить Actions, разобрать ошибки,
проверить на устройстве и только потом выпускать версию. Автоматической установки
нового upstream на роутер нет. SDK tag и полный транзитивный граф Conan пока не
зафиксированы digest/lockfile: это воспроизводимая процедура, но ещё не гарантия
побитово одинакового результата. Их фиксация — следующий этап после первой сборки.

## Дальше

- Подтвердить запуск на Hopper и потребление ресурсов.
- Адаптировать управление маршрутизацией к Keenetic, отдельно от протокола FPTN.
- Проверить одно тестовое устройство, восстановление маршрутов и обрыв связи.
- Подготовить Entware `.ipk`, управляемую службу и ограничение перезапусков.
- Добавлять архитектуры отдельными профилями; не объявлять поддержку без тестов.

В этой версии нет конфигурации сервера и секретов. Токены VPN не публикуйте в
репозитории, issues или логах Actions.
