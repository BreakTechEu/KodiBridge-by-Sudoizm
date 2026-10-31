# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

def _safe_parse_nfo_xml(raw_or_text):
    """Safely parse NFO XML, handling multi-episode NFOs with multiple root elements."""
    if not raw_or_text:
        return None
    if isinstance(raw_or_text, (bytes, bytearray)):
        text = bytes(raw_or_text).decode("utf-8-sig", errors="replace")
    else:
        text = str(raw_or_text)
    try:
        return ET.fromstring(text)
    except Exception:
        try:
            clean = re.sub(r'^\s*<\?xml[^>]*\?>', '', text, flags=re.IGNORECASE).strip()
            return ET.fromstring("<root>\n" + clean + "\n</root>")
        except Exception:
            return None



import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "service.sudosync"
SCHEMA_VERSION = 2

RATING_IMPORT_WHITELIST = {6, 7, 8}
UPDATE_ZIP_RE = re.compile(r"^(?:service[._]sudosync|kodibridge-by-sudoizm)[_-](\d+)[._](\d+)[._](\d+)(?:-([A-Za-z0-9._-]+))?\.zip$", re.IGNORECASE)
_VFS_CRED_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s]+@)")


def mask_vfs_credentials(text):
    """Strip user[:password]@ credentials from VFS URLs for safe UI/log display."""
    if not isinstance(text, str) or "@" not in text:
        return text
    return _VFS_CRED_RE.sub(r"\1", text)


def _core_win_unc(path):
    """Return a native Windows UNC path inside real Kodi on Windows when the share is accessible."""
    import sys
    if os.name != "nt" or "unittest" in sys.modules or "pytest" in sys.modules:
        return None
    try:
        from resources.lib.sudosync_v2.kodi_vfs import _resolve_win_unc
        return _resolve_win_unc(path, allow_win_unc=True)
    except Exception:
        return None


def _vfs_exists(path):
    unc = _core_win_unc(path)
    if unc is not None:
        return os.path.exists(unc)
    if xbmcvfs.exists(path):
        return True
    stripped = mask_vfs_credentials(path)
    if stripped != path:
        return bool(xbmcvfs.exists(stripped))
    return False


def _vfs_mkdirs(path):
    unc = _core_win_unc(path)
    if unc is not None:
        try:
            os.makedirs(unc, exist_ok=True)
            return True
        except Exception:
            return False
    if xbmcvfs.mkdirs(path):
        return True
    stripped = mask_vfs_credentials(path)
    if stripped != path:
        return bool(xbmcvfs.mkdirs(stripped))
    return False


def _vfs_delete(path):
    unc = _core_win_unc(path)
    if unc is not None:
        try:
            if os.path.exists(unc):
                os.remove(unc)
            return True
        except Exception:
            return False
    if xbmcvfs.delete(path):
        return True
    stripped = mask_vfs_credentials(path)
    if stripped != path:
        return bool(xbmcvfs.delete(stripped))
    return False


def _vfs_rmdir(path):
    unc = _core_win_unc(path)
    if unc is not None:
        try:
            if os.path.exists(unc):
                os.rmdir(unc)
            return True
        except Exception:
            return False
    if xbmcvfs.rmdir(path):
        return True
    stripped = mask_vfs_credentials(path)
    if stripped != path:
        return bool(xbmcvfs.rmdir(stripped))
    return False


def _vfs_listdir(path):
    unc = _core_win_unc(path)
    if unc is not None:
        if not os.path.exists(unc):
            return [], []
        dirs = []
        files = []
        with os.scandir(unc) as it:
            for entry in it:
                if entry.is_dir():
                    dirs.append(entry.name)
                else:
                    files.append(entry.name)
        return dirs, files
    dirs, files = xbmcvfs.listdir(path)
    dirs_list, files_list = list(dirs or []), list(files or [])
    if not dirs_list and not files_list:
        stripped = mask_vfs_credentials(path)
        if stripped != path:
            try:
                alt_d, alt_f = xbmcvfs.listdir(stripped)
                dirs_list, files_list = list(alt_d or []), list(alt_f or [])
            except Exception:
                pass
    return dirs_list, files_list


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[SudoSync] {}".format(mask_vfs_credentials(str(message))), level)


V2_LOGGER_NAME = "resources.lib.sudosync_v2"

# Window property: a settings action changed .nfo files, so the running
# service must drop its cached NFO identifiers (separate processes).
NFO_CACHE_REFRESH_PROPERTY = "service.sudosync.refresh_nfo_cache"
HOME_WINDOW_ID = 10000


def request_nfo_cache_refresh():
    """Ask the running service to re-read KodiBridge IDs from .nfo files."""
    try:
        xbmcgui.Window(HOME_WINDOW_ID).setProperty(NFO_CACHE_REFRESH_PROPERTY, "1")
    except Exception as exc:
        log("Cannot request NFO cache refresh: {}".format(exc), xbmc.LOGWARNING)


def take_nfo_cache_refresh_request():
    """Return True once if a settings action asked for an NFO cache refresh."""
    try:
        window = xbmcgui.Window(HOME_WINDOW_ID)
        if window.getProperty(NFO_CACHE_REFRESH_PROPERTY) != "1":
            return False
        window.clearProperty(NFO_CACHE_REFRESH_PROPERTY)
        return True
    except Exception:
        return False


class KodiLogHandler(logging.Handler):
    """Route Python ``logging`` records from the v2 engine to the Kodi log.

    Without a handler, Python's last-resort handler prints WARNING and above to
    stderr, which Kodi records as ERROR, and drops INFO/DEBUG entirely.
    """

    def emit(self, record):
        # A logging failure must never interrupt the synchronization engine.
        try:
            message = self.format(record)
            if record.levelno >= logging.ERROR:
                level = xbmc.LOGERROR
            elif record.levelno >= logging.WARNING:
                level = xbmc.LOGWARNING
            elif record.levelno >= logging.INFO:
                level = xbmc.LOGINFO
            else:
                level = xbmc.LOGDEBUG
            log(message, level)
        except Exception:
            pass


def install_v2_log_bridge(logger_name=V2_LOGGER_NAME):
    """Attach one KodiLogHandler to the v2 logger hierarchy (idempotent)."""
    target = logging.getLogger(logger_name)
    for handler in target.handlers:
        if isinstance(handler, KodiLogHandler):
            return handler
    handler = KodiLogHandler()
    handler.setFormatter(logging.Formatter("v2.%(module)s: %(message)s"))
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    target.propagate = False
    return handler


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_precise():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def rpc(method, params=None):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    raw = xbmc.executeJSONRPC(json.dumps(payload, ensure_ascii=False))
    data = json.loads(raw)
    if "error" in data:
        raise RuntimeError("JSON-RPC {} failed: {}".format(method, data["error"]))
    return data.get("result", {})


def _addon_profile_path():
    path = xbmcvfs.translatePath("special://profile/addon_data/{}/".format(ADDON_ID))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def _local_client_file():
    return os.path.join(_addon_profile_path(), "client.json")


def _read_local_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        log("Cannot read local JSON {}: {}".format(path, exc), xbmc.LOGWARNING)
        return None


def _write_local_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)


def get_or_create_client_id():
    path = _local_client_file()
    data = _read_local_json(path) or {}
    client_id = data.get("client_id")
    if client_id:
        return client_id
    client_id = str(uuid.uuid4())
    _write_local_json(path, {"client_id": client_id, "created_at": utc_now()})
    return client_id


def addon_settings():
    addon = xbmcaddon.Addon(ADDON_ID)
    base = addon.getSetting("base_path").strip()
    if base and not base.endswith("/"):
        base += "/"
    name = addon.getSetting("client_name").strip()
    if not name:
        name = xbmc.getInfoLabel("System.FriendlyName").strip() or "Kodi"
    try:
        interval = int(addon.getSetting("interval_seconds") or "900")
    except Exception:
        interval = 900
    interval = min(3600, max(60, interval))
    raw_notifications = addon.getSetting("notifications").strip().lower()
    # Existing installations may not have persisted this setting yet. Treat an
    # empty value as the documented/default ON state; only an explicit "false"
    # disables synchronization notifications.
    notifications = raw_notifications != "false"
    raw_check_updates = addon.getSetting("check_updates").strip().lower()
    check_updates = raw_check_updates != "false"
    raw_auto_update = addon.getSetting("auto_install_updates").strip().lower()
    auto_install_updates = raw_auto_update == "true"
    raw_live = addon.getSetting("live_sync_enabled").strip().lower()
    # 0.4 defaults to live sync ON, but writes are hard-gated by completed initialization.
    live_sync_enabled = raw_live != "false"
    try:
        live_poll = int(addon.getSetting("live_poll_seconds") or "60")
    except Exception:
        live_poll = 60
    live_poll = min(600, max(30, live_poll))
    
    raw_sudosync_id = addon.getSetting("sudosync_id_enabled").strip().lower()
    if not raw_sudosync_id:
        raw_sudosync_id = addon.getSetting("enable_sudosync_id").strip().lower()
    sudosync_id_enabled = raw_sudosync_id == "true"
    sudosync_id_prefix = addon.getSetting("sudosync_id_prefix").strip()
    if sudosync_id_prefix:
        sudosync_id_prefix = re.sub(r"[^A-Za-z0-9._-]", "-", sudosync_id_prefix)

    masked_base = mask_vfs_credentials(base)
    if not sudosync_id_prefix and masked_base.startswith("smb://"):
        try:
            host = masked_base.split("://")[1].split("/")[0].upper()
            sudosync_id_prefix = re.sub(r"[^A-Za-z0-9._-]", "-", host)
        except Exception:
            pass
    elif not sudosync_id_prefix and masked_base.startswith("nfs://"):
        try:
            host = masked_base.split("://")[1].split("/")[0]
            sudosync_id_prefix = re.sub(r"[^A-Za-z0-9._-]", "-", host)
        except Exception:
            pass
    if not sudosync_id_prefix:
        sudosync_id_prefix = "LOCAL"

    try:
        sudosync_id_last_number = max(0, int(addon.getSetting("sudosync_id_last_number").strip() or "0"))
    except Exception:
        sudosync_id_last_number = 0

    return {
        "base_path": base,
        "client_name": name,
        "interval_seconds": interval,
        "notifications": notifications,
        "check_updates": check_updates,
        "auto_install_updates": auto_install_updates,
        "live_sync_enabled": live_sync_enabled,
        "live_poll_seconds": live_poll,
        "sudosync_id_enabled": sudosync_id_enabled,
        "enable_sudosync_id": sudosync_id_enabled,
        "sudosync_id_prefix": sudosync_id_prefix,
        "sudosync_id_last_number": sudosync_id_last_number,
    }


def _shared_config_path(base_path):
    return _join_vfs(base_path, "system").rstrip("/") + "/config.json"


def read_shared_config(base_path=None):
    settings = addon_settings()
    base = (base_path or settings["base_path"]).rstrip("/") + "/"
    path = _shared_config_path(base)
    if not _vfs_exists(path):
        import xbmc
        xbmc.sleep(200)
        if not _vfs_exists(path):
            bak = path + ".bak"
            if _vfs_exists(bak):
                path = bak
            else:
                return {
                    "format": "SudoSync shared config",
                    "schema_version": 1,
                    "initialization": {
                        "completed": False,
                        "base_client_id": "",
                        "base_client_name": "",
                    },
                }
    try:
        data = _read_vfs_json(path)
        if not isinstance(data, dict):
            raise ValueError("nieprawidłowy format config.json")
        init = data.get("initialization") if isinstance(data.get("initialization"), dict) else {}
        data["initialization"] = {
            "completed": bool(init.get("completed", False)),
            "base_client_id": str(init.get("base_client_id") or ""),
            "base_client_name": str(init.get("base_client_name") or ""),
            "selected_at": str(init.get("selected_at") or ""),
            "completed_at": str(init.get("completed_at") or ""),
        }
        return data
    except Exception as exc:
        raise IOError("Nie można odczytać wspólnej konfiguracji KodiBridge: {}".format(exc))


def update_shared_config(mutator_func, base_path=None):
    if callable(base_path) and not callable(mutator_func):
        base_path, mutator_func = mutator_func, base_path
    settings = addon_settings()
    base = (base_path or settings["base_path"]).rstrip("/") + "/"
    ensure_remote_layout(base)
    system_dir = _join_vfs(base, "system")
    if not _vfs_exists(system_dir):
        _vfs_mkdirs(system_dir)
    
    path = _shared_config_path(base)
    lock_dir = path + ".lockdir"
    lock_info_path = lock_dir + "/lock_info.json"
    my_client_id = get_or_create_client_id()
    
    locked = False
    start = datetime.now(timezone.utc).timestamp()
    while True:
        if not _vfs_exists(lock_dir) and _vfs_mkdirs(lock_dir):
            # Direct (non-rotating) write: rename on SMB can fail and the lock file is disposable.
            try:
                if _vfs_exists(lock_info_path):
                    _vfs_delete(lock_info_path)
                _write_vfs_bytes(lock_info_path, json.dumps({"client_id": my_client_id, "timestamp": datetime.now(timezone.utc).timestamp()}))
            except Exception as exc:
                log("Nie udalo sie zapisac lock_info.json (kontynuacja z blokada katalogowa): {}".format(exc), xbmc.LOGWARNING)
            locked = True
            break
            
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            info = _read_vfs_json(lock_info_path) or {}
        except Exception:
            info = {}
            
        lock_ts = float(info.get("timestamp") or 0.0)
        no_info_stale = lock_ts <= 0.0 and (now_ts - start > 10.0)
        if no_info_stale or (lock_ts > 0.0 and (now_ts - lock_ts > 60.0)):
            log("Znaleziono osierocona blokade. Usuwanie...")
            try:
                _vfs_delete(lock_info_path)
                _vfs_rmdir(lock_dir)
            except Exception:
                pass
            continue
            
        if datetime.now(timezone.utc).timestamp() - start > 15.0:
            raise RuntimeError(
                "Nie można uzyskać blokady wspólnej konfiguracji KodiBridge w ciągu 15 sekund."
            )
        xbmc.sleep(200)

    try:
        data = None
        if _vfs_exists(path):
            try:
                data = _read_vfs_json(path)
            except Exception:
                pass
                
        if not isinstance(data, dict):
            data = {
                "format": "SudoSync shared config",
                "schema_version": 1,
                "initialization": {
                    "completed": False,
                    "base_client_id": "",
                    "base_client_name": "",
                },
            }
            
        current_rev = _safe_int(data.get("revision"), 0)
        
        changed = mutator_func(data)
        if changed is False:
            return path
            
        data["revision"] = current_rev + 1
        data["updated_at"] = utc_now()
        
        _write_vfs_json(path, data)
        return path
    finally:
        if locked:
            try:
                _vfs_delete(lock_info_path)
                _vfs_rmdir(lock_dir)
            except Exception:
                pass


