# Vynex VPN Client

Консольный CLI/TUI клиент для Windows 10/11, который управляет `xray.exe`, импортирует сервера и подписки, а также запускает Xray-core в режиме `Proxy`.

## Структура проекта

```text
vynex_vpn_client/
├── vynex_vpn_client/
│   ├── app.py
│   ├── config_builder.py
│   ├── constants.py
│   ├── core.py
│   ├── healthcheck.py
│   ├── models.py
│   ├── parsers.py
│   ├── process_manager.py
│   ├── storage.py
│   ├── subscriptions.py
│   ├── system_proxy.py
│   └── utils.py
├── main.py
├── README.md
├── build.ps1
├── requirements.txt
└── vynex_vpn_client.spec
```

## Возможности

- Автоматическая загрузка последнего `Xray-core` для Windows 64-bit через GitHub API.
- Автоматическая загрузка `geoip.dat`, `geosite.dat` и профилей маршрутизации из каталога `.database` репозитория Vynex VPN.
- Импорт одиночных ссылок `vless://`, `vmess://`, `ss://`.
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
3. Добавьте сервер через ссылку или импортируйте подписку.
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
