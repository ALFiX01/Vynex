<div align="center">
  
# Vynex

</div>  

  <p align="center">
    <a href="https://github.com/ALFiX01/Vynex/releases/latest"><img src="https://img.shields.io/github/v/release/ALFiX01/Vynex?style=plastic" alt="GitHub Release"></a>
    <a href="https://github.com/ALFiX01/Vynex/stargazers"><img src="https://img.shields.io/github/stars/ALFiX01/Vynex?style=plastic" alt="GitHub Stars"></a>
    <a href="https://github.com/ALFiX01/Vynex/releases"><img src="https://img.shields.io/github/downloads/ALFiX01/Vynex/total?style=plastic" alt="GitHub Downloads"></a>
    <a href="https://github.com/ALFiX01/Vynex/releases"><img src="https://img.shields.io/github/downloads/ALFiX01/Vynex/VynexVPNClient.exe?style=plastic" alt="GitHub EXE Downloads"></a>
  </p>

Консольный VPN-клиент для Windows 10/11, который управляет `xray.exe`, импортирует сервера и подписки и запускает Xray-core в двух режимах:

- `PROXY`: локальные `SOCKS5` + `HTTP` inbounds и, при желании, системный proxy Windows.
- `TUN`: системный IPv4 tunneling через `tun` inbound Xray с автоматической установкой маршрутов Windows.

<p align="center">
    💖 <a href="https://pay.cloudtips.ru/p/b98d1870"><b>Поддержать разработчика</b></a>
</p>

## Возможности

- Автоматическая загрузка последнего `Xray-core` для Windows 64-bit через GitHub API.
- Автоматическая догрузка `wintun.dll` вместе с `xray.exe`, если он нужен для `TUN` режима.
- Автоматическая загрузка `geoip.dat`, `geosite.dat` и профилей маршрутизации из каталога `.database` репозитория Vynex.
- Полноценный self-update приложения через GitHub Releases: клиент сам находит свежий `VynexVPNClient.exe`, скачивает его, перезапускается и применяет обновление через внешний helper script.
- Единый быстрый импорт: клиент сам определяет `vless://`, `vmess://`, `trojan://`, `ss://`, `hy2://`, URL подписки и Base64/список ссылок.
- Импорт и обновление Base64-подписок.
- Генерация `config.json` для `PROXY` и `TUN` режима на базе одного `XrayConfigBuilder`.
- Общие routing profiles для обоих режимов подключения.
- Отдельный пункт `Компоненты` для ручного обновления `xray.exe`, `geoip.dat`, `geosite.dat` и профилей маршрутизации.
- Отдельный пункт `Настройки` для выбора режима подключения, системного proxy для `PROXY` режима и активного набора маршрутизации.
- Быстрый ручной сброс системного proxy Windows из `Настройки`.
- Фоновый запуск `xray.exe` без черного окна через `subprocess.CREATE_NO_WINDOW`.
- Корректная остановка Xray при отключении и выходе из приложения.
- Понятная диагностика ошибок запуска: отсутствие admin rights, отсутствие `wintun.dll`, несовместимая версия Xray, ошибки инициализации TUN и ошибки установки маршрутов.

## Режимы подключения

### PROXY

- Использует локальные `SOCKS5` и `HTTP` inbounds Xray на случайных портах текущей сессии.
- Может автоматически включать системный proxy Windows.
- Не требует запуска приложения от администратора.

### TUN

- Использует `tun` inbound в `xray.exe`.
- Поднимает интерфейс `VynexTun`, затем автоматически добавляет Windows-маршруты `0.0.0.0/1` и `128.0.0.0/1`.
- Использует активный routing profile для правил `direct/proxy/block`, а весь остальной IPv4-трафик отправляет через outbound `proxy`.
- Требует запуск приложения от имени администратора.

## Требования для TUN на Windows

