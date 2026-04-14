# Vynex VPN Client
  <p align="center">
    <a href="https://github.com/ALFiX01/Vynex/releases/latest"><img src="https://img.shields.io/github/v/release/ALFiX01/Vynex?style=plastic" alt="GitHub Release"></a>
    <a href="https://github.com/ALFiX01/Vynex/stargazers"><img src="https://img.shields.io/github/stars/ALFiX01/Vynex?style=plastic" alt="GitHub Stars"></a>
    <a href="https://github.com/ALFiX01/Vynex/releases"><img src="https://img.shields.io/github/downloads/ALFiX01/Vynex/total?style=plastic" alt="GitHub Downloads"></a>
    <a href="https://github.com/ALFiX01/Vynex/releases"><img src="https://img.shields.io/github/downloads/ALFiX01/Vynex/VynexVPNClient.exe?style=plastic" alt="GitHub EXE Downloads"></a>
  </p>

Консольный клиент для Windows 10/11, который управляет `xray.exe`, импортирует сервера и подписки, а также запускает Xray-core в режиме `Proxy`.

<p align="center">
    💖 <a href="https://pay.cloudtips.ru/p/b98d1870"><b>Поддержать разработчика</b></a>
</p>

## Возможности

- Автоматическая загрузка последнего `Xray-core` для Windows 64-bit через GitHub API.
- Автоматическая загрузка `geoip.dat`, `geosite.dat` и профилей маршрутизации из каталога `.database` репозитория Vynex VPN.
- Единый быстрый импорт: клиент сам определяет `vless://`, `vmess://`, `ss://`, URL подписки и Base64/список ссылок.
- Импорт и обновление Base64-подписок.
- Генерация `config.json` для `Proxy` режима с локальными `SOCKS5` и `HTTP` inbounds.
- На старте недостающие `xray.exe`, `geoip.dat`, `geosite.dat` подтягиваются автоматически, а уже скачанные файлы повторно не скачиваются.
- Отдельный пункт `Компоненты` для ручного обновления `xray.exe`, `geoip.dat`, `geosite.dat`, профилей маршрутизации или всех компонентов сразу.
- Отдельный пункт `Настройки` для выбора локальных `SOCKS5`/`HTTP` портов, режима системного proxy и активного набора маршрутизации.
- Быстрый ручной сброс системного proxy Windows из `Настройки`.
- Фоновый запуск `xray.exe` без черного окна через `subprocess.CREATE_NO_WINDOW`.
- Корректная остановка Xray при отключении и выходе из приложения.

## Как использовать

1. Запустите приложение.
2. При первом старте клиент сам скачает `xray.exe`, если бинарник отсутствует.
3. Если серверов еще нет, на старте откроется экран быстрого импорта. Вставьте ссылку сервера, URL подписки или Base64/список ссылок.
4. При необходимости откройте `Компоненты` и вручную обновите `xray.exe`, `geoip.dat`, `geosite.dat` и профили маршрутизации.
5. При необходимости откройте `Настройки` и задайте локальные proxy-порты, системный proxy и активный набор маршрутизации.
6. Выберите `Подключиться`, затем сервер.

## Примечания

- Данные приложения сохраняются в `%LOCALAPPDATA%\VynexVPNClient\`.
- Конфиги и состояние лежат в `%LOCALAPPDATA%\VynexVPNClient\data\`.
- Файлы Xray runtime (`xray.exe`, `geoip.dat`, `geosite.dat`) лежат в `%LOCALAPPDATA%\VynexVPNClient\xray\`.
- Профили маршрутизации лежат по одному JSON-файлу в `%LOCALAPPDATA%\VynexVPNClient\data\routing_profiles\`.
- `geoip.dat` и `geosite.dat` скачиваются из каталога `.database`:
  - `https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geoip.dat`
  - `https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geosite.dat`
- Профили маршрутизации подтягиваются из:
  - `https://api.github.com/repos/ALFiX01/Vynex/contents/.database/routing_profiles`
- Если geo-файлы не удалось скачать, клиент покажет предупреждение при старте.