def _numeric_version(text):
    try:
        text_str = str(text or "").strip().lower()
    except Exception:
        return (0, 0, 0, 3, 0)

    semver_match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text_str)
    if semver_match:
        major = int(semver_match.group(1))
        minor = int(semver_match.group(2)) if semver_match.group(2) is not None else 0
        patch = int(semver_match.group(3)) if semver_match.group(3) is not None else 0
    else:
        major, minor, patch = 0, 0, 0

    stage_rank = 3
    stage_num = 0

    stage_match = re.search(r"(?:[\-_./]|\b|(?<=\d))(alpha|beta|rc)(?:[\-_.]?(\d+))?(?!\w)", text_str)
    if stage_match:
        stage = stage_match.group(1)
        if stage == "alpha":
            stage_rank = 0
        elif stage == "beta":
            stage_rank = 1
        elif stage == "rc":
            stage_rank = 2

        num_str = stage_match.group(2)
        if num_str:
            try:
                stage_num = int(num_str)
            except Exception:
                stage_num = 0

    return (major, minor, patch, stage_rank, stage_num)


def _copy_vfs_to_local(src, dst):
    parent = os.path.dirname(dst)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if os.path.exists(dst):
        os.remove(dst)
    if not xbmcvfs.copy(src, dst):
        raise IOError("Nie udało się skopiować pakietu aktualizacji z: {}".format(src))
    if not os.path.isfile(dst) or os.path.getsize(dst) < 100:
        raise IOError("Skopiowany pakiet aktualizacji jest pusty lub niepełny.")


MAX_ZIP_FILES = 500
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ZIP_FILE_BYTES = 20 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _normalize_sha256(value):
    """Return a lowercase 64-hex digest from 'sha256:<hex>' / '<hex>' or None."""
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):].strip()
    return text if _SHA256_RE.match(text) else None


def _sha256_of_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_from_text(text, filename):
    """Find the digest for ``filename`` in a '<hex>  <name>' list or a lone '<hex>' file."""
    base = os.path.basename(str(filename or ""))
    lone = []
    for line in str(text or "").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        digest = _normalize_sha256(parts[0])
        if not digest:
            continue
        if len(parts) == 1:
            lone.append(digest)
        elif parts[-1].lstrip("*") == base:
            return digest
    return lone[0] if len(lone) == 1 else None


def _safe_zip_member(name):
    normalized = str(name or "").replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return False
    parts = [p for p in normalized.split("/") if p]
    if not parts or parts[0] != ADDON_ID:
        return False
    return all(part not in (".", "..") for part in parts)


def _validate_addon_zip_package(zip_path, expected_version=None, expected_sha256=None):
    """Validate integrity (SHA-256), size limits, structure and addon.xml of a SudoSync ZIP package."""
    if not os.path.isfile(zip_path) or os.path.getsize(zip_path) < 100:
        raise ValueError("Pakiet aktualizacji ZIP nie istnieje lub jest pusty.")
    if expected_sha256 is not None:
        expected = _normalize_sha256(expected_sha256)
        if not expected:
            raise ValueError("Nieprawidłowy format oczekiwanej sumy SHA-256.")
        actual = _sha256_of_file(zip_path)
        if actual != expected:
            raise ValueError(
                "Suma SHA-256 pakietu nie zgadza się (oczekiwano {}, obliczono {}).".format(expected, actual)
            )
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            file_infos = [i for i in zf.infolist() if not i.filename.replace("\\", "/").endswith("/")]
            if len(file_infos) > MAX_ZIP_FILES:
                raise ValueError("Pakiet ZIP zawiera zbyt wiele plików ({} > {}).".format(len(file_infos), MAX_ZIP_FILES))
            if any(i.file_size > MAX_ZIP_FILE_BYTES for i in file_infos):
                raise ValueError("Plik w pakiecie ZIP przekracza limit {} MB.".format(MAX_ZIP_FILE_BYTES // (1024 * 1024)))
            if sum(i.file_size for i in file_infos) > MAX_ZIP_TOTAL_BYTES:
                raise ValueError(
                    "Rozmiar danych po rozpakowaniu przekracza limit {} MB.".format(MAX_ZIP_TOTAL_BYTES // (1024 * 1024))
                )

            names = zf.namelist()
            normalized_map = {str(name or "").replace("\\", "/"): name for name in names}
            if not names or not all(
                _safe_zip_member(name) for name in normalized_map.keys() if name and not name.endswith("/")
            ):
                raise ValueError("Pakiet ZIP ma nieprawidłową strukturę lub zawiera niedozwoloną ścieżkę.")
            manifest_name = ADDON_ID + "/addon.xml"
            if manifest_name not in normalized_map:
                raise ValueError("Pakiet nie zawiera {}.".format(manifest_name))
            actual_name = normalized_map[manifest_name]
            manifest = ET.fromstring(zf.read(actual_name))
            if manifest.tag != "addon" or manifest.attrib.get("id") != ADDON_ID:
                raise ValueError("To nie jest pakiet SudoSync.")
            version = manifest.attrib.get("version") or "0.0.0"
            if expected_version and _numeric_version(version) != _numeric_version(expected_version):
                raise ValueError(
                    "Wersja w addon.xml ({}) nie zgadza się z oczekiwaną wersją pakietu ({}).".format(
                        version, expected_version
                    )
                )
            return version
    except zipfile.BadZipFile as exc:
        raise ValueError("Uszkodzony plik archiwum ZIP: {}".format(exc))


def _extract_and_swap_addon(local_zip, package_version, source_desc, show_dialogs=True):
    addon_path = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo("path"))
    addon_path = os.path.abspath(addon_path.rstrip("/\\"))
    addons_parent = os.path.dirname(addon_path)
    staging = os.path.join(addons_parent, ADDON_ID + ".__sudosync_update__")
    backup = os.path.join(addons_parent, ADDON_ID + ".__sudosync_backup__")

    for path in (staging, backup):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)

    os.makedirs(staging)
    try:
        with zipfile.ZipFile(local_zip, "r") as zf:
            prefix = ADDON_ID + "/"
            extracted_total = 0
            if len(zf.infolist()) > MAX_ZIP_FILES * 2:
                raise ValueError("Pakiet ZIP zawiera zbyt wiele wpisów.")
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.endswith("/"):
                    continue
                if not _safe_zip_member(name):
                    raise ValueError("Niedozwolona ścieżka w ZIP: {}".format(name))
                rel = name[len(prefix):]
                if not rel:
                    continue
                target = os.path.abspath(os.path.join(staging, *rel.split("/")))
                if os.path.commonpath([staging, target]) != os.path.abspath(staging):
                    raise ValueError("Niedozwolona ścieżka w ZIP: {}".format(name))
                parent = os.path.dirname(target)
                if not os.path.isdir(parent):
                    os.makedirs(parent)
                with zf.open(info, "r") as src, open(target, "wb") as dst:
                    file_bytes = 0
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        file_bytes += len(chunk)
                        extracted_total += len(chunk)
                        if file_bytes > MAX_ZIP_FILE_BYTES or extracted_total > MAX_ZIP_TOTAL_BYTES:
                            raise ValueError("Przekroczono limit rozmiaru danych podczas rozpakowywania pakietu ZIP.")
                        dst.write(chunk)

        staged_manifest = os.path.join(staging, "addon.xml")
        root = ET.parse(staged_manifest).getroot()
        if root.attrib.get("id") != ADDON_ID or _numeric_version(root.attrib.get("version")) != _numeric_version(package_version):
            raise ValueError("Walidacja katalogu tymczasowego aktualizacji nie powiodła się.")

        # Atomic-ish directory swap on the same local filesystem. If anything fails,
        # restore the previous add-on directory.
        os.rename(addon_path, backup)
        try:
            os.rename(staging, addon_path)
        except Exception:
            os.rename(backup, addon_path)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        xbmc.executebuiltin("UpdateLocalAddons")
    except Exception:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
        if not os.path.isdir(addon_path) and os.path.isdir(backup):
            os.rename(backup, addon_path)
        raise

    # Po udanej instalacji podbijamy min_addon_version w SudoSync.cfg (jeśli skonfigurowano udział)
    try:
        from resources.lib.sudosync_bootstrap import update_network_min_version
        base_path = addon_settings().get("base_path") or ""
        if base_path:
            update_network_min_version(base_path, package_version)
    except Exception as exc:
        log("Cannot update network min_addon_version after install: {}".format(exc), xbmc.LOGWARNING)

    log("Self-update installed from {} -> {}".format(source_desc, package_version))
    if show_dialogs:
        restart = xbmcgui.Dialog().yesno(
            "KodiBridge — aktualizacja zainstalowana",
            "Zainstalowano wersję {}.\n\n"
            "Nowa wersja stanie się w pełni aktywna po ponownym uruchomieniu Kodi. Uruchomić Kodi ponownie teraz?".format(package_version),
            yeslabel="Uruchom ponownie",
            nolabel="Później",
        )
        if restart:
            xbmc.executebuiltin("RestartApp")
    return {
        "installed": True,
        "available": True,
        "version": package_version,
        "filename": os.path.basename(local_zip),
        "remote_path": source_desc,
        "local_package": local_zip,
    }


GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/BreakTechEu/KodiBridge-by-Sudoizm/releases/latest"
def _local_day(epoch):
    """Return the local calendar date (YYYY-MM-DD) of a Unix timestamp."""
    return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d")


def find_github_update(timeout=10, api_url=None):
    """Query GitHub Releases API for the latest published SudoSync package."""
    import urllib.request
    current_text = xbmcaddon.Addon(ADDON_ID).getAddonInfo("version") or "0.0.0"
    current = _numeric_version(current_text)
    url = api_url or GITHUB_RELEASES_LATEST_URL
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "SudoSync-Kodi-Addon/{}".format(current_text),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as exc:
        return {
            "available": False,
            "current_version": current_text,
            "error": str(exc),
            "source": "github",
        }

    if not isinstance(data, dict):
        return {
            "available": False,
            "current_version": current_text,
            "error": "Nieprawidłowa odpowiedź z GitHub API.",
            "source": "github",
        }

    tag_name = str(data.get("tag_name") or "").strip()
    clean_tag = tag_name[1:] if tag_name.lower().startswith("v") else tag_name
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []

    chosen_asset = None
    chosen_version_text = clean_tag
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        dl_url = str(asset.get("browser_download_url") or "").strip()
        if not name or not dl_url or not name.lower().endswith(".zip"):
            continue
        match = UPDATE_ZIP_RE.match(name)
        if match:
            ver_tuple = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            suffix = match.group(4) or ""
            ver_text = "{}.{}.{}".format(*ver_tuple) + ("-" + suffix if suffix else "")
            chosen_asset = (name, dl_url)
            chosen_version_text = ver_text
            break
        if chosen_asset is None:
            chosen_asset = (name, dl_url)

    if not chosen_asset or not chosen_version_text:
        return {
            "available": False,
            "current_version": current_text,
            "version": clean_tag or current_text,
            "source": "github",
        }

    expected_sha256 = None
    sha256_url = ""
    for asset in assets:
        if isinstance(asset, dict) and str(asset.get("name") or "").strip() == chosen_asset[0]:
            expected_sha256 = _normalize_sha256(asset.get("digest"))
            break
    if not expected_sha256:
        expected_sha256 = _sha256_from_text(data.get("body"), chosen_asset[0])
    if not expected_sha256:
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("name") or "").strip().lower() == chosen_asset[0].lower() + ".sha256":
                sha256_url = str(asset.get("browser_download_url") or "").strip()
                break

    remote_tuple = _numeric_version(chosen_version_text)
    return {
        "available": remote_tuple > current,
        "current_version": current_text,
        "version": chosen_version_text,
        "filename": chosen_asset[0],
        "download_url": chosen_asset[1],
        "sha256": expected_sha256 or "",
        "sha256_url": sha256_url,
        "html_url": str(data.get("html_url") or ""),
        "source": "github",
    }