- Windows 10/11.
- `Xray-core` с поддержкой `tun` inbound. Практически клиент ожидает версию не ниже `26.1.13`.
- Наличие `wintun.dll` рядом с `xray.exe` в `%LOCALAPPDATA%\VynexVPNClient\xray\`.
- Рабочий IPv4 default route у системы, чтобы клиент мог определить физический интерфейс для outbound Xray.

Если `TUN` не стартует, клиент показывает причину до или сразу после запуска Xray:

- нет прав администратора;
- отсутствует или поврежден `wintun.dll`;
- версия `xray.exe` слишком старая;
- Windows не подняла TUN интерфейс вовремя;
- Windows не приняла маршруты для TUN;
- другой VPN/TUN-драйвер конфликтует с маршрутизацией.

## Как использовать

1. Запустите приложение.
2. При первом старте клиент сам скачает `xray.exe`, если бинарник отсутствует.
3. Если серверов еще нет, на старте откроется экран быстрого импорта. Вставьте ссылку сервера, URL подписки или Base64/список ссылок.
4. При необходимости откройте `Компоненты` и вручную обновите `xray.exe`, `geoip.dat`, `geosite.dat` и профили маршрутизации.
5. В `Настройки` выберите режим подключения (`PROXY` или `TUN`) и нужный routing profile.
6. Если выбран `TUN`, перезапустите приложение через `Запуск от имени администратора`.
7. Выберите `Подключиться`, затем сервер.
8. Если доступен новый релиз клиента, выберите `Обновить приложение до ...`: приложение скачает новый exe, завершит текущую сессию и перезапустится.

## Диагностика

- Основной лог Xray: `%LOCALAPPDATA%\VynexVPNClient\logs\xray-core.log`
- Runtime-файлы: `%LOCALAPPDATA%\VynexVPNClient\xray\`
- Состояние и настройки: `%LOCALAPPDATA%\VynexVPNClient\data\`

Типовые причины ошибок и что проверить:

- `TUN режим требует запуска приложения от имени администратора`
  Запустите `VynexVPNClient.exe` с повышенными правами.
- `wintun.dll`
  Обновите `Xray-core` через `Компоненты`, проверьте, не удален ли DLL антивирусом.
- `не поддерживает TUN режим`
  Обновите `xray.exe` до свежей версии.
- `TUN интерфейс ... IPv4`
  Дождитесь полной инициализации сети или проверьте конфликт с другим VPN/TUN драйвером.
- `Не удалось добавить маршрут`
  Проверьте admin rights и убедитесь, что другой VPN-клиент не удерживает таблицу маршрутов.
- `health-check не прошел`
  Ядро стартовало, но сеть через выбранный сервер недоступна; попробуйте другой сервер или обновите компоненты.

Для `TUN` быстрый health-check теперь считается диагностикой, а не жестким условием старта:

- если `xray.exe`, TUN-интерфейс и маршруты подняты, но probe-URL не ответили вовремя, клиент оставит TUN активным и покажет предупреждение;
- после этого стоит вручную проверить нужные сайты или приложения, потому что probe-URL могут давать ложный негатив на отдельных серверах.

## Ограничения

- `TUN` в текущей реализации перехватывает IPv4. Активный системный IPv6 может обходить туннель.
- `PROXY` и `TUN` используют одно ядро `xray.exe`, но системный proxy применяется только в `PROXY`.
- Одновременная работа с другим VPN/TUN-клиентом может мешать поднятию интерфейса или установке маршрутов.
- Импорт серверов, подписки и хранение данных остаются обратно совместимыми; переключение режима не меняет формат сохраненных серверов.

## Примечания

- Данные приложения сохраняются в `%LOCALAPPDATA%\VynexVPNClient\`.
- Конфиги и состояние лежат в `%LOCALAPPDATA%\VynexVPNClient\data\`.
- Файлы Xray runtime (`xray.exe`, `wintun.dll`, `geoip.dat`, `geosite.dat`) лежат в `%LOCALAPPDATA%\VynexVPNClient\xray\`.
- Self-update staging-файлы и helper script лежат в `%LOCALAPPDATA%\VynexVPNClient\updates\`.
- Профили маршрутизации лежат по одному JSON-файлу в `%LOCALAPPDATA%\VynexVPNClient\data\routing_profiles\`.
- `geoip.dat` и `geosite.dat` скачиваются из каталога `.database`:
  - `https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geoip.dat`
  - `https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geosite.dat`
- Профили маршрутизации подтягиваются из:
  - `https://api.github.com/repos/ALFiX01/Vynex/contents/.database/routing_profiles`
- Если geo-файлы не удалось скачать, клиент покажет предупреждение при старте.
