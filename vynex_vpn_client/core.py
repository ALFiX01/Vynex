from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import requests

from .constants import (
    AMNEZIAWG_BUNDLED_FILES,
    AMNEZIAWG_EXECUTABLE,
    AMNEZIAWG_EXECUTABLE_DOWNLOAD_URL,
    AMNEZIAWG_EXECUTABLE_FALLBACK,
    AMNEZIAWG_EXECUTABLE_FALLBACK_DOWNLOAD_URL,
    AMNEZIAWG_RUNTIME_DIR,
    AMNEZIAWG_WINTUN_DLL,
    APP_DIR,
    DATA_DIR,
    GEOIP_DOWNLOAD_URL,
    GEOIP_PATH,
    GEOSITE_DOWNLOAD_URL,
    GEOSITE_PATH,
    SINGBOX_ARCHIVE_PATH,
    SINGBOX_EXECUTABLE,
    SINGBOX_RELEASES_API,
    WINTUN_ARCHIVE_PATH,
    WINTUN_DOWNLOAD_URL,
    WINTUN_DLL,
    XRAY_ARCHIVE_PATH,
    XRAY_BUNDLED_FILES,
    XRAY_EXECUTABLE,
    XRAY_RELEASES_API,
    XRAY_RUNTIME_DIR,
)

XRAY_MINIMUM_TUN_VERSION = (26, 1, 13)


