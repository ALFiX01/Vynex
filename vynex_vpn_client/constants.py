from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Vynex VPN Client"
APP_VERSION = "0.1.0"
APP_DIR = Path(__file__).resolve().parent.parent
LOGO_FILE = APP_DIR / "logo.txt"
LEGACY_DATA_DIR = APP_DIR / "data"
LOCAL_APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA", APP_DIR))
APPDATA_DIR = LOCAL_APPDATA_DIR / "VynexVPNClient"
DATA_DIR = APPDATA_DIR / "data"
ROUTING_PROFILES_DIR = DATA_DIR / "routing_profiles"
XRAY_RUNTIME_DIR = APPDATA_DIR / "xray"
XRAY_EXECUTABLE = XRAY_RUNTIME_DIR / "xray.exe"
GEOIP_PATH = XRAY_RUNTIME_DIR / "geoip.dat"
GEOSITE_PATH = XRAY_RUNTIME_DIR / "geosite.dat"
XRAY_CONFIG = DATA_DIR / "config.json"
XRAY_STDOUT_LOG = DATA_DIR / "xray.log"
SERVERS_FILE = DATA_DIR / "servers.json"
SUBSCRIPTIONS_FILE = DATA_DIR / "subscriptions.json"
RUNTIME_STATE_FILE = DATA_DIR / "runtime_state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
XRAY_ARCHIVE_PATH = DATA_DIR / "xray-core.zip"
XRAY_BUNDLED_FILES = ("xray.exe", "geoip.dat", "geosite.dat")
XRAY_RELEASES_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
GEOIP_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geoip.dat"
GEOSITE_DOWNLOAD_URL = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/geosite.dat"
ROUTING_PROFILES_REPO_API = "https://api.github.com/repos/ALFiX01/Vynex/contents/.database/routing_profiles"
ROUTING_PROFILES_RAW_BASE = "https://raw.githubusercontent.com/ALFiX01/Vynex/main/.database/routing_profiles"
PROXY_SOCKS_PORT = 1080
PROXY_HTTP_PORT = 1081
HEALTHCHECK_URLS = (
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://clients3.google.com/generate_204",
    "http://www.msftconnecttest.com/connecttest.txt",
)
HEALTHCHECK_ATTEMPTS = 5
HEALTHCHECK_TIMEOUT = 6
SUBSCRIPTION_TITLE_BY_HOST = {
    "lovecat.mooo.com": "GoodbyeZapretVPN",
}