def _fetch_expected_sha256(info, filename, timeout):
    """Return the expected SHA-256 for the update ZIP or raise (fail closed)."""
    import urllib.request
    expected = _normalize_sha256(info.get("sha256"))
    if expected:
        return expected
    sha_url = str(info.get("sha256_url") or "").strip()
    if sha_url:
        if not sha_url.lower().startswith("https://"):
            raise ValueError("Niedozwolony adres pliku sumy kontrolnej (wymagany HTTPS): {}".format(sha_url))
        req = urllib.request.Request(
            sha_url,
            headers={"User-Agent": "SudoSync-Kodi-Addon/{}".format(info.get("current_version") or "2.0.0")},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(64 * 1024)
        except Exception as exc:
            raise IOError("Nie udało się pobrać pliku sumy SHA-256: {}".format(exc))
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        expected = _sha256_from_text(raw, filename)
        if expected:
            return expected
    raise ValueError(
        "Brak sumy kontrolnej SHA-256 dla pakietu aktualizacji w wydaniu GitHub - instalacja odrzucona."
    )


def install_github_update(update_info=None, show_dialogs=True, timeout=30):
    """Download and install the latest release ZIP from GitHub Releases."""
    import urllib.request
    info = update_info if isinstance(update_info, dict) else find_github_update(timeout=timeout)
    if info.get("error"):
        raise IOError(info["error"])
    if not info.get("available"):
        return {
            "installed": False,
            "available": False,
            "current_version": info.get("current_version"),
        }

    download_url = str(info.get("download_url") or "").strip()
    filename = os.path.basename(str(info.get("filename") or "service.sudosync-update.zip"))
    if not download_url.lower().startswith("https://"):
        raise ValueError("Niedozwolony adres pobierania aktualizacji (wymagany HTTPS): {}".format(download_url))

    expected_sha256 = _fetch_expected_sha256(info, filename, timeout)

    package_dir = xbmcvfs.translatePath("special://home/addons/packages/")
    if not os.path.isdir(package_dir):
        os.makedirs(package_dir)
    local_zip = os.path.join(package_dir, filename)
    part_zip = local_zip + ".part"

    req = urllib.request.Request(
        download_url,
        headers={"User-Agent": "SudoSync-Kodi-Addon/{}".format(info.get("current_version") or "2.0.0")},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part_zip, "wb") as dst:
            shutil.copyfileobj(resp, dst)
        os.replace(part_zip, local_zip)
    except Exception as exc:
        if os.path.exists(part_zip):
            try:
                os.remove(part_zip)
            except Exception:
                pass
        raise IOError("Nie udało się pobrać pakietu z GitHub Releases: {}".format(exc))

    try:
        package_version = _validate_addon_zip_package(local_zip, info.get("version"), expected_sha256=expected_sha256)
    except Exception:
        # Reject the update and remove the downloaded file.
        try:
            os.remove(local_zip)
        except OSError:
            pass
        raise
    return _extract_and_swap_addon(local_zip, package_version, download_url, show_dialogs=show_dialogs)


def install_update_from_zip_path(zip_path, allow_same_or_older=False, show_dialogs=True):
    """Install SudoSync from a user-selected local or VFS ZIP file."""
    zip_path = str(zip_path or "").strip()
    if not zip_path:
        return {"installed": False, "cancelled": True}

    current_text = xbmcaddon.Addon(ADDON_ID).getAddonInfo("version") or "0.0.0"
    filename = os.path.basename(zip_path.replace("\\", "/")) or "service.sudosync-local.zip"
    package_dir = xbmcvfs.translatePath("special://home/addons/packages/")
    if not os.path.isdir(package_dir):
        os.makedirs(package_dir)
    local_zip = os.path.join(package_dir, filename)

    if os.path.isfile(zip_path):
        if os.path.abspath(zip_path) != os.path.abspath(local_zip):
            shutil.copyfile(zip_path, local_zip)
    else:
        _copy_vfs_to_local(zip_path, local_zip)

    package_version = _validate_addon_zip_package(local_zip)
    if not allow_same_or_older and _numeric_version(package_version) < _numeric_version(current_text):
        raise ValueError(
            "Wybrany pakiet ({}) jest starszy niż obecnie zainstalowana wersja ({}).".format(
                package_version, current_text
            )
        )

    if show_dialogs:
        if not xbmcgui.Dialog().yesno(
            "KodiBridge — instalacja z pliku ZIP",
            "Zweryfikowano pakiet KodiBridge w wersji {} (obecnie zainstalowana: {}).\n\n"
            "Czy na pewno chcesz zainstalować tę wersję?".format(package_version, current_text),
            yeslabel="Zainstaluj",
            nolabel="Anuluj",
        ):
            return {"installed": False, "cancelled": True, "version": package_version}

    return _extract_and_swap_addon(local_zip, package_version, zip_path, show_dialogs=show_dialogs)


def maybe_check_github_update_daily(now_epoch=None, ask_and_install=True, force=False):
    """Check GitHub Releases once per local calendar day (unless force=True) and ask consent before installing.

    The first call on a new day (normally the first Kodi start that day, or the
    running service just after midnight) checks; later calls that day do not.
    """
    settings = addon_settings()
    if not settings.get("check_updates", True) and not force:
        return {"checked": False, "reason": "disabled"}

    now = float(now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp())
    local_status = read_local_status()
    try:
        last_check = float(local_status.get("last_github_update_check_epoch") or 0.0)
    except Exception:
        last_check = 0.0

    if not force and last_check > 0.0:
        try:
            checked_today = _local_day(last_check) == _local_day(now)
        except (ValueError, OverflowError, OSError):
            checked_today = False
        if checked_today:
            return {"checked": False, "reason": "not_due", "last_check_epoch": last_check}

    result = find_github_update()
    target_version = str(result.get("version") or "").strip()
    declined_version = str(local_status.get("last_declined_github_version") or "").strip()
    local_status["last_github_update_check_epoch"] = now
    local_status["last_github_update_check_at"] = utc_now()
    local_status["last_github_update_result"] = {
        "available": bool(result.get("available")),
        "version": target_version,
        "current_version": str(result.get("current_version") or ""),
        "error": str(result.get("error") or ""),
    }
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), local_status)

    if result.get("available") and ask_and_install:
        if not force and target_version and target_version == declined_version:
            result["declined_previously"] = True
            return result
        if xbmcgui.Dialog().yesno(
            "KodiBridge — dostępna aktualizacja",
            "Dostępna jest nowa wersja KodiBridge {} na GitHub Releases (obecnie zainstalowana: {}).\n\n"
            "Czy chcesz pobrać i zainstalować aktualizację teraz?".format(
                result.get("version"), result.get("current_version")
            ),
            yeslabel="Zainstaluj",
            nolabel="Później",
        ):
            try:
                installed = install_github_update(update_info=result, show_dialogs=True)
                result["installed"] = bool(installed.get("installed"))
            except Exception as exc:
                log("GitHub update installation failed: {}".format(exc), xbmc.LOGERROR)
                xbmcgui.Dialog().ok(
                    "KodiBridge — błąd aktualizacji",
                    "Nie udało się zainstalować aktualizacji z GitHub:\n{}".format(exc),
                )
        elif target_version:
            local_status["last_declined_github_version"] = target_version
            _write_local_json(os.path.join(_addon_profile_path(), "status.json"), local_status)
            result["declined"] = True
    return result


def _platform_name():
    if xbmc.getCondVisibility("System.Platform.Android"):
        return "android"
    if xbmc.getCondVisibility("System.Platform.Windows"):
        return "windows"
    if xbmc.getCondVisibility("System.Platform.Linux"):
        return "linux"
    if xbmc.getCondVisibility("System.Platform.OSX"):
        return "macos"
    return "unknown"


def get_kodi_info():
    try:
        app = rpc("Application.GetProperties", {"properties": ["name", "version"]})
    except Exception:
        app = {}
    try:
        jv = rpc("JSONRPC.Version")
    except Exception:
        jv = {}
    version = app.get("version") or {}
    version_text = "{}.{}.{}".format(version.get("major", 0), version.get("minor", 0), version.get("revision", 0))
    tag = version.get("tag")
    if tag:
        version_text += "-{}".format(tag)
    return {
        "name": app.get("name") or "Kodi",
        "version": version_text,
        "platform": _platform_name(),
        "jsonrpc": jv.get("version") or jv,
    }


def _normalize_ids(uniqueid, imdbnumber=None):
    ids = {}
    if isinstance(uniqueid, dict):
        for key, value in uniqueid.items():
            if value is None:
                continue
            val = str(value).strip()
            if val:
                ids[str(key).strip().lower()] = val
    if imdbnumber:
        val = str(imdbnumber).strip()
        if val and "imdb" not in ids and val.startswith("tt"):
            ids["imdb"] = val
    return ids


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _import_rating(value):
    value = _safe_int(value, 0)
    return value if value in RATING_IMPORT_WHITELIST else None


def _live_rating(value):
    value = _safe_int(value, 0)
    return value if 1 <= value <= 10 else 0


def _resume(value):
    value = value if isinstance(value, dict) else {}
    return {
        "position": round(_safe_float(value.get("position"), 0.0), 3),
        "total": round(_safe_float(value.get("total"), 0.0), 3),
    }


def _fallback_identity(kind, item):
    if kind == "movie":
        title = (item.get("originaltitle") or item.get("title") or "").strip()
        year = _safe_int(item.get("year"), 0)
        return "titleyear:{}:{}".format(_norm_text(title), year)
    show = (item.get("showtitle") or "").strip()
    return "episode:{}:s{:02d}e{:02d}".format(
        _norm_text(show), _safe_int(item.get("season"), 0), _safe_int(item.get("episode"), 0)
    )


def _norm_text(text):
    text = (text or "").casefold().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _nfo_candidates(media_file, kind):
    if not media_file:
        return []
    slash = max(media_file.rfind("/"), media_file.rfind("\\"))
    folder = media_file[: slash + 1] if slash >= 0 else ""
    filename = media_file[slash + 1 :] if slash >= 0 else media_file
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    candidates = [folder + stem + ".nfo"]
    if kind == "movie":
        candidates.append(folder + "movie.nfo")
    return candidates


def _read_vfs_text(path, max_bytes=256 * 1024):
    try:
        unc = _core_win_unc(path)
        if unc is not None:
            with open(unc, "rb") as f:
                raw = f.read(max_bytes)
            return raw.decode("utf-8-sig", errors="replace")
        f = xbmcvfs.File(path)
        raw = f.readBytes(max_bytes)
        f.close()
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw).decode("utf-8-sig", errors="replace")
        return str(raw or "")
    except Exception:
        return ""


def _find_best_nfo(media_file, kind):
    candidates = _nfo_candidates(media_file, kind)
    first_existing = None
    
    for path in candidates:
        if not _vfs_exists(path):
            continue
        if first_existing is None:
            first_existing = path
        text = _read_vfs_text(path)
        if not text:
            continue
        root = _safe_parse_nfo_xml(text)
        if root is None:
            continue
            
        ids = {}
        find_target = ".//uniqueid" if root.tag == "root" else "uniqueid"
        for node in root.findall(find_target):
            typ = (node.attrib.get("type") or "").strip().lower()
            val = (node.text or "").strip()
            if typ and val and typ not in ids:
                ids[typ] = val
        if kind == "movie":
            tmdbid = root.findtext(".//tmdbid") if root.tag == "root" else root.findtext("tmdbid")
            if tmdbid and "tmdb" not in ids:
                ids["tmdb"] = tmdbid.strip()
            legacy = root.findtext(".//id") if root.tag == "root" else root.findtext("id")
            if legacy and legacy.strip().startswith("tt") and "imdb" not in ids:
                ids["imdb"] = legacy.strip()
                
        if ids:
            return path, ids, text
            
    return first_existing or (candidates[0] if candidates else None), {}, ""

_NFO_ID_CACHE = {}

def _ids_from_nfo(media_file, kind):
    if not media_file:
        return {}
    if media_file in _NFO_ID_CACHE:
        return dict(_NFO_ID_CACHE[media_file])
    _, ids, _ = _find_best_nfo(media_file, kind)
    ids = ids if isinstance(ids, dict) else {}
    _NFO_ID_CACHE[media_file] = dict(ids)
    return dict(ids)


def _record(kind, item, first_import=True):
    db_ids = _normalize_ids(item.get("uniqueid"), item.get("imdbnumber"))
    ids = dict(db_ids)
    nfo_ids = _ids_from_nfo(item.get("file"), kind)
    for k, v in nfo_ids.items():
        if k not in ids and v:
            ids[k] = v
    rec = {
        "type": kind,
        "ids": ids,
        "fallback_identity": _fallback_identity(kind, item),
        "title": item.get("title") or "",
        "originaltitle": item.get("originaltitle") or "",
        "file": item.get("file") or "",
        "state": {
            "playcount": _safe_int(item.get("playcount"), 0),
            "lastplayed": item.get("lastplayed") or "",
            "resume": _resume(item.get("resume")),
            "userrating": _import_rating(item.get("userrating")) if first_import else _live_rating(item.get("userrating")),
        },
        "local": {},
    }
    if kind == "movie":
        rec["year"] = _safe_int(item.get("year"), 0)
        rec["local"]["movieid"] = _safe_int(item.get("movieid"), -1)
    else:
        rec["showtitle"] = item.get("showtitle") or ""
        rec["season"] = _safe_int(item.get("season"), 0)
        rec["episode"] = _safe_int(item.get("episode"), 0)
        rec["local"]["episodeid"] = _safe_int(item.get("episodeid"), -1)
    return rec


def collect_library(first_import=True, progress_callback=None, is_canceled=None):
    if first_import:
        _NFO_ID_CACHE.clear()
    if is_canceled and is_canceled():
        return [], []
    movie_props = [
        "title", "originaltitle", "year", "imdbnumber", "uniqueid", "file",
        "playcount", "lastplayed", "resume", "userrating"
    ]
    episode_props = [
        "title", "originaltitle", "showtitle", "season", "episode", "uniqueid", "file",
        "playcount", "lastplayed", "resume", "userrating"
    ]
    movies_result = rpc("VideoLibrary.GetMovies", {"properties": movie_props})
    if is_canceled and is_canceled():
        return [], []
    episodes_result = rpc("VideoLibrary.GetEpisodes", {"properties": episode_props})
    if is_canceled and is_canceled():
        return [], []

    raw_movies = movies_result.get("movies", []) if isinstance(movies_result, dict) else []
    raw_episodes = episodes_result.get("episodes", []) if isinstance(episodes_result, dict) else []
    total_raw = len(raw_movies) + len(raw_episodes)
    processed = 0

    movies = []
    for x in raw_movies:
        if is_canceled and is_canceled():
            return movies, []
        movies.append(_record("movie", x, first_import=first_import))
        processed += 1
        if progress_callback:
            progress_callback(processed, total_raw, x.get("title") or x.get("originaltitle") or "")

    episodes = []
    for x in raw_episodes:
        if is_canceled and is_canceled():
            return movies, episodes
        episodes.append(_record("episode", x, first_import=first_import))
        processed += 1
        if progress_callback:
            progress_callback(processed, total_raw, x.get("title") or x.get("originaltitle") or "")

    return movies, episodes


def _join_vfs(base, *parts):
    out = base.rstrip("/") + "/"
    for part in parts:
        out += str(part).strip("/") + "/"
    return out


def _write_vfs_bytes(path, payload):
    unc = _core_win_unc(path)
    if unc is not None:
        raw_bytes = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        with open(unc, "wb") as f:
            f.write(raw_bytes)
            f.flush()
        return len(raw_bytes) or True
    if isinstance(payload, str):
        payload = bytearray(payload.encode("utf-8"))
    elif isinstance(payload, bytes):
        payload = bytearray(payload)
    f = xbmcvfs.File(path, "w")
    try:
        written = f.write(payload)
    finally:
        f.close()
    if not written:
        raise IOError("VFS write failed: {}".format(path))
    return written


# Windows refuses to replace a file on an SMB share while another handle has it
# open (e.g. the background service reading system/config.json), reporting
# "access denied". Such collisions are short, so the replace is retried.
UNC_REPLACE_ATTEMPTS = 10
UNC_REPLACE_DELAY_MS = 200


def _replace_with_retry(source, target, attempts=None, delay_ms=None):
    attempts = UNC_REPLACE_ATTEMPTS if attempts is None else attempts
    delay_ms = UNC_REPLACE_DELAY_MS if delay_ms is None else delay_ms
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            if attempt:
                log("Replaced {} after {} retries (file was busy).".format(target, attempt), xbmc.LOGDEBUG)
            return
        except OSError as exc:
            if attempt == attempts - 1:
                raise
            log("Replace of {} refused ({}); retrying.".format(target, exc), xbmc.LOGDEBUG)
            xbmc.sleep(int(delay_ms * (attempt + 1)))


def _write_vfs_json(path, data):
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    unc = _core_win_unc(path)
    if unc is not None:
        tmp_unc = unc + ".new"
        with open(tmp_unc, "wb") as f:
            f.write(payload.encode("utf-8"))
            f.flush()
        _replace_with_retry(tmp_unc, unc)
        return
    tmp = path + ".new"
    bak = path + ".bak"
    _write_vfs_bytes(tmp, payload)
    if xbmcvfs.exists(bak):
        xbmcvfs.delete(bak)
    had_old = xbmcvfs.exists(path)
    backed_up = False
    if had_old:
        # Back up by COPY: renaming the live file is frequently refused by SMB
        # servers that have not yet released the handle of a just-written file.
        backed_up = bool(xbmcvfs.copy(path, bak))
        if not backed_up:
            log("Nie udalo sie utworzyc kopii .bak dla {} - kontynuacja bez kopii".format(path), xbmc.LOGWARNING)
    installed = False
    for attempt in range(5):
        if xbmcvfs.exists(path):
            xbmcvfs.delete(path)
        if xbmcvfs.rename(tmp, path):
            installed = True
            break
        xbmc.sleep(150 * (attempt + 1))
    if not installed:
        if had_old and backed_up and not xbmcvfs.exists(path):
            xbmcvfs.copy(bak, path)
        if xbmcvfs.exists(tmp):
            xbmcvfs.delete(tmp)
        raise IOError("Cannot install new file: {}".format(path))
    if xbmcvfs.exists(bak):
        xbmcvfs.delete(bak)


