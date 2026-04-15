from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Vynex VPN Client"
APP_VERSION = "0.6.1"
APP_RELEASES_API = "https://api.github.com/repos/ALFiX01/Vynex/releases/latest"
APP_RELEASES_PAGE = "https://github.com/ALFiX01/Vynex/releases/latest"
APP_DIR = Path(__file__).resolve().parent.parent
LOGO_FILE = APP_DIR / "logo.txt"
LEGACY_DATA_DIR = APP_DIR / "data"
LOCAL_APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA", APP_DIR))
APPDATA_DIR = LOCAL_APPDATA_DIR / "VynexVPNClient"
DATA_DIR = APPDATA_DIR / "data"
PROCESS_LOG_DIR = APPDATA_DIR / "logs"
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
XRAY_EXECUTABLE = XRAY_RUNTIME_DIR / "xray.exe"
SINGBOX_EXECUTABLE = XRAY_RUNTIME_DIR / "sing-box.exe"
XRAY_PROCESS_LOG = PROCESS_LOG_DIR / "xray-core.log"
SINGBOX_PROCESS_LOG = PROCESS_LOG_DIR / "sing-box.log"
GEOIP_PATH = XRAY_RUNTIME_DIR / "geoip.dat"
GEOSITE_PATH = XRAY_RUNTIME_DIR / "geosite.dat"
SERVERS_FILE = DATA_DIR / "servers.json"
SUBSCRIPTIONS_FILE = DATA_DIR / "subscriptions.json"
RUNTIME_STATE_FILE = DATA_DIR / "runtime_state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
XRAY_ARCHIVE_PATH = DATA_DIR / "xray-core.zip"
SINGBOX_ARCHIVE_PATH = DATA_DIR / "sing-box.zip"
XRAY_BUNDLED_FILES = ("xray.exe", "geoip.dat", "geosite.dat")
LOCAL_PROXY_HOST = "127.0.0.1"
XRAY_RELEASES_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
SINGBOX_RELEASES_API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
GEOIP_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geoip.dat"
GEOSITE_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geosite.dat"
ROUTING_PROFILES_REPO_API = "https://api.github.com/repos/ALFiX01/Vynex/contents/.database/routing_profiles"
ROUTING_PROFILES_RAW_BASE = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/routing_profiles"
HEALTHCHECK_URLS = (
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://clients3.google.com/generate_204",
    "http://www.msftconnecttest.com/connecttest.txt",
)
HEALTHCHECK_ATTEMPTS = 3
HEALTHCHECK_TIMEOUT = 4
SUBSCRIPTION_TITLE_BY_HOST = {
    "lovecat.mooo.com": "GoodbyeZapretVPN",
}