class XrayInstaller:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "Vynex-Client/1.0",
            }
        )

    def ensure_xray(self) -> Path:
        self.warnings = []
        self._prepare_runtime_dirs()
        self._copy_bundled_runtime()
        if XRAY_EXECUTABLE.exists():
            self._ensure_geo_data_files(download_missing_only=True)
            return XRAY_EXECUTABLE
        self.update_xray()
        self._ensure_geo_data_files(download_missing_only=True)
        return XRAY_EXECUTABLE

    def ensure_xray_tun_runtime(self) -> Path:
        self.ensure_xray()
        version = self.get_xray_version()
        if version is None or version < XRAY_MINIMUM_TUN_VERSION or not WINTUN_DLL.exists():
            self.update_xray()
            version = self.get_xray_version()
        if version is None:
            raise RuntimeError("Не удалось определить версию Xray-core для TUN режима.")
        if version < XRAY_MINIMUM_TUN_VERSION:
            version_text = ".".join(str(part) for part in version)
            minimum_text = ".".join(str(part) for part in XRAY_MINIMUM_TUN_VERSION)
            raise RuntimeError(
                f"Текущая версия Xray-core ({version_text}) не поддерживает TUN режим. "
                f"Нужна версия не ниже {minimum_text}."
            )
        if not WINTUN_DLL.exists():
            raise RuntimeError(
                "В runtime-каталоге отсутствует wintun.dll. "
                "Обновите Xray-core через 'Компоненты' или проверьте целостность runtime."
            )
        return XRAY_EXECUTABLE

    def ensure_amneziawg_runtime(self) -> Path:
        self.warnings = []
        self._prepare_runtime_dirs()
        self._copy_bundled_amneziawg_runtime()
        if self._has_complete_amneziawg_runtime():
            return AMNEZIAWG_EXECUTABLE if AMNEZIAWG_EXECUTABLE.exists() else AMNEZIAWG_EXECUTABLE_FALLBACK
        self.update_amneziawg()
        return AMNEZIAWG_EXECUTABLE if AMNEZIAWG_EXECUTABLE.exists() else AMNEZIAWG_EXECUTABLE_FALLBACK

    def update_xray(self) -> Path:
        self.warnings = []
        self._prepare_runtime_dirs()
        download_url = self._resolve_release_asset_url()
        self._download(download_url, XRAY_ARCHIVE_PATH)
        self._extract_release(XRAY_ARCHIVE_PATH)
        XRAY_ARCHIVE_PATH.unlink(missing_ok=True)
        if not XRAY_EXECUTABLE.exists():
            raise RuntimeError("После распаковки xray.exe не найден.")
        return XRAY_EXECUTABLE

    def update_geoip(self) -> Path:
        self.warnings = []
        self._prepare_runtime_dirs()
        self._download_geo_file(GEOIP_DOWNLOAD_URL, GEOIP_PATH)
        return GEOIP_PATH

    def update_geosite(self) -> Path:
        self.warnings = []
        self._prepare_runtime_dirs()
        self._download_geo_file(GEOSITE_DOWNLOAD_URL, GEOSITE_PATH)
        return GEOSITE_PATH

    def update_amneziawg(self) -> Path:
        self.warnings = []
        self._prepare_runtime_dirs()
        self._download_binary_file(AMNEZIAWG_EXECUTABLE_DOWNLOAD_URL, AMNEZIAWG_EXECUTABLE)
        self._download_binary_file(AMNEZIAWG_EXECUTABLE_FALLBACK_DOWNLOAD_URL, AMNEZIAWG_EXECUTABLE_FALLBACK)
        self._download_wintun_for_amneziawg()
        if not self._has_complete_amneziawg_runtime():
            raise RuntimeError("После обновления runtime AmneziaWG собран не полностью.")
        return AMNEZIAWG_EXECUTABLE

    def update_all_components(self) -> dict[str, Path]:
        self.warnings = []
        return {
            "xray.exe": self.update_xray(),
            "amneziawg": self.update_amneziawg(),
            "geoip.dat": self.update_geoip(),
            "geosite.dat": self.update_geosite(),
        }

    @staticmethod
    def _prepare_runtime_dirs() -> None:
        XRAY_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        AMNEZIAWG_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _copy_bundled_runtime(self) -> bool:
        source_executable = APP_DIR / "xray.exe"
        if not source_executable.exists():
            return False
        copied = False
        for filename in XRAY_BUNDLED_FILES:
            source = APP_DIR / filename
            destination = XRAY_RUNTIME_DIR / filename
            if source.exists() and not destination.exists():
                shutil.copy2(source, destination)
            copied = copied or destination.exists()
        return copied and XRAY_EXECUTABLE.exists()

    def _copy_bundled_amneziawg_runtime(self) -> bool:
        copied = False
        for filename in AMNEZIAWG_BUNDLED_FILES:
            source = APP_DIR / filename
            destination = AMNEZIAWG_RUNTIME_DIR / filename
            if source.exists() and not destination.exists():
                shutil.copy2(source, destination)
            copied = copied or destination.exists()
        return copied and self._has_complete_amneziawg_runtime()

    def _resolve_release_asset_url(self) -> str:
        try:
            response = self.session.get(XRAY_RELEASES_API, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось получить информацию о релизе Xray-core: {exc}") from exc
        payload = response.json()
        assets = payload.get("assets", [])
        candidates = []
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            if not name.endswith(".zip"):
                continue
            if "windows" not in name:
                continue
            if "arm64" in name:
                continue
            if any(token in name for token in ("64", "amd64", "x64")):
                candidates.append(asset)
        if not candidates:
            raise RuntimeError("Не найден архив Xray-core для Windows 64-bit.")
        return str(candidates[0]["browser_download_url"])

    def _download(self, url: str, target: Path) -> None:
        try:
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                with target.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=1024 * 512):
                        if chunk:
                            file_obj.write(chunk)
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось скачать Xray-core: {exc}") from exc

    def _download_binary_file(self, url: str, target: Path) -> Path:
        temp_target = target.with_suffix(f"{target.suffix}.tmp")
        try:
            with self.session.get(url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with temp_target.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=1024 * 512):
                        if chunk:
                            file_obj.write(chunk)
            temp_target.replace(target)
            return target
        except requests.RequestException as exc:
            temp_target.unlink(missing_ok=True)
            raise RuntimeError(
                f"Не удалось скачать {target.name} с {url}. Проверьте подключение или обновите файл вручную."
            ) from exc
        except OSError as exc:
            temp_target.unlink(missing_ok=True)
            raise RuntimeError(
                f"Не удалось сохранить {target.name} в {target}. Проверьте права доступа."
            ) from exc

    def _extract_release(self, archive_path: Path) -> None:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                extracted = False
                for member in archive.infolist():
                    name = Path(member.filename).name
                    if not name:
                        continue
                    lower_name = name.lower()
                    if lower_name == "xray.exe" or lower_name in {
                        "wintun.dll",
                        "geoip.dat",
                        "geosite.dat",
                    }:
                        destination = XRAY_RUNTIME_DIR / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as src, destination.open("wb") as dst:
                            dst.write(src.read())
                        extracted = extracted or lower_name == "xray.exe"
                if not extracted:
                    raise RuntimeError("В архиве Xray-core отсутствует xray.exe.")
        except zipfile.BadZipFile as exc:
            raise RuntimeError("Архив Xray-core поврежден или имеет неверный формат.") from exc

    def _download_wintun_for_amneziawg(self) -> Path:
        self._download_binary_file(WINTUN_DOWNLOAD_URL, WINTUN_ARCHIVE_PATH)
        self._extract_wintun_release(WINTUN_ARCHIVE_PATH, AMNEZIAWG_WINTUN_DLL)
        WINTUN_ARCHIVE_PATH.unlink(missing_ok=True)
        return AMNEZIAWG_WINTUN_DLL

    def _extract_wintun_release(self, archive_path: Path, target: Path) -> None:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    member_name = Path(member.filename).name.lower()
                    normalized_path = member.filename.replace("\\", "/").lower()
                    if member_name != "wintun.dll":
                        continue
                    if "/amd64/" not in f"/{normalized_path}":
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as src, target.open("wb") as dst:
                        dst.write(src.read())
                    return
                raise RuntimeError("В архиве Wintun отсутствует wintun.dll для Windows amd64.")
        except zipfile.BadZipFile as exc:
            raise RuntimeError("Архив Wintun поврежден или имеет неверный формат.") from exc

    @staticmethod
    def get_xray_version(executable_path: Path | None = None) -> tuple[int, int, int] | None:
        target = executable_path or XRAY_EXECUTABLE
        if not target.exists():
            return None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [str(target), "version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=creationflags,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return None
        output = (result.stdout or result.stderr or "").strip()
        match = re.search(r"Xray\s+(\d+)\.(\d+)\.(\d+)", output)
        if not match:
            return None
        return tuple(int(group) for group in match.groups())

    def _ensure_geo_data_files(self, *, download_missing_only: bool = False) -> None:
        for target, url in (
            (GEOIP_PATH, GEOIP_DOWNLOAD_URL),
            (GEOSITE_PATH, GEOSITE_DOWNLOAD_URL),
        ):
            if download_missing_only and target.exists():
                continue
            try:
                self._download_geo_file(url, target)
            except RuntimeError as exc:
                self.warnings.append(str(exc))

    def _download_geo_file(self, url: str, target: Path) -> None:
        temp_target = target.with_suffix(f"{target.suffix}.tmp")
        try:
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                with temp_target.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=1024 * 512):
                        if chunk:
                            file_obj.write(chunk)
            temp_target.replace(target)
        except requests.RequestException as exc:
            temp_target.unlink(missing_ok=True)
            raise RuntimeError(
                f"Не удалось скачать {target.name} с {url}. Проверьте подключение или обновите файл вручную."
            ) from exc
        except OSError as exc:
            temp_target.unlink(missing_ok=True)
            raise RuntimeError(
                f"Не удалось сохранить {target.name} в {target}. Проверьте права доступа."
            ) from exc

    @staticmethod
    def _has_complete_amneziawg_runtime() -> bool:
        return (
            AMNEZIAWG_EXECUTABLE.exists()
            and AMNEZIAWG_EXECUTABLE_FALLBACK.exists()
            and AMNEZIAWG_WINTUN_DLL.exists()
        )