def ensure_remote_layout(base_path):
    if not _vfs_exists(base_path):
        _vfs_mkdirs(base_path)
    clients = _join_vfs(base_path, "clients")
    if not _vfs_exists(clients):
        _vfs_mkdirs(clients)
    system_dir = _join_vfs(base_path, "system")
    if not _vfs_exists(system_dir):
        _vfs_mkdirs(system_dir)
    tx_dir = _join_vfs(base_path, "transactions")
    if not _vfs_exists(tx_dir):
        _vfs_mkdirs(tx_dir)
    tx_backups = _join_vfs(base_path, "transactions", "backups")
    if not _vfs_exists(tx_backups):
        _vfs_mkdirs(tx_backups)
    tx_journals = _join_vfs(base_path, "transactions", "journals")
    if not _vfs_exists(tx_journals):
        _vfs_mkdirs(tx_journals)
    marker = base_path.rstrip("/") + "/format.json"
    if not _vfs_exists(marker):
        _write_vfs_json(marker, {
            "format": "SudoSync",
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now(),
            "mode": "collect_only",
        })
    return clients


def read_local_status():
    data = _read_local_json(os.path.join(_addon_profile_path(), "status.json")) or {}
    if not data:
        return {
            "client_id": get_or_create_client_id(),
            "client_name": addon_settings()["client_name"],
            "last_snapshot_at": "—",
            "remote_path": "—",
            "counts": {"movies": 0, "episodes": 0},
            "kodi": get_kodi_info(),
        }
    return data


# status.json keys written only by the removed 1.x engine; dropped on the next v2 update.
OBSOLETE_V1_STATUS_KEYS = (
    "dry_run_local_plan", "dry_run_report_path", "dry_run_snapshot_errors", "dry_run_summary",
    "last_dry_run_at", "last_initial_apply_at", "last_initial_apply_backup",
    "last_initial_apply_completed", "last_initial_apply_post_plan", "last_initial_apply_report",
    "last_live_apply_report", "last_live_guarded_items", "last_live_plan_at",
    "last_live_published_at", "last_live_published_fields", "last_live_published_items",
    "last_live_report_path", "last_local_probe_at", "last_local_probe_changed",
    "last_local_probe_changed_items", "last_local_probe_reason", "live_local_plan",
    "live_waiting_clients",
)


def record_kodibridge_id_coverage(coverage):
    """Store the KodiBridge ID coverage in status.json (only when it changed)."""
    if not isinstance(coverage, dict):
        return False
    try:
        status = read_local_status()
        stored = dict(coverage)
        previous = status.get("kodibridge_id_coverage") if isinstance(status, dict) else None
        if isinstance(previous, dict):
            comparable = dict(previous)
            comparable.pop("checked_at", None)
            if comparable == stored:
                return False
        stored["checked_at"] = utc_now()
        status["kodibridge_id_coverage"] = stored
        _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)
        return True
    except Exception as exc:
        log("Cannot record KodiBridge ID coverage: {}".format(exc), xbmc.LOGDEBUG)
        return False


def update_local_status_from_v2(v2_result=None, published_snapshot=None, candidate_snapshot=None, error=None):
    """Update status.json immediately from v2 runtime results."""
    try:
        status = read_local_status()
        settings = addon_settings()
        client_id = get_or_create_client_id()
        kodi = get_kodi_info()

        status["client_id"] = client_id
        status["client_name"] = settings["client_name"]
        status["kodi"] = kodi

        snap = published_snapshot
        if snap is None and isinstance(v2_result, dict):
            preview = v2_result.get("preview") or {}
            snap = preview.get("snapshot")
            if snap is None:
                snap = v2_result.get("snapshot")
        if snap is None and candidate_snapshot is not None:
            snap = candidate_snapshot

        if isinstance(snap, dict):
            movies = snap.get("movies") or []
            episodes = snap.get("episodes") or []
            status["counts"] = {"movies": len(movies), "episodes": len(episodes)}
            generated_at = snap.get("generated_at") or utc_now_precise()
            status["last_snapshot_at"] = generated_at
            pub_path = (v2_result.get("published_snapshot_path") if isinstance(v2_result, dict) else None)
            if pub_path:
                status["remote_path"] = pub_path
            elif not status.get("remote_path") or status.get("remote_path") == "—":
                base_clean = (settings.get("base_path") or "").rstrip("/")
                if base_clean:
                    status["remote_path"] = "{}/clients/client-{}.json".format(base_clean, client_id)

        if isinstance(v2_result, dict):
            status_val = v2_result.get("status")
            if status_val in ("committed", "no_changes"):
                status["last_live_sync_at"] = utc_now_precise()
                status["last_live_applied_items"] = int(v2_result.get("applied_count") or 0)
                status["last_live_error"] = ""
                if status_val == "committed" and status["last_live_applied_items"] > 0:
                    # Kept until the next cycle that actually receives changes, so
                    # the status window does not show 0 after a quiet cycle.
                    status["last_live_received_items"] = status["last_live_applied_items"]
                    status["last_live_received_at"] = status["last_live_sync_at"]
            elif status_val == "deferred":
                status["last_live_deferred_reason"] = v2_result.get("reason", "deferred")

        if error:
            status["last_live_error"] = mask_vfs_credentials(str(error))

        try:
            status["sudosync_id_last_number"] = get_network_sudosync_id_last_number(settings.get("base_path"))
        except Exception:
            pass

        for key in OBSOLETE_V1_STATUS_KEYS:
            status.pop(key, None)

        _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)
        return status
    except Exception as exc:
        log("update_local_status_from_v2 failed: {}".format(exc), xbmc.LOGWARNING)
        return None


def _read_vfs_all(path, chunk_size=1024 * 1024, max_size=50*1024*1024):
    """Read an arbitrary-size VFS file without assuming local filesystem access."""
    unc = _core_win_unc(path)
    if unc is not None:
        if not os.path.exists(unc):
            return b""
        with open(unc, "rb") as f:
            data = f.read(max_size + 1)
        if len(data) > max_size:
            raise ValueError("Plik przekracza limit rozmiaru: {}".format(path))
        return data
    f = xbmcvfs.File(path)
    parts = []
    total_size = 0
    try:
        while True:
            chunk = f.readBytes(chunk_size)
            if not chunk:
                break
            if isinstance(chunk, bytearray):
                chunk = bytes(chunk)
            elif not isinstance(chunk, bytes):
                chunk = str(chunk).encode("utf-8", errors="replace")
            total_size += len(chunk)
            if total_size > max_size:
                raise ValueError("Plik przekracza limit rozmiaru: {}".format(path))
            parts.append(chunk)
            if len(chunk) < chunk_size:
                break
    finally:
        f.close()
    return b"".join(parts)


def _read_vfs_json(path, max_attempts=3, retry_delay_ms=200):
    last_err = None
    for attempt in range(max_attempts):
        raw = _read_vfs_all(path)
        if raw:
            try:
                return json.loads(raw.decode("utf-8-sig", errors="strict"))
            except (ValueError, UnicodeDecodeError) as err:
                last_err = err
        else:
            last_err = ValueError("Empty JSON file: {}".format(path))
        if attempt < max_attempts - 1:
            try:
                xbmc.sleep(retry_delay_ms)
            except Exception:
                import time
                time.sleep(retry_delay_ms / 1000.0)
    # If still failed, check if .bak exists as emergency fallback
    bak = path + ".bak"
    if _vfs_exists(bak):
        raw_bak = _read_vfs_all(bak)
        if raw_bak:
            try:
                log("Fallback read from .bak for {}".format(path), xbmc.LOGWARNING)
                return json.loads(raw_bak.decode("utf-8-sig", errors="strict"))
            except Exception:
                pass
    if last_err:
        raise last_err
    raise ValueError("Empty JSON file: {}".format(path))


_SEQ_SUDOSYNC_SUFFIX_RE = re.compile(r"^\d{1,9}$")


def _has_sudosync_id(ids_dict):
    if not isinstance(ids_dict, dict):
        return False
    val = ids_dict.get("sudosync")
    return bool(val is not None and str(val).strip())


def _extract_sudosync_id_number(value):
    if not isinstance(value, str):
        return 0
    text = value.strip()
    if not text:
        return 0
    suffix = text.rsplit("-", 1)[-1].strip() if "-" in text else text
    if _SEQ_SUDOSYNC_SUFFIX_RE.match(suffix):
        try:
            num = int(suffix, 10)
            return num if num > 0 else 0
        except Exception:
            return 0
    return 0


def _max_sudosync_id_number_from_records(records):
    max_num = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        for key in ("ids", "uniqueid"):
            ids_obj = rec.get(key)
            if isinstance(ids_obj, dict):
                num = _extract_sudosync_id_number(ids_obj.get("sudosync"))
                if num > max_num:
                    max_num = num
    return max_num


def _nfo_raw_has_sudosync_id(raw_bytes, kind=None):
    """Inspect raw NFO bytes and return (has_sudosync_id, extracted_ids_dict)."""
    if not raw_bytes:
        return False, {}
    if isinstance(raw_bytes, (bytes, bytearray)):
        text = bytes(raw_bytes).decode("utf-8-sig", errors="replace")
        raw_lower = bytes(raw_bytes).lower()
    else:
        text = str(raw_bytes)
        raw_lower = text.encode("utf-8", errors="replace").lower()
    root = _safe_parse_nfo_xml(text)
    if root is None:
        return (b'type="sudosync"' in raw_lower or b"type='sudosync'" in raw_lower), {}

    ids = {}
    find_target = ".//uniqueid" if root.tag == "root" else "uniqueid"
    for node in root.findall(find_target):
        typ = (node.attrib.get("type") or "").strip().lower()
        val = (node.text or "").strip()
        if typ and val and typ not in ids:
            ids[typ] = val
    if kind == "movie":
        tmdbid = root.findtext(".//tmdbid") if root.tag == "root" else root.findtext("tmdbid")
        if tmdbid and tmdbid.strip() and "tmdb" not in ids:
            ids["tmdb"] = tmdbid.strip()
        legacy = root.findtext(".//id") if root.tag == "root" else root.findtext("id")
        if legacy and legacy.strip().startswith("tt") and "imdb" not in ids:
            ids["imdb"] = legacy.strip()
    has_sudosync = bool(ids.get("sudosync") and str(ids["sudosync"]).strip())
    return has_sudosync, ids


def get_network_sudosync_id_last_number(base_path=None, local_records=None):
    """Return the highest sequential SudoSync ID number known across local and network state."""
    max_num = _max_sudosync_id_number_from_records(local_records)
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        local_setting = _safe_int(addon.getSetting("sudosync_id_last_number"), 0)
        if local_setting > max_num:
            max_num = local_setting
    except Exception:
        pass

    try:
        status_path = os.path.join(_addon_profile_path(), "status.json")
        status_data = _read_local_json(status_path)
        if isinstance(status_data, dict):
            status_num = _safe_int(status_data.get("sudosync_id_last_number"), 0)
            if status_num > max_num:
                max_num = status_num
    except Exception:
        pass

    try:
        base = base_path
        if base is None:
            base = addon_settings().get("base_path") or ""
        base = str(base or "").strip()
    except Exception:
        base = ""

    if base:
        base_norm = base.rstrip("/") + "/"
        try:
            cfg_path = base_norm + "SudoSync.cfg"
            if xbmcvfs.exists(cfg_path):
                cfg_data = _read_vfs_json(cfg_path)
                if isinstance(cfg_data, dict):
                    net = cfg_data.get("network_settings")
                    if isinstance(net, dict):
                        cfg_num = _safe_int(net.get("sudosync_id_last_number"), 0)
                        if cfg_num > max_num:
                            max_num = cfg_num
        except Exception:
            pass

        try:
            shared_path = _shared_config_path(base_norm)
            if xbmcvfs.exists(shared_path):
                shared_data = _read_vfs_json(shared_path)
                if isinstance(shared_data, dict):
                    sh_num = _safe_int(shared_data.get("sudosync_id_last_number"), 0)
                    if sh_num > max_num:
                        max_num = sh_num
                    sh_net = shared_data.get("network_settings")
                    if isinstance(sh_net, dict):
                        sh_net_num = _safe_int(sh_net.get("sudosync_id_last_number"), 0)
                        if sh_net_num > max_num:
                            max_num = sh_net_num
        except Exception:
            pass

        for subdir in ("clients", "snapshots"):
            try:
                dir_path = _join_vfs(base_norm, subdir)
                if not _vfs_exists(dir_path):
                    continue
                _dirs, files = _vfs_listdir(dir_path)
                if not isinstance(files, (list, tuple)):
                    continue
                for filename in files:
                    if not isinstance(filename, str) or not filename.lower().endswith(".json"):
                        continue
                    try:
                        snap = _read_vfs_json(dir_path + filename)
                        if isinstance(snap, dict):
                            snap_records = []
                            for col in ("movies", "episodes"):
                                items = snap.get(col)
                                if isinstance(items, list):
                                    snap_records.extend(items)
                            snap_num = _max_sudosync_id_number_from_records(snap_records)
                            if snap_num > max_num:
                                max_num = snap_num
                    except Exception:
                        continue
            except Exception:
                continue

    return max(0, max_num)


def persist_network_sudosync_id_last_number(last_number, base_path=None, s=None):
    """Persist the highest used sequential SudoSync ID number locally and on the shared NAS.

    Updates SudoSync.cfg (network_settings.sudosync_id_last_number) and system/config.json
    atomically inside the shared config lock (update_shared_config), preserving monotonicity.
    Returns the actually persisted number, or raises IOError on NAS write failure when base_path is set.
    """
    base = base_path if base_path is not None else s
    if base is None:
        try:
            base = addon_settings().get("base_path") or ""
        except Exception:
            base = ""
    base = str(base or "").strip()

    target_num = max(0, _safe_int(last_number, 0))
    try:
        current_known = get_network_sudosync_id_last_number(base_path=base)
        if current_known > target_num:
            target_num = current_known
    except Exception:
        pass

    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        cur_local = _safe_int(addon.getSetting("sudosync_id_last_number"), 0)
        if cur_local > target_num:
            target_num = cur_local
    except Exception:
        pass

    try:
        status_path = os.path.join(_addon_profile_path(), "status.json")
        existing_status = _read_local_json(status_path)
        if isinstance(existing_status, dict):
            cur_status = _safe_int(existing_status.get("sudosync_id_last_number"), 0)
            if cur_status > target_num:
                target_num = cur_status
    except Exception:
        pass

    actual_persisted = [target_num]

    if base:
        base_norm = base.rstrip("/") + "/"
        cfg_path = base_norm + "SudoSync.cfg"

        def _mutator(shared_data):
            # 1. Inspect and update SudoSync.cfg inside the shared lock
            cur_cfg_num = 0
            cfg_obj = None
            if xbmcvfs.exists(cfg_path):
                try:
                    cfg_obj = _read_vfs_json(cfg_path)
                    if isinstance(cfg_obj, dict):
                        net = cfg_obj.setdefault("network_settings", {})
                        if isinstance(net, dict):
                            cur_cfg_num = _safe_int(net.get("sudosync_id_last_number"), 0)
                except Exception as exc:
                    log("Błąd odczytu SudoSync.cfg podczas aktualizacji licznika: {}".format(exc), xbmc.LOGWARNING)

            actual_persisted[0] = max(actual_persisted[0], cur_cfg_num)

            # 2. Inspect and update system/config.json inside the shared lock
            cur_sh = _safe_int(shared_data.get("sudosync_id_last_number"), 0)
            net_sh = shared_data.setdefault("network_settings", {})
            cur_net = _safe_int(net_sh.get("sudosync_id_last_number"), 0) if isinstance(net_sh, dict) else 0

            actual_persisted[0] = max(actual_persisted[0], cur_sh, cur_net)
            final_val = actual_persisted[0]

            # Write to SudoSync.cfg if needed
            if isinstance(cfg_obj, dict):
                net = cfg_obj.setdefault("network_settings", {})
                if isinstance(net, dict):
                    if final_val > cur_cfg_num:
                        net["sudosync_id_last_number"] = final_val
                        _write_vfs_json(cfg_path, cfg_obj)

            # Update system/config.json
            shared_data["sudosync_id_last_number"] = final_val
            if isinstance(net_sh, dict):
                net_sh["sudosync_id_last_number"] = final_val

            return True

        try:
            update_shared_config(_mutator, base_norm)
        except Exception as exc:
            log("Błąd atomowej synchronizacji licznika SudoSync ID na NAS: {}".format(exc), xbmc.LOGERROR)
            raise IOError("Nie udało się zapisać licznika KodiBridge ID na NAS: {}".format(exc))

    final_saved = actual_persisted[0]

    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        cur_local = _safe_int(addon.getSetting("sudosync_id_last_number"), 0)
        if final_saved > cur_local:
            addon.setSetting("sudosync_id_last_number", str(final_saved))
    except Exception:
        pass

    try:
        status_path = os.path.join(_addon_profile_path(), "status.json")
        existing_status = _read_local_json(status_path)
        if isinstance(existing_status, dict):
            if final_saved > _safe_int(existing_status.get("sudosync_id_last_number"), 0):
                existing_status["sudosync_id_last_number"] = final_saved
                _write_local_json(status_path, existing_status)
    except Exception:
        pass

    return final_saved


