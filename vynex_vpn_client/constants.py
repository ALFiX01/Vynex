from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Vynex VPN Client"
APP_VERSION = "0.9.0"
APP_RELEASES_API = "https://api.github.com/repos/ALFiX01/Vynex/releases/latest"
APP_RELEASES_PAGE = "https://github.com/ALFiX01/Vynex/releases/latest"
APP_DIR = Path(__file__).resolve().parent.parent
LOGO_FILE = APP_DIR / "logo.txt"
LEGACY_DATA_DIR = APP_DIR / "data"
LOCAL_APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA", APP_DIR))
APPDATA_DIR = LOCAL_APPDATA_DIR / "VynexVPNClient"
DATA_DIR = APPDATA_DIR / "data"
PROCESS_LOG_DIR = APPDATA_DIR / "logs"
RUNTIME_ARTIFACTS_DIR = APPDATA_DIR / "runtime"
AMNEZIAWG_RUNTIME_DIR = APPDATA_DIR / "amneziawg"
AMNEZIAWG_LEGACY_RUNTIME_DIR = APPDATA_DIR / "AmneziaWG"
ROUTING_PROFILES_DIR = DATA_DIR / "routing_profiles"
XRAY_RUNTIME_DIR = APPDATA_DIR / "xray"
APP_UPDATE_CACHE_FILE = DATA_DIR / "app_update.json"
APP_UPDATE_CHECK_TTL_SECONDS = 6 * 60 * 60
APP_UPDATE_ASSET_NAME = "VynexVPNClient.exe"
APP_UPDATES_DIR = APPDATA_DIR / "updates"
APP_UPDATE_HELPER_SCRIPT_NAME = "apply_app_update.cmd"
APP_UPDATE_BACKUP_BASENAME_SUFFIX = ".old"
APP_UPDATE_TEMP_SUFFIX = ".part"
APP_UPDATE_REQUEST_TIMEOUT_SECONDS = 20
APP_UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 60
APP_UPDATE_DOWNLOAD_CHUNK_SIZE = 1024 * 512
APP_UPDATE_HELPER_WAIT_SECONDS = 90
APP_UPDATE_HELPER_RETRY_COUNT = 20
DEFAULT_CONSOLE_COLUMNS = 138
DEFAULT_CONSOLE_LINES = 41
XRAY_EXECUTABLE = XRAY_RUNTIME_DIR / "xray.exe"
AMNEZIAWG_EXECUTABLE = AMNEZIAWG_RUNTIME_DIR / "amneziawg.exe"
AMNEZIAWG_EXECUTABLE_FALLBACK = AMNEZIAWG_RUNTIME_DIR / "awg.exe"
AMNEZIAWG_WINTUN_DLL = AMNEZIAWG_RUNTIME_DIR / "wintun.dll"
SINGBOX_EXECUTABLE = XRAY_RUNTIME_DIR / "sing-box.exe"
WINTUN_DLL = XRAY_RUNTIME_DIR / "wintun.dll"
XRAY_PROCESS_LOG = PROCESS_LOG_DIR / "xray-core.log"
AMNEZIAWG_PROCESS_LOG = PROCESS_LOG_DIR / "amneziawg.log"
SINGBOX_PROCESS_LOG = PROCESS_LOG_DIR / "sing-box.log"
GEOIP_PATH = XRAY_RUNTIME_DIR / "geoip.dat"
GEOSITE_PATH = XRAY_RUNTIME_DIR / "geosite.dat"
SERVERS_FILE = DATA_DIR / "servers.json"
SUBSCRIPTIONS_FILE = DATA_DIR / "subscriptions.json"
RUNTIME_STATE_FILE = DATA_DIR / "runtime_state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
XRAY_ARCHIVE_PATH = DATA_DIR / "xray-core.zip"
SINGBOX_ARCHIVE_PATH = DATA_DIR / "sing-box.zip"
WINTUN_ARCHIVE_PATH = DATA_DIR / "wintun.zip"
XRAY_BUNDLED_FILES = ("xray.exe", "wintun.dll", "geoip.dat", "geosite.dat")
AMNEZIAWG_BUNDLED_FILES = ("amneziawg.exe", "awg.exe", "wintun.dll")
LOCAL_PROXY_HOST = "127.0.0.1"
XRAY_RELEASES_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
SINGBOX_RELEASES_API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
GEOIP_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geoip.dat"
GEOSITE_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geosite.dat"
AMNEZIAWG_EXECUTABLE_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/amnezia-wg/amneziawg.exe"
AMNEZIAWG_EXECUTABLE_FALLBACK_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/amnezia-wg/awg.exe"
WINTUN_DOWNLOAD_URL = "https://www.wintun.net/builds/wintun-0.14.1.zip"
ROUTING_PROFILES_REPO_API = "https://api.github.com/repos/ALFiX01/Vynex/contents/.database/routing_profiles"
ROUTING_PROFILES_RAW_BASE = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/routing_profiles"
HEALTHCHECK_URLS = (
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://clients3.google.com/generate_204",
    "http://www.msftconnecttest.com/connecttest.txt",
)
HEALTHCHECK_ATTEMPTS = 3
HEALTHCHECK_TIMEOUT = 4
TCP_PING_TIMEOUT_SECONDS = 1.5
TCP_PING_MAX_CONCURRENCY = 20
SUBSCRIPTION_TITLE_BY_HOST = {
    "lovecat.mooo.com": "GoodbyeZapretVPN",
}