class SingboxInstaller:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "Vynex-Client/1.0",
            }
        )

    def ensure_singbox(self) -> Path:
        self._prepare_runtime_dirs()
        if SINGBOX_EXECUTABLE.exists():
            return SINGBOX_EXECUTABLE
        self.update_singbox()
        return SINGBOX_EXECUTABLE

    def update_singbox(self) -> Path:
        self._prepare_runtime_dirs()
        download_url = self._resolve_release_asset_url()
        self._download(download_url, SINGBOX_ARCHIVE_PATH)
        self._extract_release(SINGBOX_ARCHIVE_PATH)
        SINGBOX_ARCHIVE_PATH.unlink(missing_ok=True)
        if not SINGBOX_EXECUTABLE.exists():
            raise RuntimeError("После распаковки sing-box.exe не найден.")
        return SINGBOX_EXECUTABLE

    @staticmethod
    def _prepare_runtime_dirs() -> None:
        XRAY_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        AMNEZIAWG_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _resolve_release_asset_url(self) -> str:
        try:
            response = self.session.get(SINGBOX_RELEASES_API, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось получить информацию о релизе sing-box: {exc}") from exc
        payload = response.json()
        assets = payload.get("assets", [])
        candidates = []
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            if not name.endswith(".zip"):
                continue
            if "windows-amd64" not in name:
                continue
            if "legacy" in name:
                continue
            candidates.append(asset)
        if not candidates:
            raise RuntimeError("Не найден архив sing-box для Windows 64-bit.")
        return str(candidates[0]["browser_download_url"])

    def _download(self, url: str, target: Path) -> None:
        try:
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                with target.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=1024 * 512):
                        if chunk:
                            file_obj.write(chunk)
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось скачать sing-box: {exc}") from exc

    def _extract_release(self, archive_path: Path) -> None:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                extracted = False
                for member in archive.infolist():
                    name = Path(member.filename).name
                    if not name:
                        continue
                    if name.lower() != "sing-box.exe":
                        continue
                    destination = XRAY_RUNTIME_DIR / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as src, destination.open("wb") as dst:
                        dst.write(src.read())
                    extracted = True
                if not extracted:
                    raise RuntimeError("В архиве sing-box отсутствует sing-box.exe.")
        except zipfile.BadZipFile as exc:
            raise RuntimeError("Архив sing-box поврежден или имеет неверный формат.") from exc