def get_or_sync_network_id_and_alias(base_path=None):
    addon = xbmcaddon.Addon(ADDON_ID)
    settings = addon_settings()
    base = base_path or settings["base_path"]
    config = read_shared_config(base)
    
    local_prefix = addon.getSetting("sudosync_id_prefix").strip()
    last_synced = addon.getSetting("sudosync_id_prefix_last_synced").strip()
    try:
        known_last_num = get_network_sudosync_id_last_number(base)
        if known_last_num > 0:
            persist_network_sudosync_id_last_number(known_last_num, base)
    except Exception:
        pass
    
    network_id = config.get("sudosync_network_id")
    if not network_id:
        if local_prefix:
            generated_id = local_prefix
        else:
            import random, string
            generated_id = "NET-" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        
        def mutator1(cfg):
            if not cfg.get("sudosync_network_id"):
                cfg["sudosync_network_id"] = generated_id
                cfg["sudosync_network_alias"] = local_prefix or generated_id
                cfg["sudosync_network_alias_ts"] = utc_now()
                return True
            return False
            
        update_shared_config(mutator1, base)
        config = read_shared_config(base)
        network_id = config.get("sudosync_network_id") or generated_id
        alias = config.get("sudosync_network_alias") or local_prefix or network_id
        addon.setSetting("sudosync_id_prefix", alias)
        addon.setSetting("sudosync_id_prefix_last_synced", alias)
        return network_id
        
    shared_alias = config.get("sudosync_network_alias", "")
    
    if local_prefix != last_synced:
        def mutator2(cfg):
            cfg["sudosync_network_alias"] = local_prefix
            cfg["sudosync_network_alias_ts"] = utc_now()
            return True
        update_shared_config(mutator2, base)
        addon.setSetting("sudosync_id_prefix_last_synced", local_prefix)
    elif shared_alias != local_prefix:
        addon.setSetting("sudosync_id_prefix", shared_alias)
        addon.setSetting("sudosync_id_prefix_last_synced", shared_alias)
        
    return network_id


def _build_created_nfo_xml(kind, title_text, full_id):
    import xml.sax.saxutils
    if kind == "movie":
        root_tag = "movie"
    elif kind == "episode":
        root_tag = "episodedetails"
    elif kind == "musicvideo":
        root_tag = "musicvideo"
    else:
        root_tag = kind
    escaped_title = xml.sax.saxutils.escape(title_text)
    escaped_full_id = xml.sax.saxutils.escape(full_id)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<{root}>\n'
        '  <title>{title}</title>\n'
        '  <uniqueid type="sudosync" default="true">{id}</uniqueid>\n'
        '</{root}>\n'
    ).format(root=root_tag, id=escaped_full_id, title=escaped_title)


def _verify_created_nfo_unmodified(entry, raw_bytes):
    """Verify that a newly created NFO file still matches the exact file generated by SudoSync."""
    if raw_bytes is None:
        return False, "file_modified", "Plik .nfo jest pusty lub nieczytelny; zachowano plik."
    if isinstance(raw_bytes, str):
        raw_bytes = raw_bytes.encode("utf-8")
    else:
        raw_bytes = bytes(raw_bytes)
    if not raw_bytes:
        return False, "file_modified", "Plik .nfo jest pusty; zachowano plik."

    expected_hash = entry.get("content_sha256")
    expected_id = entry.get("sudosync_id")
    if not expected_hash and not expected_id:
        return (
            False,
            "unverifiable_created_file",
            "Brak danych weryfikacyjnych w manifeście dla utworzonego pliku; zachowano plik.",
        )

    if expected_hash:
        actual_hash = hashlib.sha256(raw_bytes).hexdigest()
        if actual_hash != expected_hash:
            return (
                False,
                "file_modified",
                "Utworzony plik .nfo został zmodyfikowany po wygenerowaniu KodiBridge ID; zachowano plik.",
            )

    try:
        root = ET.fromstring(raw_bytes)
    except Exception:
        return (
            False,
            "file_modified",
            "Utworzony plik .nfo nie jest poprawnym dokumentem XML KodiBridge; zachowano plik.",
        )

    kind = entry.get("type")
    if kind == "movie" and root.tag != "movie":
        return False, "file_modified", "Zmieniono główny znacznik XML w utworzonym pliku .nfo; zachowano plik."
    if kind == "episode" and root.tag != "episodedetails":
        return False, "file_modified", "Zmieniono główny znacznik XML w utworzonym pliku .nfo; zachowano plik."
    if root.tag not in ("movie", "episodedetails"):
        return False, "file_modified", "Nieznany główny znacznik XML w utworzonym pliku .nfo; zachowano plik."

    children = list(root)
    if len(children) != 2 or [c.tag for c in children] != ["title", "uniqueid"]:
        return (
            False,
            "file_modified",
            "Struktura XML pliku .nfo zawiera dodatkowe lub zmienione pola; zachowano plik.",
        )

    uid_el = children[1]
    if uid_el.attrib.get("type") != "sudosync" or uid_el.attrib.get("default") != "true":
        return (
            False,
            "file_modified",
            "Znacznik uniqueid w pliku .nfo nie należy do operacji KodiBridge; zachowano plik.",
        )
    actual_id = (uid_el.text or "").strip()
    if not actual_id:
        return False, "file_modified", "Brak wartości KodiBridge ID w pliku .nfo; zachowano plik."
    if expected_id and actual_id != expected_id:
        return (
            False,
            "file_modified",
            "Wartość KodiBridge ID w pliku .nfo nie zgadza się z manifestem operacji; zachowano plik.",
        )

    if not expected_hash:
        inferred_kind = "movie" if root.tag == "movie" else "episode"
        expected_title = entry.get("title") if entry.get("title") is not None else (children[0].text or "")
        expected_xml = _build_created_nfo_xml(inferred_kind, expected_title, actual_id).encode("utf-8")
        if raw_bytes != expected_xml:
            return (
                False,
                "file_modified",
                "Zawartość utworzonego pliku .nfo odbiega od szablonu KodiBridge; zachowano plik.",
            )

    return True, "ok", ""


def _safe_restore_modified_nfo(bak_path, target_path):
    """Restore bak_path to target_path safely, protecting target_path from data loss."""
    try:
        os.replace(bak_path, target_path)
        return True
    except (OSError, IOError, ValueError):
        pass

    tmp_safety = target_path + ".rollback_tmp"
    if xbmcvfs.exists(tmp_safety):
        xbmcvfs.delete(tmp_safety)

    had_target = xbmcvfs.exists(target_path)
    moved_to_safety = False
    if had_target:
        moved_to_safety = xbmcvfs.rename(target_path, tmp_safety)
        if not moved_to_safety:
            raise IOError("Nie można przenieść pliku docelowego do tymczasowej kopii bezpieczeństwa: {}".format(target_path))

    try:
        success = xbmcvfs.rename(bak_path, target_path)
        if not success:
            raise IOError("Operacja xbmcvfs.rename nie powiodła się przy przywracaniu pliku z kopii zapasowej")
        if moved_to_safety and xbmcvfs.exists(tmp_safety):
            xbmcvfs.delete(tmp_safety)
        return True
    except Exception as exc:
        if moved_to_safety and xbmcvfs.exists(tmp_safety):
            xbmcvfs.rename(tmp_safety, target_path)
        raise exc


def rollback_sudosync_ids(manifest_path=None):
    if not manifest_path:
        manifest_path = os.path.join(_addon_profile_path(), "assign_ids_manifest.json")
    if not xbmcvfs.exists(manifest_path):
        raise RuntimeError("Brak pliku manifestu: {}".format(manifest_path))
    manifest = _read_local_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("modified"), list):
        raise RuntimeError("Nieprawidłowy format pliku manifestu: {}".format(manifest_path))

    rollbacks = 0
    restored = 0
    deleted = 0
    skipped_modified = 0
    already = 0
    errors = 0
    error_details = []

    def _mark_done(entry):
        # Progress is saved after every file, so an interrupted rollback can
        # be started again without reporting finished files as errors.
        entry["rolled_back"] = True
        try:
            _write_local_json(manifest_path, manifest)
        except Exception as exc:
            log("Cannot record rollback progress in {}: {}".format(manifest_path, exc), xbmc.LOGWARNING)

    def _record_error(file_path, reason, message):
        error_details.append({"file": file_path, "reason": reason, "message": message})
        log("Rollback SudoSync ID error for {}: {} ({})".format(file_path, message, reason), xbmc.LOGWARNING)

    for entry in manifest.get("modified", []):
        if not isinstance(entry, dict):
            errors += 1
            _record_error(None, "invalid_entry", "Nieprawidłowy wpis w manifeście wycofywania.")
            continue

        best_path = entry.get("file")
        bak_path = entry.get("backup")
        action = entry.get("action")
        is_created = bool(action == "created" or entry.get("created") is True or (action is None and bak_path is None))

        if not best_path:
            errors += 1
            _record_error(None, "missing_file_path", "Wpis manifestu nie zawiera ścieżki pliku .nfo.")
            continue

        if entry.get("rolled_back"):
            already += 1
            continue

        state = entry.get("state")
        if state in ("pending", "failed") and bak_path:
            # Repair interrupted between its steps (see repair_duplicate_sudosync_ids).
            target_exists = xbmcvfs.exists(best_path)
            backup_exists = xbmcvfs.exists(bak_path)
            if not target_exists and backup_exists:
                try:
                    if not xbmcvfs.rename(bak_path, best_path):
                        raise IOError("rename failed")
                    rollbacks += 1
                    restored += 1
                    _mark_done(entry)
                except Exception as exc:
                    errors += 1
                    _record_error(best_path, "restore_failed",
                                  "Nie udało się przywrócić pliku .nfo z kopii {}: {}".format(bak_path, exc))
                continue
            if not backup_exists:
                # The original was never moved: nothing to undo.
                already += 1
                _mark_done(entry)
                continue

        if is_created:
            if not xbmcvfs.exists(best_path):
                errors += 1
                _record_error(best_path, "missing_created_file", "Utworzony plik .nfo już nie istnieje.")
                continue
            try:
                raw_bytes = _read_vfs_all(best_path)
            except Exception as exc:
                errors += 1
                _record_error(best_path, "read_failed", "Nie udało się odczytać utworzonego pliku .nfo: {}".format(exc))
                continue

            verified, reason, msg = _verify_created_nfo_unmodified(entry, raw_bytes)
            if not verified:
                errors += 1
                if reason == "file_modified":
                    skipped_modified += 1
                _record_error(best_path, reason, msg)
                continue

            if xbmcvfs.delete(best_path):
                rollbacks += 1
                deleted += 1
                _mark_done(entry)
            else:
                errors += 1
                _record_error(best_path, "delete_failed", "Nie udało się usunąć utworzonego pliku .nfo.")
        else:
            if not bak_path or not xbmcvfs.exists(bak_path):
                errors += 1
                _record_error(
                    best_path,
                    "missing_backup",
                    "Brak pliku kopii zapasowej (.bak); pozostawiono bieżący plik .nfo bez zmian.",
                )
                continue

            if not xbmcvfs.exists(best_path):
                errors += 1
                _record_error(
                    best_path,
                    "missing_target_file",
                    "Docelowy plik .nfo nie istnieje; zachowano plik kopii zapasowej (.bak).",
                )
                continue

            expected_hash = entry.get("content_sha256")
            if not expected_hash:
                errors += 1
                _record_error(
                    best_path,
                    "unverifiable_modified_file",
                    "Brak sumy kontrolnej w manifeście dla zmodyfikowanego pliku; zachowano plik .nfo i kopię .bak.",
                )
                continue

            try:
                raw_bytes = _read_vfs_all(best_path)
            except Exception as exc:
                errors += 1
                _record_error(
                    best_path,
                    "read_failed",
                    "Nie udało się odczytać bieżącego pliku .nfo: {}".format(exc),
                )
                continue

            actual_hash = hashlib.sha256(raw_bytes).hexdigest()
            if actual_hash != expected_hash:
                errors += 1
                skipped_modified += 1
                _record_error(
                    best_path,
                    "file_modified",
                    "Plik .nfo został zmodyfikowany po przypisaniu KodiBridge ID; zachowano plik .nfo i kopię .bak.",
                )
                continue

            try:
                _safe_restore_modified_nfo(bak_path, best_path)
                rollbacks += 1
                restored += 1
                _mark_done(entry)
            except Exception as exc:
                errors += 1
                _record_error(
                    best_path,
                    "restore_failed",
                    "Nie udało się przywrócić kopii zapasowej .bak do pliku .nfo: {}".format(exc),
                )

    _NFO_ID_CACHE.clear()
    entries = [entry for entry in manifest.get("modified", []) if isinstance(entry, dict)]
    if entries and all(entry.get("rolled_back") for entry in entries):
        manifest["rolled_back_at"] = utc_now_precise()
        try:
            _write_local_json(manifest_path, manifest)
        except Exception as exc:
            log("Cannot mark {} as rolled back: {}".format(manifest_path, exc), xbmc.LOGWARNING)
    return {
        "rolled_back": rollbacks,
        "restored": restored,
        "deleted": deleted,
        "already_rolled_back": already,
        "skipped_modified": skipped_modified,
        "errors": errors,
        "error_details": error_details,
    }


# ---------------------------------------------------------------------------
# Repair of duplicate KodiBridge IDs
# ---------------------------------------------------------------------------

ASSIGN_IDS_MANIFEST_NAME = "assign_ids_manifest.json"
REPAIR_IDS_MANIFEST_NAME = "repair_ids_manifest.json"
REPAIR_BACKUP_SUFFIX = ".repair.bak"
NFO_ID_EPOCH_FILE_NAME = "nfo_id_epoch.txt"
# system/config.json keys: a new value of the epoch tells every Kodi that
# KodiBridge IDs in .nfo files were changed or removed (not only added), so the
# cached identifiers must be read again; the map lets the sync engine accept an
# item whose KodiBridge ID was replaced by the duplicate repair.
SHARED_NFO_ID_EPOCH_KEY = "nfo_id_epoch"
SHARED_RENUMBERED_IDS_KEY = "kodibridge_id_renumbered"
MAX_SHARED_RENUMBERED_IDS = 5000
_EXTERNAL_ID_KINDS = ("imdb", "tmdb", "tvdb", "trakt")
_SUDOSYNC_UNIQUEID_RE = re.compile(
    rb"(<uniqueid\b[^>]*?\btype\s*=\s*[\"']sudosync[\"'][^>]*>)(\s*)([^<]*?)(\s*)(</uniqueid>)",
    re.IGNORECASE,
)
_XML_COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
_SEEN_NFO_ID_EPOCH = {"value": None}


def _sid_key(value):
    """Comparable KodiBridge ID: lower case, without a ':sNNeNN' episode qualifier."""
    return str(value or "").strip().lower().split(":", 1)[0]


def _effective_sudosync_id(record):
    """KodiBridge ID as the sync engine sees it: the .nfo value wins over the Kodi database."""
    nfo_ids = _NFO_ID_CACHE.get(record.get("file") or "") or {}
    value = nfo_ids.get("sudosync") or (record.get("ids") or {}).get("sudosync")
    return str(value).strip() if value else ""


def _record_label(record):
    if record.get("type") == "episode":
        try:
            return "{} S{:02d}E{:02d}".format(
                record.get("showtitle") or record.get("title") or "?",
                int(record.get("season") or 0),
                int(record.get("episode") or 0),
            )
        except (TypeError, ValueError):
            pass
    title = record.get("title") or record.get("originaltitle") or ""
    if record.get("year"):
        title = "{} ({})".format(title, record.get("year"))
    return title or os.path.basename(str(record.get("file") or "")) or "?"


def find_duplicate_sudosync_id_groups(movies, episodes):
    """Return the KodiBridge IDs used by more than one media file.

    Library items of one media file (episodes of a multi-episode file) share
    one .nfo and one ID; that is not a duplicate. Each group lists its files
    sorted by path: {"sudosync_id", "files": [{"file", "type", "sudosync_id",
    "records"}]}.
    """
    groups = {}
    for kind, collection in (("movie", movies), ("episode", episodes)):
        for record in collection or []:
            if not isinstance(record, dict) or not record.get("file"):
                continue
            sid = _effective_sudosync_id(record)
            if not sid:
                continue
            group = groups.setdefault(_sid_key(sid), {"sudosync_id": sid, "files": {}})
            # One physical file may be listed under paths differing only in
            # letter case (series scanned twice on a case-insensitive share):
            # it has one .nfo, so it is not a duplicate ID.
            entry = group["files"].setdefault(
                _same_file_key(record["file"]),
                {"file": record["file"], "type": kind, "sudosync_id": sid, "records": []},
            )
            entry["records"].append(record)
    result = []
    for key in sorted(groups):
        group = groups[key]
        if len(group["files"]) < 2:
            continue
        files = sorted(group["files"].values(), key=lambda entry: entry["file"].lower())
        result.append({"sudosync_id": group["sudosync_id"], "files": files})
    return result


def _same_file_key(path):
    return str(path or "").replace("\\", "/").casefold()


def find_repeated_library_files(movies, episodes):
    """Media files listed in the library under several paths differing only in letter case.

    Returns ["label: path1 | path2", ...]; such entries are fixed by removing
    the extra entry from the Kodi library, not by changing KodiBridge IDs.
    """
    spellings = {}
    for collection in (movies, episodes):
        for record in collection or []:
            if isinstance(record, dict) and record.get("file"):
                paths = spellings.setdefault(_same_file_key(record["file"]), {})
                paths.setdefault(record["file"], _record_label(record))
    found = []
    for key in sorted(spellings):
        paths = spellings[key]
        if len(paths) > 1:
            found.append("{}: {}".format(next(iter(paths.values())), " | ".join(sorted(paths))))
    return found


def _load_network_snapshot_ids(base_path, exclude_client_id=None):
    """(snapshot file, media type, ids) of the records in other clients' snapshots on the NAS."""
    found = []
    own_file = "client-{}.json".format(exclude_client_id).lower() if exclude_client_id else None
    base = str(base_path or "").strip()
    if not base:
        return found
    dir_path = _join_vfs(base.rstrip("/") + "/", "clients")
    try:
        if not _vfs_exists(dir_path):
            return found
        _dirs, files = _vfs_listdir(dir_path)
    except Exception as exc:
        log("Cannot list client snapshots for the duplicate repair: {}".format(exc), xbmc.LOGWARNING)
        return found
    for filename in files or []:
        if not isinstance(filename, str) or not filename.lower().endswith(".json"):
            continue
        if own_file and filename.lower() == own_file:
            continue
        try:
            snap = _read_vfs_json(dir_path.rstrip("/") + "/" + filename)
        except Exception:
            continue
        if not isinstance(snap, dict):
            continue
        for key, media_type in (("movies", "movie"), ("episodes", "episode")):
            for rec in snap.get(key) or []:
                if isinstance(rec, dict) and isinstance(rec.get("ids"), dict):
                    found.append((filename, media_type, rec["ids"]))
    return found


def _network_holders(entry, snapshot_ids):
    """Number of other Kodi devices whose snapshot keeps this file's item under its KodiBridge ID.

    Matched by an external ID (IMDb/TMDb/TVDB/Trakt) of the item; such an item
    keeps its number, because the synchronized history belongs to it.
    """
    external = {}
    for record in entry["records"]:
        ids = record.get("ids") if isinstance(record.get("ids"), dict) else {}
        for kind in _EXTERNAL_ID_KINDS:
            if ids.get(kind):
                external.setdefault(kind, set()).add(str(ids[kind]).strip().lower())
    if not external:
        return 0
    key = _sid_key(entry["sudosync_id"])
    holders = set()
    for snapshot_file, media_type, ids in snapshot_ids:
        if media_type != entry["type"] or _sid_key(ids.get("sudosync")) != key:
            continue
        for kind, values in external.items():
            if str(ids.get(kind) or "").strip().lower() in values:
                holders.add(snapshot_file)
                break
    return len(holders)


def _replace_sudosync_id_in_nfo(raw, old_id, new_id):
    """Replace the KodiBridge ID value old_id with new_id; return (bytes, count)."""
    import xml.sax.saxutils

    old_key = str(old_id or "").strip().lower()
    new_value = xml.sax.saxutils.escape(str(new_id)).encode("utf-8")
    count = [0]

    def _repl(match):
        if match.group(3).decode("utf-8", "replace").strip().lower() != old_key:
            return match.group(0)
        count[0] += 1
        return match.group(1) + match.group(2) + new_value + match.group(4) + match.group(5)

    raw = bytes(raw)
    comments = [(m.start(), m.end()) for m in _XML_COMMENT_RE.finditer(raw)]

    def _repl_outside_comments(match):
        if any(start <= match.start() < end for start, end in comments):
            return match.group(0)
        return _repl(match)

    return _SUDOSYNC_UNIQUEID_RE.sub(_repl_outside_comments, raw), count[0]


def _nfo_id_epoch_path():
    return os.path.join(_addon_profile_path(), NFO_ID_EPOCH_FILE_NAME)


def _read_local_nfo_id_epoch():
    try:
        with open(_nfo_id_epoch_path(), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except Exception:
        return ""


def _remember_nfo_id_epoch(epoch):
    _SEEN_NFO_ID_EPOCH["value"] = epoch
    try:
        with open(_nfo_id_epoch_path(), "w", encoding="utf-8") as handle:
            handle.write(epoch)
    except Exception as exc:
        log("Cannot store the KodiBridge ID change marker: {}".format(exc), xbmc.LOGWARNING)


def publish_kodibridge_id_change(base_path=None, renumbered=None):
    """Tell every Kodi that KodiBridge IDs in .nfo files changed or were removed.

    Bumps the epoch in system/config.json (each service then drops its cached
    .nfo identifiers once) and records renumbered pairs (old ID, new ID).
    """
    base = base_path if base_path is not None else (addon_settings().get("base_path") or "")
    if not str(base or "").strip():
        return ""
    epoch = "{}-{}".format(utc_now_precise(), uuid.uuid4().hex[:8])
    pairs = [(_sid_key(old), _sid_key(new)) for old, new in (renumbered or []) if old and new]

    def _mutator(shared):
        shared[SHARED_NFO_ID_EPOCH_KEY] = epoch
        if pairs:
            mapping = shared.get(SHARED_RENUMBERED_IDS_KEY)
            mapping = dict(mapping) if isinstance(mapping, dict) else {}
            for old, new in pairs:
                targets = mapping.pop(old, None)
                targets = list(targets) if isinstance(targets, list) else []
                if new not in targets:
                    targets.append(new)
                mapping[old] = targets  # re-inserted last: oldest entries are dropped first
            while len(mapping) > MAX_SHARED_RENUMBERED_IDS:
                mapping.pop(next(iter(mapping)))
            shared[SHARED_RENUMBERED_IDS_KEY] = mapping
        return True

    # Stored locally first: the service of this Kodi may read system/config.json
    # at any moment and must not take its own change for another Kodi's one.
    previous = _read_local_nfo_id_epoch()
    _remember_nfo_id_epoch(epoch)
    try:
        update_shared_config(_mutator, base)
    except Exception:
        _remember_nfo_id_epoch(previous)
        raise
    return epoch


def take_nfo_id_epoch_change(shared):
    """Return True once per new KodiBridge ID change marker found in system/config.json."""
    epoch = shared.get(SHARED_NFO_ID_EPOCH_KEY) if isinstance(shared, dict) else None
    if not epoch:
        return False
    epoch = str(epoch)
    if _SEEN_NFO_ID_EPOCH["value"] == epoch:
        return False
    local = _read_local_nfo_id_epoch()
    _remember_nfo_id_epoch(epoch)
    return local != epoch


def renumbered_sudosync_ids(shared):
    """Old KodiBridge ID -> new IDs from system/config.json (for the sync engine)."""
    mapping = shared.get(SHARED_RENUMBERED_IDS_KEY) if isinstance(shared, dict) else None
    return mapping if isinstance(mapping, dict) else {}


def latest_rollback_manifests():
    """Existing rollback manifests, newest first: [(kind, path, generated_at)]."""
    found = []
    for kind, name in (("assign", ASSIGN_IDS_MANIFEST_NAME), ("repair", REPAIR_IDS_MANIFEST_NAME)):
        path = os.path.join(_addon_profile_path(), name)
        if not xbmcvfs.exists(path):
            continue
        try:
            data = _read_local_json(path)
        except Exception:
            data = None
        if not isinstance(data, dict) or data.get("rolled_back_at"):
            continue
        stamp = str(data.get("generated_at") or "")
        found.append((kind, path, stamp))
    found.sort(key=lambda item: item[2], reverse=True)
    return found


def renumbered_pairs_from_manifest(manifest_path):
    """(old ID, new ID) pairs recorded by the duplicate repair in a manifest."""
    try:
        data = _read_local_json(manifest_path)
    except Exception:
        return []
    pairs = []
    for entry in (data.get("modified") or []) if isinstance(data, dict) else []:
        if isinstance(entry, dict) and entry.get("old_sudosync_id") and entry.get("sudosync_id"):
            pairs.append((entry["old_sudosync_id"], entry["sudosync_id"]))
    return pairs


def repair_duplicate_sudosync_ids(progress_callback=None, is_canceled=None, base_path=None, collected_items=None):
    """Give a new KodiBridge ID to every extra media file that shares one ID.

    In each group one file keeps the number: the one a client snapshot on the
    NAS knows under this ID (by an external ID), otherwise the first by path.
    The other .nfo files get the next numbers of the network counter, reserved
    before each change. Every changed .nfo is backed up as .repair.bak and
    listed in repair_ids_manifest.json, so the change can be rolled back.
    """
    settings = addon_settings()
    if not bool(settings.get("sudosync_id_enabled") or settings.get("enable_sudosync_id")):
        raise RuntimeError("Generowanie KodiBridge ID jest wyłączone w ustawieniach.")
    base = base_path if base_path is not None else (settings.get("base_path") or "")
    result = {
        "busy": False,
        "groups": 0,
        "renumbered": 0,
        "changes": [],
        "kept": [],
        "errors": [],
        "manifest_path": "",
        "last_number": 0,
        "cancelled": False,
        "fatal_error": "",
    }
    if not _acquire_assign_ids_lock(base_path=base, auto_mode=False):
        result["busy"] = True
        return result
    try:
        if collected_items is not None:
            movies, episodes = collected_items
        else:
            movies, episodes = collect_library(first_import=True, is_canceled=is_canceled)
        if is_canceled and is_canceled():
            result["cancelled"] = True
            return result
        groups = find_duplicate_sudosync_id_groups(movies, episodes)
        result["groups"] = len(groups)
        result["repeated"] = find_repeated_library_files(movies, episodes)
        if not groups:
            return result

        snapshot_ids = _load_network_snapshot_ids(base, exclude_client_id=get_or_create_client_id())
        prefix = get_or_sync_network_id_and_alias(base)
        last_number = get_network_sudosync_id_last_number(base, list(movies) + list(episodes))
        manifest_path = os.path.join(_addon_profile_path(), REPAIR_IDS_MANIFEST_NAME)
        generated_at = utc_now_precise()
        manifest = {
            "operation": "repair_duplicate_ids",
            "generated_at": generated_at,
            "groups": len(groups),
            "last_number": last_number,
            "kept": [],
            "modified": [],
            "errors": [],
            "cancelled": False,
            "fatal_error": "",
        }
        result["manifest_path"] = manifest_path
        stamp = re.sub(r"[^0-9]", "", generated_at)[:14] or uuid.uuid4().hex[:12]

        def _write_manifest():
            manifest["last_number"] = last_number
            try:
                _write_local_json(manifest_path, manifest)
                return True
            except Exception as exc:
                log("Cannot write {}: {}".format(REPAIR_IDS_MANIFEST_NAME, exc), xbmc.LOGWARNING)
                return False

        def _error(entry, label, message):
            manifest["errors"].append({"file": entry["file"], "title": label, "error": message})
            result["errors"].append("{}: {}".format(label, message))
            log("Duplicate KodiBridge ID repair skipped '{}': {}".format(entry["file"], message), xbmc.LOGWARNING)

        def _fatal(entry, label, message):
            result["fatal_error"] = manifest["fatal_error"] = message
            _error(entry, label, message)

        def _backup_path(best_path):
            # Never overwrite an earlier backup (an earlier repair or assignment).
            candidate = best_path + REPAIR_BACKUP_SUFFIX
            if xbmcvfs.exists(candidate):
                candidate = "{}.repair-{}.bak".format(best_path, stamp)
            return candidate

        if not _write_manifest():
            raise RuntimeError("Nie można zapisać raportu {}; naprawa nie została rozpoczęta.".format(REPAIR_IDS_MANIFEST_NAME))

        total = sum(len(group["files"]) - 1 for group in groups)
        done = 0
        stop = False
        for group in groups:
            if stop:
                break
            files = group["files"]
            holders = [_network_holders(entry, snapshot_ids) for entry in files]
            best = max(holders)
            keeper = files[holders.index(best)]  # files are sorted by path: first wins a tie
            known_elsewhere = sum(1 for count in holders if count)
            keeper_label = _record_label(keeper["records"][0])
            manifest["kept"].append({
                "file": keeper["file"],
                "title": keeper_label,
                "sudosync_id": keeper["sudosync_id"],
                "known_on_network": bool(best),
                "ambiguous": known_elsewhere > 1,
            })
            note = ""
            if known_elsewhere > 1:
                note = " (UWAGA: inne Kodi synchronizują pod tym ID {} różne pozycje; numer zachowała ta, którą zna najwięcej urządzeń)".format(known_elsewhere)
            result["kept"].append("{} — {}{}".format(keeper["sudosync_id"], keeper_label, note))
            for entry in files:
                if entry is keeper:
                    continue
                if is_canceled and is_canceled():
                    result["cancelled"] = manifest["cancelled"] = True
                    stop = True
                    break
                label = _record_label(entry["records"][0])
                done += 1
                if progress_callback:
                    progress_callback(done, total, label)
                old_id = entry["sudosync_id"]
                best_path, _ids, _text = _find_best_nfo(entry["file"], entry["type"])
                if not best_path or not xbmcvfs.exists(best_path):
                    _error(entry, label, "brak pliku .nfo")
                    continue
                _touch_assign_ids_lock(base)
                try:
                    raw = _read_vfs_all(best_path)
                    raw = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw or b"")
                except Exception as exc:
                    _error(entry, label, "nie można odczytać .nfo: {}".format(exc))
                    continue
                if _replace_sudosync_id_in_nfo(raw, old_id, old_id)[1] == 0:
                    _error(entry, label, "plik .nfo nie zawiera ID {} (ID pochodzi z bazy Kodi)".format(old_id))
                    continue

                candidate = last_number + 1
                try:
                    persisted = persist_network_sudosync_id_last_number(candidate, base)
                    if not persisted or persisted < candidate:
                        raise IOError("Rezerwacja licznika zwróciła nieprawidłową wartość: {}".format(persisted))
                    candidate = persisted
                except Exception as exc:
                    _fatal(entry, label, "rezerwacja licznika nie powiodła się: {}".format(exc))
                    stop = True
                    break
                last_number = candidate

                new_id = "{}-{:06d}".format(prefix, candidate)
                new_raw, _count = _replace_sudosync_id_in_nfo(raw, old_id, new_id)
                bak_path = _backup_path(best_path)
                new_path = best_path + ".new"
                record = {
                    "file": best_path,
                    "backup": bak_path,
                    "action": "modified",
                    "created": False,
                    "state": "pending",
                    "type": entry["type"],
                    "sudosync_id": new_id,
                    "old_sudosync_id": old_id,
                    "content_sha256": hashlib.sha256(new_raw).hexdigest(),
                    "original_sha256": hashlib.sha256(raw).hexdigest(),
                    "title": label,
                    "media_file": entry["file"],
                }
                # The rollback record must be on disk before the .nfo changes.
                manifest["modified"].append(record)
                if not _write_manifest():
                    manifest["modified"].remove(record)
                    _fatal(entry, label, "nie można zapisać raportu naprawy; przerwano bez zmiany pliku")
                    stop = True
                    break

                moved_original = False
                changed = False
                failure = ""
                try:
                    _write_vfs_bytes(new_path, new_raw)
                    current = _read_vfs_all(best_path)
                    current = current.encode("utf-8") if isinstance(current, str) else bytes(current or b"")
                    if current != raw:
                        failure = "plik .nfo zmienił się w trakcie naprawy; pozostawiono go bez zmian"
                    else:
                        moved_original = bool(xbmcvfs.rename(best_path, bak_path))
                        if not moved_original:
                            failure = "nie można utworzyć kopii zapasowej"
                        elif xbmcvfs.rename(new_path, best_path):
                            changed = True
                        else:
                            failure = "nie można podmienić pliku .nfo"
                except Exception as exc:
                    failure = "błąd zapisu .nfo: {}".format(exc)
                if not changed:
                    try:
                        if moved_original and not xbmcvfs.exists(best_path):
                            xbmcvfs.rename(bak_path, best_path)
                        if xbmcvfs.exists(new_path):
                            xbmcvfs.delete(new_path)
                    except Exception as exc:
                        failure += "; przywracanie kopii nie powiodło się: {} (kopia: {})".format(exc, bak_path)
                    if xbmcvfs.exists(best_path):
                        manifest["modified"].remove(record)
                    else:
                        record["state"] = "failed"
                    _error(entry, label, "{} (numer {} pozostaje niewykorzystany)".format(failure, new_id))
                    _write_manifest()
                    continue

                record["state"] = "done"
                for library_record in entry["records"]:
                    library_record.setdefault("ids", {})["sudosync"] = new_id
                    try:
                        if entry["type"] == "movie":
                            rpc("VideoLibrary.RefreshMovie", {"movieid": library_record.get("local", {}).get("movieid")})
                        else:
                            rpc("VideoLibrary.RefreshEpisode", {"episodeid": library_record.get("local", {}).get("episodeid")})
                    except Exception:
                        pass
                result["changes"].append("{} → {} — {}".format(old_id, new_id, label))
                result["renumbered"] += 1
                _write_manifest()

        _NFO_ID_CACHE.clear()
        result["last_number"] = last_number
        _write_manifest()
        pairs = [(item["old_sudosync_id"], item["sudosync_id"])
                 for item in manifest["modified"] if item.get("state") == "done"]
        if pairs:
            try:
                publish_kodibridge_id_change(base, pairs)
            except Exception as exc:
                msg = "Nie udało się ogłosić zmiany ID innym Kodi: {}".format(exc)
                result["errors"].append(msg)
                log(msg, xbmc.LOGWARNING)
        log("Duplicate KodiBridge ID repair: groups={}, renumbered={}, errors={}".format(
            len(groups), result["renumbered"], len(result["errors"])), xbmc.LOGINFO)
        return result
    finally:
        _release_assign_ids_lock(base_path=base)


class _PreviewResult(list):
    def __init__(self, *args, **kwargs):
        super(_PreviewResult, self).__init__(*args, **kwargs)
        self.collected_items = None


def preview_sudosync_ids(progress_callback=None, is_canceled=None):
    settings = addon_settings()
    if not settings.get("sudosync_id_enabled"):
        raise RuntimeError("Generowanie KodiBridge ID jest wylaczone w ustawieniach.")
    _NFO_ID_CACHE.clear()
    if is_canceled and is_canceled():
        res = _PreviewResult([])
        res.collected_items = ([], [])
        return res

    movies, episodes = collect_library(first_import=False, is_canceled=is_canceled)
    if is_canceled and is_canceled():
        res = _PreviewResult([])
        res.collected_items = (movies, episodes)
        return res

    preview_list = []
    total_items = len(movies) + len(episodes)
    inspected = 0

    def _check_item(item, kind):
        ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
        if _has_sudosync_id(ids):
            return False
        file_path = item.get("file")
        if not file_path:
            return False
        best_path, nfo_ids, _ = _find_best_nfo(file_path, kind)
        if isinstance(nfo_ids, dict) and _has_sudosync_id(nfo_ids):
            return False
        if not best_path:
            return False
        if xbmcvfs.exists(best_path):
            try:
                raw = _read_vfs_all(best_path)
                if not raw:
                    return False
                has_sudosync, _ = _nfo_raw_has_sudosync_id(raw, kind)
                if has_sudosync:
                    return False
                closing_tags = (b"</movie>", b"</episodedetails>", b"</musicvideo>")
                raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                raw_lower = raw_bytes.lower()
                if not any(tag in raw_lower for tag in closing_tags):
                    return False
                return best_path
            except Exception:
                return False
        else:
            return best_path

    for m in movies:
        if is_canceled and is_canceled():
            break
        path = _check_item(m, "movie")
        if path:
            preview_list.append({"title": m.get("title") or m.get("originaltitle"), "path": path, "type": "movie"})
        inspected += 1
        if progress_callback:
            progress_callback(inspected, total_items, m.get("title") or m.get("originaltitle") or "")

    if not (is_canceled and is_canceled()):
        for e in episodes:
            if is_canceled and is_canceled():
                break
            path = _check_item(e, "episode")
            if path:
                preview_list.append({"title": e.get("title") or e.get("originaltitle"), "path": path, "type": "episode"})
            inspected += 1
            if progress_callback:
                progress_callback(inspected, total_items, e.get("title") or e.get("originaltitle") or "")

    result = _PreviewResult(preview_list)
    result.collected_items = (movies, episodes)
    return result


_ASSIGN_IDS_LOCK_HELD = False


def _get_localized_string(string_id, fallback=""):
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        val = addon.getLocalizedString(string_id)
        if isinstance(val, str) and val.strip():
            return val
    except Exception:
        pass
    return fallback


def _remove_assign_ids_lock_file(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
    try:
        if xbmcvfs.exists(path):
            xbmcvfs.delete(path)
    except Exception:
        pass


def _acquire_assign_ids_lock(base_path=None, auto_mode=False, s=None):
    """Acquire local and optional NAS concurrency lock for assign_sudosync_ids.

    Uses os.O_CREAT | os.O_EXCL with orphan verification (1800s TTL).
    Returns True if acquired, False otherwise.
    """
    global _ASSIGN_IDS_LOCK_HELD
    if _ASSIGN_IDS_LOCK_HELD:
        return False

    base = base_path if base_path is not None else s
    if base is None:
        try:
            base = addon_settings().get("base_path") or ""
        except Exception:
            base = ""
    base = str(base or "").strip()

    now = datetime.now(timezone.utc).timestamp()
    local_path = os.path.join(_addon_profile_path(), "assign_ids.lock")

    # Check local lock file for active or orphaned state
    local_data = _read_local_json(local_path)
    if isinstance(local_data, dict) and local_data.get("locked"):
        lock_ts = float(local_data.get("timestamp") or 0.0)
        if lock_ts > 0.0 and (now - lock_ts > 1800.0):
            log("Znaleziono osieroconą blokadę assign_ids.lock (wiek > 1800s). Usuwanie...", xbmc.LOGWARNING)
            _remove_assign_ids_lock_file(local_path)
        elif lock_ts > 0.0:
            return False

    lock_payload = {
        "locked": True,
        "timestamp": now,
        "pid": os.getpid(),
        "client_id": get_or_create_client_id(),
    }

    # Attempt atomic creation via os.O_CREAT | os.O_EXCL if real filesystem directory
    acquired_via_os = False
    try:
        dirname = os.path.dirname(local_path)
        if dirname and os.path.exists(dirname):
            fd = os.open(local_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            try:
                payload_str = json.dumps(lock_payload, indent=2) + "\n"
                os.write(fd, payload_str.encode("utf-8"))
            finally:
                os.close(fd)
            acquired_via_os = True
    except FileExistsError:
        return False
    except (OSError, IOError):
        pass

    if not acquired_via_os:
        # Fallback to _write_local_json for virtual / mock environments
        existing = _read_local_json(local_path)
        if isinstance(existing, dict) and existing.get("locked"):
            return False
        _write_local_json(local_path, lock_payload)

    # Optional NAS lock in system/ directory
    if base:
        base_norm = base.rstrip("/") + "/"
        nas_lock = _join_vfs(base_norm, "system").rstrip("/") + "/assign_ids.lock"
        try:
            if xbmcvfs.exists(nas_lock):
                nas_data = _read_vfs_json(nas_lock)
                if isinstance(nas_data, dict) and nas_data.get("locked"):
                    nas_ts = float(nas_data.get("timestamp") or 0.0)
                    if nas_ts > 0.0 and (now - nas_ts > 1800.0):
                        log("Znaleziono osieroconą blokadę NAS assign_ids.lock (wiek > 1800s). Usuwanie...", xbmc.LOGWARNING)
                        try:
                            xbmcvfs.delete(nas_lock)
                        except Exception:
                            pass
                    elif nas_ts > 0.0:
                        _release_assign_ids_lock(base_path=base)
                        return False
            _write_vfs_json(nas_lock, lock_payload)
        except Exception as exc:
            log("Ostrzeżenie przy zapisie blokady NAS assign_ids.lock: {}".format(exc), xbmc.LOGWARNING)

    _ASSIGN_IDS_LOCK_HELD = True
    return True


def _release_assign_ids_lock(base_path=None, s=None):
    """Release local and optional NAS concurrency lock for assign_sudosync_ids."""
    global _ASSIGN_IDS_LOCK_HELD
    _ASSIGN_IDS_LOCK_HELD = False

    base = base_path if base_path is not None else s
    if base is None:
        try:
            base = addon_settings().get("base_path") or ""
        except Exception:
            base = ""
    base = str(base or "").strip()

    local_path = os.path.join(_addon_profile_path(), "assign_ids.lock")
    try:
        _write_local_json(local_path, {"locked": False, "timestamp": 0.0})
    except Exception:
        pass
    _remove_assign_ids_lock_file(local_path)

    if base:
        base_norm = base.rstrip("/") + "/"
        nas_lock = _join_vfs(base_norm, "system").rstrip("/") + "/assign_ids.lock"
        try:
            if xbmcvfs.exists(nas_lock):
                xbmcvfs.delete(nas_lock)
        except Exception:
            pass


def _touch_assign_ids_lock(base_path=None, s=None):
    """Refresh the timestamp of the assign_sudosync_ids lock to avoid 1800s timeout during long scans."""
    now = datetime.now(timezone.utc).timestamp()
    local_path = os.path.join(_addon_profile_path(), "assign_ids.lock")
    try:
        data = _read_local_json(local_path)
        if isinstance(data, dict) and data.get("locked"):
            data["timestamp"] = now
            _write_local_json(local_path, data)
    except Exception:
        pass

    base = base_path if base_path is not None else s
    if base is None:
        try:
            base = addon_settings().get("base_path") or ""
        except Exception:
            base = ""
    base = str(base or "").strip()
    if base:
        base_norm = base.rstrip("/") + "/"
        nas_lock = _join_vfs(base_norm, "system").rstrip("/") + "/assign_ids.lock"
        try:
            if xbmcvfs.exists(nas_lock):
                nas_data = _read_vfs_json(nas_lock)
                if isinstance(nas_data, dict) and nas_data.get("locked"):
                    nas_data["timestamp"] = now
                    _write_vfs_json(nas_lock, nas_data)
        except Exception:
            pass


def assign_sudosync_ids(show_notification=True, auto_mode=False, collected_items=None, base_path=None, s=None, progress_callback=None, is_canceled=None, silent=False):
    settings = addon_settings()
    id_enabled = bool(settings.get("sudosync_id_enabled") or settings.get("enable_sudosync_id"))
    if not id_enabled:
        if silent:
            log("Generowanie SudoSync ID jest wyłączone w ustawieniach (silent=True).", xbmc.LOGINFO)
            return {
                "movies": 0,
                "episodes": 0,
                "refreshed_existing": 0,
                "last_number": 0,
                "manifest_path": "",
                "cancelled": False,
                "fatal_error": "",
            }
        raise RuntimeError("Generowanie KodiBridge ID jest wylaczone w ustawieniach.")

    base = base_path if base_path is not None else s
    if base is None:
        base = settings.get("base_path") or ""

    if not _acquire_assign_ids_lock(base_path=base, auto_mode=auto_mode):
        if auto_mode or silent:
            log("Przypisywanie SudoSync ID w tle pominięte: inna operacja przypisywania jest w toku.", xbmc.LOGINFO)
            return 0
        else:
            log("Ręczne przypisywanie SudoSync ID zablokowane: inna operacja przypisywania jest w toku.", xbmc.LOGWARNING)
            if not silent and show_notification:
                try:
                    msg = _get_localized_string(32079, "Przypisywanie KodiBridge ID jest już wykonywane w tle. Poczekaj na zakończenie.")
                    xbmcgui.Dialog().notification("KodiBridge", msg, xbmcgui.NOTIFICATION_WARNING, 5000)
                except Exception:
                    pass
            return 0

    try:
        if is_canceled and is_canceled():
            return {
                "movies": 0,
                "episodes": 0,
                "refreshed_existing": 0,
                "last_number": 0,
                "manifest_path": "",
                "cancelled": True,
            }

        if collected_items is not None:
            movies, episodes = collected_items
        else:
            movies, episodes = collect_library(first_import=False, is_canceled=is_canceled)

        if is_canceled and is_canceled():
            return {
                "movies": 0,
                "episodes": 0,
                "refreshed_existing": 0,
                "last_number": 0,
                "manifest_path": "",
                "cancelled": True,
            }

        prefix = get_or_sync_network_id_and_alias(settings.get("base_path"))

        modified_movies = 0
        modified_episodes = 0
        refreshed_existing = 0
        fatal_errors = []

        all_items = list(movies) + list(episodes)
        last_number = get_network_sudosync_id_last_number(settings.get("base_path"), all_items)

        def _refresh_item(item, kind):
            try:
                if kind == "movie":
                    rpc("VideoLibrary.RefreshMovie", {"movieid": item.get("local", {}).get("movieid")})
                else:
                    rpc("VideoLibrary.RefreshEpisode", {"episodeid": item.get("local", {}).get("episodeid")})
            except Exception:
                pass

        # First pass: discover any sudosync IDs already written in .nfo files on NAS
        # (e.g. by another Kodi) so we never overwrite them and always advance
        # last_number past the highest existing sequential SudoSync ID.
        for kind, collection in (("movie", movies), ("episode", episodes)):
            if is_canceled and is_canceled():
                break
            for item in collection:
                if is_canceled and is_canceled():
                    break
                ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
                if _has_sudosync_id(ids):
                    num = _extract_sudosync_id_number(ids.get("sudosync"))
                    if num > last_number:
                        last_number = num
                    continue
                file_path = item.get("file")
                if not file_path:
                    continue
                best_path, nfo_ids, _ = _find_best_nfo(file_path, kind)
                found_ids = nfo_ids if isinstance(nfo_ids, dict) and _has_sudosync_id(nfo_ids) else {}
                if not found_ids and best_path and xbmcvfs.exists(best_path):
                    try:
                        raw_existing = _read_vfs_all(best_path)
                        has_sudosync, parsed_ids = _nfo_raw_has_sudosync_id(raw_existing, kind)
                        if has_sudosync and parsed_ids:
                            found_ids = parsed_ids
                    except Exception:
                        pass
                if found_ids and _has_sudosync_id(found_ids):
                    target_ids = item.setdefault("ids", {})
                    for k, v in found_ids.items():
                        if k not in target_ids and v:
                            target_ids[k] = v
                    num = _extract_sudosync_id_number(target_ids.get("sudosync"))
                    if num > last_number:
                        last_number = num
                    _refresh_item(item, kind)
                    refreshed_existing += 1

        manifest = {
            "generated_at": utc_now_precise(),
            "silent": bool(silent),
            "checked_movies": len(movies),
            "checked_episodes": len(episodes),
            "checked_items": len(movies) + len(episodes),
            "modified_movies": modified_movies,
            "modified_episodes": modified_episodes,
            "newly_assigned": modified_movies + modified_episodes,
            "refreshed_existing": refreshed_existing,
            "last_number": last_number,
            "modified": [],
            "errors": [],
            "cancelled": False,
            "fatal_error": "",
        }

        def _write_manifest():
            manifest["modified_movies"] = modified_movies
            manifest["modified_episodes"] = modified_episodes
            manifest["newly_assigned"] = modified_movies + modified_episodes
            manifest["refreshed_existing"] = refreshed_existing
            manifest["last_number"] = last_number
            if fatal_errors:
                manifest["fatal_error"] = fatal_errors[0]
            manifest_path = os.path.join(_addon_profile_path(), "assign_ids_manifest.json")
            sudosync_manifest_path = os.path.join(_addon_profile_path(), "sudosync_id_manifest.json")
            if silent or not auto_mode or manifest["modified"] or manifest["errors"]:
                try:
                    _write_local_json(manifest_path, manifest)
                except Exception:
                    pass
                try:
                    _write_local_json(sudosync_manifest_path, manifest)
                except Exception:
                    pass

        _write_manifest()

        # Persist discovered highest number if higher than before
        try:
            persisted = persist_network_sudosync_id_last_number(last_number, settings.get("base_path"))
            if persisted and persisted > last_number:
                last_number = persisted
            manifest["last_number"] = last_number
            _write_manifest()
        except Exception as exc:
            log("Nie udało się zsynchronizować początkowego licznika z NAS: {}".format(exc), xbmc.LOGWARNING)

        def _process_item(item, kind):
            nonlocal last_number
            ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
            if _has_sudosync_id(ids):
                return False, False
            file_path = item.get("file")
            if not file_path:
                return False, False

            best_path, nfo_ids, _ = _find_best_nfo(file_path, kind)
            if isinstance(nfo_ids, dict) and _has_sudosync_id(nfo_ids):
                return False, False
            if not best_path:
                return False, False

            import xml.sax.saxutils

            _touch_assign_ids_lock(settings.get("base_path"))

            if xbmcvfs.exists(best_path):
                try:
                    raw = _read_vfs_all(best_path)
                    if not raw:
                        return False, False
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8")
                    else:
                        raw = bytearray(raw)

                    has_sudosync, _ = _nfo_raw_has_sudosync_id(raw, kind)
                    if has_sudosync:
                        return False, False

                    closing_tags = [b"</movie>", b"</episodedetails>", b"</musicvideo>"]
                    if kind == "movie":
                        preferred = b"</movie>"
                    elif kind == "episode":
                        preferred = b"</episodedetails>"
                    elif kind == "musicvideo":
                        preferred = b"</musicvideo>"
                    else:
                        preferred = b"</" + kind.encode("utf-8") + b">"

                    pos = raw.lower().rfind(preferred)
                    if pos < 0:
                        for fallback in closing_tags:
                            pos = raw.lower().rfind(fallback)
                            if pos >= 0:
                                break
                    if pos < 0:
                        return False, False

                    candidate_number = last_number + 1

                    # Atomowa rezerwacja licznika na NAS PRZED modyfikacją pliku .nfo
                    try:
                        persisted_num = persist_network_sudosync_id_last_number(candidate_number, settings.get("base_path"))
                        if not persisted_num or persisted_num < candidate_number:
                            raise IOError("Rezerwacja licznika zwróciła nieprawidłową wartość: {}".format(persisted_num))
                        candidate_number = persisted_num
                    except Exception as exc:
                        log("Błąd rezerwacji licznika SudoSync ID na NAS dla '{}': {}. Przerywanie operacji.".format(best_path, exc), xbmc.LOGERROR)
                        manifest["errors"].append({"file": best_path, "error": "Rezerwacja licznika nie powiodła się: {}".format(exc)})
                        fatal_errors.append(str(exc))
                        manifest["last_number"] = last_number
                        _write_manifest()
                        return False, True

                    last_number = candidate_number
                    manifest["last_number"] = last_number
                    _write_manifest()

                    full_id = "{}-{:06d}".format(prefix, candidate_number)
                    escaped_full_id = xml.sax.saxutils.escape(full_id)

                    has_default_true = bool(re.search(rb'default\s*=\s*["\']true["\']', raw, re.IGNORECASE))
                    default_attr = 'default="false"' if has_default_true else 'default="true"'

                    insert = ('    <uniqueid type="sudosync" ' + default_attr + '>' + escaped_full_id + '</uniqueid>\n').encode("utf-8")
                    new_raw = raw[:pos] + insert + raw[pos:]
                    new_raw_bytes = bytes(new_raw)

                    bak_path = best_path + ".bak"
                    new_path = best_path + ".new"

                    _write_vfs_bytes(new_path, new_raw)
                    if xbmcvfs.exists(bak_path):
                        xbmcvfs.delete(bak_path)

                    success_bak = xbmcvfs.rename(best_path, bak_path)
                    success_new = xbmcvfs.rename(new_path, best_path)

                    if not success_new:
                        if success_bak:
                            xbmcvfs.rename(bak_path, best_path)
                        if xbmcvfs.exists(new_path):
                            xbmcvfs.delete(new_path)
                        return False, False

                    if xbmcvfs.exists(new_path):
                        xbmcvfs.delete(new_path)

                    item.setdefault("ids", {})["sudosync"] = full_id
                    _refresh_item(item, kind)
                    manifest["modified"].append({
                        "file": best_path,
                        "backup": bak_path,
                        "action": "modified",
                        "created": False,
                        "type": kind,
                        "sudosync_id": full_id,
                        "content_sha256": hashlib.sha256(new_raw_bytes).hexdigest(),
                        "title": item.get("title"),
                    })
                    _write_manifest()
                    return True, False
                except Exception as exc:
                    manifest["errors"].append({"file": best_path, "error": str(exc)})
                    _write_manifest()
                    return False, False
            else:
                candidate_number = last_number + 1

                # Atomowa rezerwacja licznika na NAS PRZED utworzeniem pliku .nfo
                try:
                    persisted_num = persist_network_sudosync_id_last_number(candidate_number, settings.get("base_path"))
                    if not persisted_num or persisted_num < candidate_number:
                        raise IOError("Rezerwacja licznika zwróciła nieprawidłową wartość: {}".format(persisted_num))
                    candidate_number = persisted_num
                except Exception as exc:
                    log("Błąd rezerwacji licznika SudoSync ID na NAS dla '{}': {}. Przerywanie operacji.".format(best_path, exc), xbmc.LOGERROR)
                    manifest["errors"].append({"file": best_path, "error": "Rezerwacja licznika nie powiodła się: {}".format(exc)})
                    manifest["last_number"] = last_number
                    _write_manifest()
                    return False, True

                last_number = candidate_number
                manifest["last_number"] = last_number
                _write_manifest()

                full_id = "{}-{:06d}".format(prefix, candidate_number)
                raw_title = item.get("title") or item.get("originaltitle") or "Nieznany"
                xml_content = _build_created_nfo_xml(kind, raw_title, full_id)
                xml_bytes = xml_content.encode("utf-8")
                try:
                    _write_vfs_bytes(best_path, xml_content)
                    item.setdefault("ids", {})["sudosync"] = full_id
                    _refresh_item(item, kind)
                    manifest["modified"].append({
                        "file": best_path,
                        "backup": None,
                        "action": "created",
                        "created": True,
                        "type": kind,
                        "sudosync_id": full_id,
                        "content_sha256": hashlib.sha256(xml_bytes).hexdigest(),
                        "title": raw_title,
                    })
                    _write_manifest()
                    return True, False
                except Exception as exc:
                    manifest["errors"].append({"file": best_path, "error": str(exc)})
                    _write_manifest()
                    return False, False

        aborted = False
        total_items = len(movies) + len(episodes)
        processed = 0

        if silent:
            log(
                "Rozpoczęto sprawdzanie i przypisywanie SudoSync ID w tle (silent=True). "
                "Pozycji w bibliotece: {} (filmy: {}, odcinki: {}), bieżący numer ID w sieci: {}.".format(
                    total_items, len(movies), len(episodes), last_number
                ),
                xbmc.LOGINFO,
            )

        for m in movies:
            if is_canceled and is_canceled():
                log("Przypisywanie SudoSync ID przerwane na żądanie użytkownika.", xbmc.LOGINFO)
                aborted = True
                break
            success, fatal = _process_item(m, "movie")
            if success:
                modified_movies += 1
            processed += 1
            if progress_callback and not silent:
                progress_callback(processed, total_items, m.get("title") or m.get("originaltitle") or "")
            if fatal:
                aborted = True
                break

        if not aborted:
            for e in episodes:
                if is_canceled and is_canceled():
                    log("Przypisywanie SudoSync ID przerwane na żądanie użytkownika.", xbmc.LOGINFO)
                    aborted = True
                    break
                success, fatal = _process_item(e, "episode")
                if success:
                    modified_episodes += 1
                processed += 1
                if progress_callback and not silent:
                    progress_callback(processed, total_items, e.get("title") or e.get("originaltitle") or "")
                if fatal:
                    aborted = True
                    break

        _NFO_ID_CACHE.clear()
        manifest_path = os.path.join(_addon_profile_path(), "assign_ids_manifest.json")
        if aborted:
            manifest["cancelled"] = True
        _write_manifest()

        if silent:
            log(
                "Zakończono ciche przypisywanie SudoSync ID: sprawdzono={}, nadano nowych={} "
                "(filmy={}, odcinki={}), odświeżono={}, ostatni numer ID w sieci={}, błędów={}.".format(
                    total_items,
                    modified_movies + modified_episodes,
                    modified_movies,
                    modified_episodes,
                    refreshed_existing,
                    last_number,
                    len(manifest.get("errors") or []),
                ),
                xbmc.LOGINFO,
            )
            if fatal_errors:
                log("Ciche przypisywanie SudoSync ID napotkało błąd krytyczny: {}".format(fatal_errors[0]), xbmc.LOGERROR)
            for err in manifest.get("errors") or []:
                log("Ciche przypisywanie SudoSync ID - błąd pliku '{}': {}".format(err.get("file"), err.get("error")), xbmc.LOGWARNING)

        return {
            "movies": modified_movies,
            "episodes": modified_episodes,
            "refreshed_existing": refreshed_existing,
            "last_number": last_number,
            "manifest_path": manifest_path,
            "cancelled": aborted,
            "fatal_error": fatal_errors[0] if fatal_errors else "",
        }
    finally:
        _release_assign_ids_lock(base_path=base)


