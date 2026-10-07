import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from resources.lib.sudosync_merge import build_dry_run, build_live_plan

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "service.sudosync"
SCHEMA_VERSION = 2
DRY_RUN_REPORT_SCHEMA = 2
LIVE_SCHEMA_VERSION = 1
LIVE_FIELDS = ("playcount", "lastplayed", "userrating", "resume")
RATING_IMPORT_WHITELIST = {6, 7, 8}
_LIVE_LOCK_HANDLE = None
UPDATE_ZIP_RE = re.compile(r"^service[._]sudosync[_-](\d+)[._](\d+)[._](\d+)(?:-([A-Za-z0-9._-]+))?\.zip$", re.IGNORECASE)


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[SudoSync] {}".format(message), level)


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


INITIAL_WRITE_GUARD_SECONDS = 600


def _initial_write_guard_file():
    return os.path.join(_addon_profile_path(), "initial_write_guard.json")


def arm_initial_write_guard(seconds=INITIAL_WRITE_GUARD_SECONDS):
    seconds = max(30, int(seconds or INITIAL_WRITE_GUARD_SECONDS))
    now = datetime.now(timezone.utc)
    data = {
        "armed": True,
        "armed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expires_at_epoch": now.timestamp() + seconds,
        "expires_in_seconds": seconds,
    }
    _write_local_json(_initial_write_guard_file(), data)
    return data


def disarm_initial_write_guard():
    path = _initial_write_guard_file()
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        log("Cannot remove initial write guard: {}".format(exc), xbmc.LOGWARNING)


def initial_write_guard_status():
    data = _read_local_json(_initial_write_guard_file()) or {}
    if not data.get("armed"):
        return {"armed": False, "remaining_seconds": 0}
    try:
        expires = float(data.get("expires_at_epoch") or 0)
    except Exception:
        expires = 0
    remaining = int(max(0, expires - datetime.now(timezone.utc).timestamp()))
    if remaining <= 0:
        disarm_initial_write_guard()
        return {"armed": False, "expired": True, "remaining_seconds": 0}
    result = dict(data)
    result["armed"] = True
    result["remaining_seconds"] = remaining
    return result


def is_initial_write_guard_armed():
    return bool(initial_write_guard_status().get("armed"))


def addon_settings():
    addon = xbmcaddon.Addon(ADDON_ID)
    base = addon.getSetting("base_path").strip() or "smb://192.168.69.100/Wideo/.SudoSync/"
    if not base.endswith("/"):
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
    
    sudosync_id_enabled = addon.getSetting("sudosync_id_enabled").strip().lower() == "true"
    sudosync_id_prefix = addon.getSetting("sudosync_id_prefix").strip()
    if sudosync_id_prefix:
        sudosync_id_prefix = re.sub(r"[^A-Za-z0-9._-]", "-", sudosync_id_prefix)

    if not sudosync_id_prefix and base.startswith("smb://"):
        try:
            host = base.split("://")[1].split("/")[0].upper()
            sudosync_id_prefix = host
        except Exception:
            pass
    elif not sudosync_id_prefix and base.startswith("nfs://"):
        try:
            host = base.split("://")[1].split("/")[0]
            sudosync_id_prefix = host
        except Exception:
            pass
    if not sudosync_id_prefix:
        sudosync_id_prefix = "LOCAL"

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
        "sudosync_id_prefix": sudosync_id_prefix,
    }



def _shared_config_path(base_path):
    return _join_vfs(base_path, "system").rstrip("/") + "/config.json"


def read_shared_config(base_path=None):
    settings = addon_settings()
    base = (base_path or settings["base_path"]).rstrip("/") + "/"
    path = _shared_config_path(base)
    if not xbmcvfs.exists(path):
        import xbmc
        xbmc.sleep(200)
        if not xbmcvfs.exists(path):
            bak = path + ".bak"
            if xbmcvfs.exists(bak):
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
        raise IOError("Nie można odczytać wspólnej konfiguracji SudoSync: {}".format(exc))


def update_shared_config(mutator_func, base_path=None):
    settings = addon_settings()
    base = (base_path or settings["base_path"]).rstrip("/") + "/"
    ensure_remote_layout(base)
    system_dir = _join_vfs(base, "system")
    if not xbmcvfs.exists(system_dir):
        xbmcvfs.mkdirs(system_dir)
    
    path = _shared_config_path(base)
    lock_dir = path + ".lockdir"
    lock_info_path = lock_dir + "/lock_info.json"
    my_client_id = get_or_create_client_id()
    
    locked = False
    start = datetime.now(timezone.utc).timestamp()
    while True:
        if xbmcvfs.mkdirs(lock_dir):
            _write_vfs_json(lock_info_path, {"client_id": my_client_id, "timestamp": datetime.now(timezone.utc).timestamp()})
            locked = True
            break
            
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            info = _read_vfs_json(lock_info_path) or {}
        except Exception:
            info = {}
            
        lock_ts = float(info.get("timestamp") or 0.0)
        if lock_ts > 0.0 and (now_ts - lock_ts > 60.0):
            log("Znaleziono osierocona blokade. Usuwanie...")
            try:
                xbmcvfs.delete(lock_info_path)
                xbmcvfs.rmdir(lock_dir)
            except Exception:
                pass
            continue
            
        if datetime.now(timezone.utc).timestamp() - start > 15.0:
            raise RuntimeError(
                "Nie można uzyskać blokady wspólnej konfiguracji SudoSync w ciągu 15 sekund."
            )
        xbmc.sleep(200)

    try:
        data = None
        if xbmcvfs.exists(path):
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
                xbmcvfs.delete(lock_info_path)
                xbmcvfs.rmdir(lock_dir)
            except Exception:
                pass


def sync_initial_base_selection(show_notification=False):
    """Read/reconcile the shared base-device selection without changing ownership.

    Since 0.3.3 the base device can be selected only by the explicit
    ``selectbase`` action. Merely opening/changing settings can never steal or
    clear the role. A different base requires the explicit clear action first.
    """
    addon = xbmcaddon.Addon(ADDON_ID)
    settings = addon_settings()
    client_id = get_or_create_client_id()
    explicit_name = addon.getSetting("client_name").strip()
    config = read_shared_config(settings["base_path"])
    init = config.get("initialization") or {}
    base_id = str(init.get("base_client_id") or "")
    base_name = str(init.get("base_client_name") or "")

    # Only keep the label current for the already selected client. Ownership is
    # never changed here.
    if base_id == client_id and explicit_name and explicit_name != base_name:
        def mutator(cfg):
            cfg_init = cfg.setdefault("initialization", {})
            if str(cfg_init.get("base_client_id") or "") == client_id:
                cfg_init["base_client_name"] = explicit_name
            return True
        update_shared_config(mutator, settings["base_path"])
    return read_shared_config(settings["base_path"])


def sync_network_settings():
    """Ensure all Kodi instances on the same network share the same SudoSync ID config."""
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        settings = addon_settings()
        config = read_shared_config(settings["base_path"])
        net = config.get("network_settings")

        local_enabled = addon.getSetting("sudosync_id_enabled").strip().lower() == "true"
        local_prefix = addon.getSetting("sudosync_id_prefix").strip()

        if not net:
            if local_enabled and local_prefix:
                def mutator(cfg):
                    cfg["network_settings"] = {
                        "sudosync_id_enabled": local_enabled,
                        "sudosync_id_prefix": local_prefix,
                    }
                    return True
                update_shared_config(mutator, settings["base_path"])
                log("Published local SudoSync ID settings to shared network config.")
        else:
            remote_enabled = bool(net.get("sudosync_id_enabled", False))
            remote_prefix = str(net.get("sudosync_id_prefix") or "").strip()

            changed = False
            if local_enabled != remote_enabled:
                addon.setSetting("sudosync_id_enabled", "true" if remote_enabled else "false")
                changed = True
            if local_prefix != remote_prefix:
                addon.setSetting("sudosync_id_prefix", remote_prefix)
                changed = True

            if changed:
                log("SudoSync ID network settings forced from shared config to match the network.")
    except Exception as exc:
        log("Nie udało się zsynchronizować ustawień sieciowych SudoSync ID: {}".format(exc), xbmc.LOGWARNING)


def select_this_as_initial_base():
    """Explicitly select this Kodi as the base device for initialization.

    Refuses to replace another selected client. The user must first invoke
    ``clear_initial_base_selection`` through the dedicated UI action.
    """
    addon = xbmcaddon.Addon(ADDON_ID)
    settings = addon_settings()
    client_id = get_or_create_client_id()
    explicit_name = addon.getSetting("client_name").strip()
    if not explicit_name:
        raise RuntimeError("Najpierw wpisz nazwę tego urządzenia Kodi.")

    config = read_shared_config(settings["base_path"])
    init = config.get("initialization") or {}
    if init.get("completed", False):
        raise RuntimeError("Pierwsza synchronizacja jest już zakończona. Zmiana urządzenia bazowego jest zablokowana.")

    base_id = str(init.get("base_client_id") or "")
    base_name = str(init.get("base_client_name") or "")
    if base_id and base_id != client_id:
        raise RuntimeError(
            "Urządzeniem bazowym jest już: {}.\n\n"
            "Najpierw użyj „Wyczyść wybór urządzenia bazowego”, a dopiero potem wybierz inne Kodi.".format(
                base_name or base_id[:8]
            )
        )

    already = base_id == client_id
    def mutator(cfg):
        cfg_init = cfg.setdefault("initialization", {})
        cfg_init.update({
            "completed": False,
            "base_client_id": client_id,
            "base_client_name": explicit_name,
            "selected_at": cfg_init.get("selected_at") if already and cfg_init.get("selected_at") else utc_now(),
            "completed_at": "",
        })
        return True
    path = update_shared_config(mutator, settings["base_path"])
    return {
        "selected": True,
        "already_selected": already,
        "client_id": client_id,
        "client_name": explicit_name,
        "config_path": path,
    }

def clear_initial_base_selection():
    """Clear the shared base selection only before initialization is completed."""
    addon = xbmcaddon.Addon(ADDON_ID)
    settings = addon_settings()
    config = read_shared_config(settings["base_path"])
    init = config.get("initialization") or {}
    if init.get("completed", False):
        raise RuntimeError("Pierwsza synchronizacja jest już zakończona. Zwykłe wyczyszczenie wyboru bazowego jest zablokowane.")
    def mutator(cfg):
        cfg_init = cfg.setdefault("initialization", {})
        cfg_init.update({
            "completed": False,
            "base_client_id": "",
            "base_client_name": "",
            "selected_at": "",
            "completed_at": "",
        })
        return True
    path = update_shared_config(mutator, settings["base_path"])
    return path


def _mark_initialization_completed_if_converged(report, base_path=None):
    clients = report.get("clients") if isinstance(report.get("clients"), list) else []
    if not clients:
        return False
    if any(int((client.get("planned_changes") or {}).get("items", 0) or 0) > 0 for client in clients):
        return False
    config = read_shared_config(base_path)
    init = config.get("initialization") or {}
    if not init.get("base_client_id"):
        return False
    def mutator(cfg):
        cfg_init = cfg.setdefault("initialization", {})
        if not cfg_init.get("completed", False):
            cfg_init["completed"] = True
            cfg_init["completed_at"] = utc_now()
            return True
        return False
    update_shared_config(mutator, base_path)
    return True


def _numeric_version(text):
    text_str = str(text or "").lower()
    nums = re.findall(r"\d+", text_str)
    values = [int(x) for x in nums[:3]]
    while len(values) < 3:
        values.append(0)
    
    tag_val = 3
    if "-alpha" in text_str or "alpha" in text_str:
        tag_val = 0
    elif "-beta" in text_str or "beta" in text_str:
        tag_val = 1
    elif "-rc" in text_str or "rc" in text_str:
        tag_val = 2
        
    values.append(tag_val)
    return tuple(values)


def find_latest_update(base_path=None):
    settings = addon_settings()
    base = (base_path or settings["base_path"]).rstrip("/") + "/"
    current_text = xbmcaddon.Addon(ADDON_ID).getAddonInfo("version") or "0.0.0"
    current = _numeric_version(current_text)
    try:
        dirs, files = xbmcvfs.listdir(base)
    except Exception as exc:
        return {
            "available": False,
            "current_version": current_text,
            "error": str(exc),
            "base_path": base,
        }

    candidates = []
    for filename in files:
        match = UPDATE_ZIP_RE.match(filename or "")
        if not match:
            continue
        ver_tuple = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        suffix = match.group(4) or ""
        version_text = "{}.{}.{}".format(*ver_tuple)
        if suffix:
            version_text += "-" + suffix
        candidates.append((ver_tuple, version_text, filename))

    if not candidates:
        return {
            "available": False,
            "current_version": current_text,
            "base_path": base,
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    latest_tuple, latest_text, latest_file = candidates[0]
    return {
        "available": latest_tuple > current,
        "current_version": current_text,
        "version": latest_text,
        "filename": latest_file,
        "path": base + latest_file,
        "base_path": base,
    }


def check_for_update(show_notification=True, manual=False):
    result = find_latest_update()
    if result.get("available") and show_notification:
        xbmcgui.Dialog().notification(
            "SudoSync — aktualizacja",
            "Dostępna {}. Ustawienia SudoSync → Zainstaluj aktualizację.".format(result.get("version", "nowsza wersja")),
            xbmcgui.NOTIFICATION_INFO,
            9000,
        )
        log("Update available: {} -> {}".format(result.get("current_version"), result.get("version")))
    elif result.get("error"):
        log("Cannot check updates: {}".format(result["error"]), xbmc.LOGWARNING)
    elif manual:
        log("No newer update in {}".format(result.get("base_path", "")))
    return result


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


def _safe_zip_member(name):
    normalized = str(name or "").replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return False
    parts = [p for p in normalized.split("/") if p]
    if not parts or parts[0] != ADDON_ID:
        return False
    return all(part not in (".", "..") for part in parts)


def _validate_update_zip(zip_path, expected_version=None):
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        normalized_map = {str(name or "").replace("\\", "/"): name for name in names}
        if not names or not all(_safe_zip_member(name) for name in normalized_map.keys() if name and not name.endswith("/")):
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
            raise ValueError("Wersja w addon.xml ({}) nie zgadza się z nazwą pakietu ({}).".format(version, expected_version))
        return version


def install_latest_update(show_dialogs=True):
    """Install the newest SudoSync package directly from the hidden shared folder.

    This intentionally does not use Kodi's file picker. The package is copied through
    xbmcvfs, validated, staged beside the installed add-on, then atomically swapped.
    The current Python process may keep running old code until Kodi is restarted.
    """
    result = find_latest_update()
    if result.get("error"):
        raise IOError(result["error"])
    if not result.get("available"):
        return {"installed": False, "available": False, "current_version": result.get("current_version")}

    remote_zip = result["path"]
    filename = result["filename"]
    package_dir = xbmcvfs.translatePath("special://home/addons/packages/")
    if not os.path.isdir(package_dir):
        os.makedirs(package_dir)
    local_zip = os.path.join(package_dir, filename)
    _copy_vfs_to_local(remote_zip, local_zip)
    package_version = _validate_update_zip(local_zip, result.get("version"))

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
                    shutil.copyfileobj(src, dst)

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

    log("Self-update installed from {} -> {}".format(remote_zip, package_version))
    if show_dialogs:
        restart = xbmcgui.Dialog().yesno(
            "SudoSync — aktualizacja zainstalowana",
            "Zainstalowano wersję {} bez otwierania ukrytego folderu .SudoSync.\n\n"
            "Nowa wersja będzie pewnie aktywna po ponownym uruchomieniu Kodi. Uruchomić Kodi ponownie teraz?".format(package_version),
            yeslabel="Uruchom ponownie",
            nolabel="Później",
        )
        if restart:
            xbmc.executebuiltin("RestartApp")
    return {
        "installed": True,
        "available": True,
        "version": package_version,
        "filename": filename,
        "remote_path": remote_zip,
        "local_package": local_zip,
    }


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
        if not xbmcvfs.exists(path):
            continue
        if first_existing is None:
            first_existing = path
        text = _read_vfs_text(path)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except Exception:
            continue
            
        ids = {}
        for node in root.findall("uniqueid"):
            typ = (node.attrib.get("type") or "").strip().lower()
            val = (node.text or "").strip()
            if typ and val:
                ids[typ] = val
        if kind == "movie":
            tmdbid = root.findtext("tmdbid")
            if tmdbid and "tmdb" not in ids:
                ids["tmdb"] = tmdbid.strip()
            legacy = root.findtext("id")
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
        return _NFO_ID_CACHE[media_file]
    _, ids, _ = _find_best_nfo(media_file, kind)
    _NFO_ID_CACHE[media_file] = ids
    return ids


def _record(kind, item, first_import=True):
    ids = _normalize_ids(item.get("uniqueid"), item.get("imdbnumber"))
    if not ids:
        ids = _ids_from_nfo(item.get("file"), kind)
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


def collect_library(first_import=True):
    movie_props = [
        "title", "originaltitle", "year", "imdbnumber", "uniqueid", "file",
        "playcount", "lastplayed", "resume", "userrating"
    ]
    episode_props = [
        "title", "originaltitle", "showtitle", "season", "episode", "uniqueid", "file",
        "playcount", "lastplayed", "resume", "userrating"
    ]
    movies_result = rpc("VideoLibrary.GetMovies", {"properties": movie_props})
    episodes_result = rpc("VideoLibrary.GetEpisodes", {"properties": episode_props})
    movies = [_record("movie", x, first_import=first_import) for x in movies_result.get("movies", [])]
    episodes = [_record("episode", x, first_import=first_import) for x in episodes_result.get("episodes", [])]
    return movies, episodes


def _slug(value):
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip())
    value = value.strip("-._")
    return value[:48] or "kodi"


def _join_vfs(base, *parts):
    out = base.rstrip("/") + "/"
    for part in parts:
        out += str(part).strip("/") + "/"
    return out


def _write_vfs_bytes(path, payload):
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


def _write_vfs_json(path, data):
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    tmp = path + ".new"
    bak = path + ".bak"
    _write_vfs_bytes(tmp, payload)
    if xbmcvfs.exists(bak):
        xbmcvfs.delete(bak)
    had_old = xbmcvfs.exists(path)
    if had_old:
        if not xbmcvfs.rename(path, bak):
            xbmcvfs.delete(tmp)
            raise IOError("Cannot rotate existing file: {}".format(path))
    if not xbmcvfs.rename(tmp, path):
        if had_old and xbmcvfs.exists(bak):
            xbmcvfs.rename(bak, path)
        raise IOError("Cannot install new file: {}".format(path))
    if xbmcvfs.exists(bak):
        xbmcvfs.delete(bak)


def ensure_remote_layout(base_path):
    if not xbmcvfs.exists(base_path):
        xbmcvfs.mkdirs(base_path)
    clients = _join_vfs(base_path, "clients")
    if not xbmcvfs.exists(clients):
        xbmcvfs.mkdirs(clients)
    system_dir = _join_vfs(base_path, "system")
    if not xbmcvfs.exists(system_dir):
        xbmcvfs.mkdirs(system_dir)
    live_dir = _join_vfs(base_path, "live")
    if not xbmcvfs.exists(live_dir):
        xbmcvfs.mkdirs(live_dir)
    marker = base_path.rstrip("/") + "/format.json"
    if not xbmcvfs.exists(marker):
        _write_vfs_json(marker, {
            "format": "SudoSync",
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now(),
            "mode": "collect_only",
        })
    return clients


def snapshot_path(base_path, client_name, client_id):
    clients = _join_vfs(base_path, "clients")
    return clients + "{}--{}.json".format(_slug(client_name), client_id[:8])



def cleanup_old_client_snapshots(base_path, client_id, keep_path):
    """Remove stale snapshots created by this same client under an older device name."""
    clients = _join_vfs(base_path, "clients")
    suffix = "--{}.json".format(client_id[:8]).lower()
    keep = keep_path.lower()
    try:
        dirs, files = xbmcvfs.listdir(clients)
    except Exception as exc:
        log("Cannot list client snapshots for cleanup: {}".format(exc), xbmc.LOGWARNING)
        return 0
    removed = 0
    for filename in files:
        if not (filename or "").lower().endswith(suffix):
            continue
        path = clients + filename
        if path.lower() == keep:
            continue
        try:
            if xbmcvfs.delete(path):
                removed += 1
                log("Removed old snapshot after device rename: {}".format(path))
        except Exception as exc:
            log("Cannot remove stale snapshot {}: {}".format(path, exc), xbmc.LOGWARNING)
    return removed

def _collect_initial_snapshot(show_notification=False):
    settings = addon_settings()
    if not settings["base_path"].lower().startswith(("smb://", "nfs://", "special://", "/")):
        raise ValueError("Unsupported synchronization path: {}".format(settings["base_path"]))
    client_id = get_or_create_client_id()
    kodi = get_kodi_info()
    movies, episodes = collect_library()
    ensure_remote_layout(settings["base_path"])
    snapshot = {
        "format": "SudoSync client snapshot",
        "schema_version": SCHEMA_VERSION,
        "mode": "collect_only",
        "generated_at": utc_now(),
        "client": {
            "id": client_id,
            "name": settings["client_name"],
            "kodi": kodi,
        },
        "initial_import_rules": {
            "userrating_whitelist": sorted(RATING_IMPORT_WHITELIST),
            "other_userratings_are_null": True,
        },
        "counts": {"movies": len(movies), "episodes": len(episodes)},
        "movies": movies,
        "episodes": episodes,
    }
    remote = snapshot_path(settings["base_path"], settings["client_name"], client_id)
    _write_vfs_json(remote, snapshot)
    cleanup_old_client_snapshots(settings["base_path"], client_id, remote)
    local_status = {
        "last_snapshot_at": snapshot["generated_at"],
        "remote_path": remote,
        "counts": snapshot["counts"],
        "kodi": kodi,
        "client_id": client_id,
        "client_name": settings["client_name"],
    }
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), local_status)
    log("Snapshot written: {} movies, {} episodes -> {}".format(len(movies), len(episodes), remote))
    if show_notification:
        xbmcgui.Dialog().notification(
            "SudoSync",
            "Snapshot: {} filmów, {} odcinków".format(len(movies), len(episodes)),
            xbmcgui.NOTIFICATION_INFO,
            5000,
        )
    return local_status



ZERO_VERSION = {"lc": 0, "ts": "0001-01-01T00:00:00.000Z", "client_id": "", "seq": 0}


def _live_registry_file():
    return os.path.join(_addon_profile_path(), "live_registry.json")


def _live_lock_file():
    return os.path.join(_addon_profile_path(), "live_cycle_lock.json")


def _record_local_key(record):
    kind = str(record.get("type") or "")
    local = record.get("local") if isinstance(record.get("local"), dict) else {}
    if kind == "movie":
        value = _safe_int(local.get("movieid"), -1)
        if value >= 0:
            return "movie:{}".format(value)
    elif kind == "episode":
        value = _safe_int(local.get("episodeid"), -1)
        if value >= 0:
            return "episode:{}".format(value)
    # Defensive fallback; local IDs should always exist for library records.
    return "{}:{}:{}".format(kind, record.get("fallback_identity") or "", record.get("file") or "")


def _live_state_copy(state):
    state = state if isinstance(state, dict) else {}
    resume = state.get("resume") if isinstance(state.get("resume"), dict) else {}
    return {
        "playcount": _safe_int(state.get("playcount"), 0),
        "lastplayed": str(state.get("lastplayed") or ""),
        "resume": {
            "position": round(_safe_float(resume.get("position"), 0.0), 3),
            "total": round(_safe_float(resume.get("total"), 0.0), 3),
        },
        "userrating": _live_rating(state.get("userrating")),
    }


def _live_field_equal(field, a, b):
    if field == "resume":
        a = a if isinstance(a, dict) else {}
        b = b if isinstance(b, dict) else {}
        ap = round(_safe_float(a.get("position"), 0.0), 1)
        bp = round(_safe_float(b.get("position"), 0.0), 1)
        if ap == 0.0 and bp == 0.0:
            return True
        return ap == bp and round(_safe_float(a.get("total"), 0.0), 1) == round(_safe_float(b.get("total"), 0.0), 1)
    return a == b


def _baseline_version(initialization):
    stamp = str((initialization or {}).get("completed_at") or (initialization or {}).get("selected_at") or "1970-01-01T00:00:00.000Z")
    if stamp.endswith("Z") and "." not in stamp:
        stamp = stamp[:-1] + ".000Z"
    return {"lc": 0, "ts": stamp, "client_id": "baseline", "seq": 0}


def _read_live_registry(client_id, initialization):
    path = _live_registry_file()
    data = _read_local_json(path) or {}
    if data.get("client_id") != client_id or data.get("schema_version") != LIVE_SCHEMA_VERSION:
        data = {}
    if not data:
        data = {
            "format": "SudoSync local live registry",
            "schema_version": LIVE_SCHEMA_VERSION,
            "client_id": client_id,
            "created_at": utc_now(),
            "bootstrapped": False,
            "seq": 0,
            "records": {},
            "baseline_version": _baseline_version(initialization),
        }
    if not isinstance(data.get("records"), dict):
        data["records"] = {}
    data["seq"] = _safe_int(data.get("seq"), 0)
    return data


def _next_live_version(registry, client_id):
    registry["seq"] = _safe_int(registry.get("seq"), 0) + 1
    registry["lc"] = _safe_int(registry.get("lc"), 0) + 1
    return {"lc": registry["lc"], "ts": utc_now_precise(), "client_id": client_id, "seq": registry["seq"]}


def _try_os_file_lock(fd):
    """Acquire a non-blocking advisory OS lock on an already-created file."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, IOError):
        return False


def _unlock_os_file(fd):
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, IOError):
        pass


def _acquire_live_lock(max_age_seconds=180):
    """Acquire the local LIVE lock using an OS advisory lock.

    The lock file is intentionally persistent. The OS releases the advisory lock
    automatically when Kodi/process exits, so a crashed process cannot strand a
    stale path that later clients must blindly delete.
    """
    global _LIVE_LOCK_HANDLE
    if _LIVE_LOCK_HANDLE is not None:
        return False

    path = _live_lock_file()
    now = datetime.now(timezone.utc).timestamp()
    fd = None
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR)
        payload = json.dumps({
            "locked": True,
            "started_epoch": now,
            "started_at": utc_now_precise(),
            "pid": os.getpid(),
        })
        os.ftruncate(fd, 0)
        os.write(fd, payload.encode("utf-8"))
        os.lseek(fd, 0, os.SEEK_SET)

        # Ensure a one-byte region exists for Windows msvcrt locking.
        if os.path.getsize(path) == 0:
            os.write(fd, b" ")
            os.lseek(fd, 0, os.SEEK_SET)

        if not _try_os_file_lock(fd):
            os.close(fd)
            return False

        _LIVE_LOCK_HANDLE = fd
        return True
    except (OSError, IOError):
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        return False


def _release_live_lock():
    global _LIVE_LOCK_HANDLE
    fd = _LIVE_LOCK_HANDLE
    _LIVE_LOCK_HANDLE = None
    if fd is None:
        return
    _unlock_os_file(fd)
    try:
        os.close(fd)
    except Exception as exc:
        log("Cannot close live cycle lock: {}".format(exc), xbmc.LOGWARNING)


def _live_backup_path(base_path, client_name, client_id):
    backups = _join_vfs(base_path, "backups")
    client_dir = _join_vfs(backups, "{}--{}".format(_slug(client_name), client_id[:8]))
    if not xbmcvfs.exists(backups):
        xbmcvfs.mkdirs(backups)
    if not xbmcvfs.exists(client_dir):
        xbmcvfs.mkdirs(client_dir)
    # Rolling backup: bounded disk usage for continuous synchronization.
    return client_dir + "pre-live-latest.json"


def live_report_path(base_path, client_name, client_id):
    reports = _join_vfs(base_path, "reports")
    if not xbmcvfs.exists(reports):
        xbmcvfs.mkdirs(reports)
    return reports + "{}--{}--live.json".format(_slug(client_name), client_id[:8])



def _live_pulse_path(base_path, client_name, client_id):
    return _join_vfs(base_path, "live") + "{}--{}.json".format(_slug(client_name), client_id[:8])


def _cleanup_old_live_pulses(base_path, client_id, keep_path):
    live_dir = _join_vfs(base_path, "live")
    suffix = "--{}.json".format(client_id[:8]).lower()
    try:
        _dirs, files = xbmcvfs.listdir(live_dir)
    except Exception:
        return
    for filename in files:
        if not (filename or "").lower().endswith(suffix):
            continue
        path = live_dir + filename
        if path.lower() == keep_path.lower():
            continue
        try:
            xbmcvfs.delete(path)
        except Exception:
            pass


def _peer_pulse_cache_file():
    return os.path.join(_addon_profile_path(), "live_peer_pulses.json")


def _read_live_pulses(base_path):
    live_dir = _join_vfs(base_path, "live")
    result = {}
    if not xbmcvfs.exists(live_dir):
        return result
    try:
        _dirs, files = xbmcvfs.listdir(live_dir)
    except Exception:
        return result
    for filename in files:
        if not (filename or "").lower().endswith(".json"):
            continue
        try:
            data = _read_vfs_json(live_dir + filename)
        except Exception:
            continue
        cid = str(data.get("client_id") or "")
        if cid:
            result[cid] = {
                "client_name": str(data.get("client_name") or cid),
                "snapshot_at": str(data.get("snapshot_at") or ""),
                "seq": _safe_int(data.get("seq"), 0),
            }
    return result


def _remote_pulse_changed(base_path, client_id):
    pulses = _read_live_pulses(base_path)
    cached = _read_local_json(_peer_pulse_cache_file()) or {}
    old = cached.get("pulses") if isinstance(cached.get("pulses"), dict) else {}
    for cid, pulse in pulses.items():
        if cid == client_id:
            continue
        if (old.get(cid) or {}).get("snapshot_at") != pulse.get("snapshot_at") or _safe_int((old.get(cid) or {}).get("seq"), -1) != _safe_int(pulse.get("seq"), -1):
            return True, pulses
    # A removed peer pulse also deserves one full reconciliation.
    old_remote = set(old) - {client_id}
    new_remote = set(pulses) - {client_id}
    if old_remote != new_remote:
        return True, pulses
    return False, pulses


def _save_peer_pulses(pulses):
    _write_local_json(_peer_pulse_cache_file(), {"updated_at": utc_now_precise(), "pulses": pulses or {}})


def _probe_library_state():
    """Read only local IDs + user-state fields, without paths/IDs/NFO fallback.

    This is intentionally much lighter than a full SudoSync snapshot and is used
    as a periodic safety net when Kodi/Android misses an OnUpdate/OnStop
    notification. It never writes to the Kodi library.
    """
    props = ["playcount", "lastplayed", "resume", "userrating"]
    movies_result = rpc("VideoLibrary.GetMovies", {"properties": props})
    episodes_result = rpc("VideoLibrary.GetEpisodes", {"properties": props})
    result = {}
    for item in movies_result.get("movies", []) or []:
        mid = _safe_int(item.get("movieid"), -1)
        if mid >= 0:
            result["movie:{}".format(mid)] = _live_state_copy(item)
    for item in episodes_result.get("episodes", []) or []:
        eid = _safe_int(item.get("episodeid"), -1)
        if eid >= 0:
            result["episode:{}".format(eid)] = _live_state_copy(item)
    return result


def live_local_changes_detected():
    """Cheap periodic fallback for missed Kodi notifications.

    Returns a diagnostic dict. A positive result means the next LIVE cycle must
    collect a full local snapshot before resolving peers. No Kodi writes happen
    here.
    """
    settings = addon_settings()
    shared = read_shared_config(settings["base_path"])
    initialization = shared.get("initialization") or {}
    client_id = get_or_create_client_id()
    registry = _read_live_registry(client_id, initialization)
    now = utc_now_precise()

    if not registry.get("bootstrapped"):
        result = {"changed": True, "reason": "registry_not_bootstrapped", "changed_items": 0}
    else:
        probe = _probe_library_state()
        registered = registry.get("records") if isinstance(registry.get("records"), dict) else {}
        changed_items = 0
        reason = ""
        if set(probe) != set(registered):
            changed_items = len(set(probe).symmetric_difference(set(registered)))
            reason = "library_membership_changed"
        else:
            for key, actual in probe.items():
                old = registered.get(key) if isinstance(registered.get(key), dict) else {}
                observed = _live_state_copy(old.get("observed_state") or {})
                if any(not _live_field_equal(field, observed.get(field), actual.get(field)) for field in LIVE_FIELDS):
                    changed_items += 1
            if changed_items:
                reason = "user_state_changed"
        result = {"changed": bool(changed_items), "reason": reason or "unchanged", "changed_items": changed_items}

    status = read_local_status()
    status["last_local_probe_at"] = now
    status["last_local_probe_changed"] = bool(result.get("changed"))
    status["last_local_probe_changed_items"] = _safe_int(result.get("changed_items"), 0)
    status["last_local_probe_reason"] = str(result.get("reason") or "")
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)
    return result


def _live_change_labels(counts):
    counts = counts if isinstance(counts, dict) else {}
    labels = []
    if _safe_int(counts.get("playcount"), 0) or _safe_int(counts.get("lastplayed"), 0):
        labels.append("odtworzenie")
    if _safe_int(counts.get("resume"), 0):
        labels.append("resume")
    if _safe_int(counts.get("userrating"), 0):
        labels.append("ocena")
    return ", ".join(labels) or "stan"


def _parse_kodi_datetime(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _promote_guarded_local_fields(item, fields):
    """Promote unquestionably newer local playback fields before a stale remote write.

    This is a defensive recovery path for a narrow race: a library-scan refresh can
    temporarily suppress publication while the user stops/seeks a video. If a later
    remote plan would move a non-empty lastplayed timestamp backwards, the local
    playback is newer by Kodi's own timestamp and must not be destroyed.
    """
    fields = set(fields or [])
    if not fields:
        return
    client_id = get_or_create_client_id()
    shared = read_shared_config()
    registry = _read_live_registry(client_id, shared.get("initialization") or {})
    key = str(item.get("local_key") or "")
    entry = registry.get("records", {}).get(key)
    if not isinstance(entry, dict):
        return
    observed = _live_state_copy(entry.get("observed_state") or {})
    published = _live_state_copy(entry.get("published_state") or observed)
    versions = entry.get("field_versions") if isinstance(entry.get("field_versions"), dict) else {}
    changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
    for field in fields:
        if field not in LIVE_FIELDS or field not in changes:
            continue
        current = changes[field].get("from") if isinstance(changes[field], dict) else None
        if field == "resume":
            current = current if isinstance(current, dict) else {}
            current = {
                "position": _safe_float(current.get("position"), 0.0),
                "total": _safe_float(current.get("total"), 0.0),
            }
        elif field == "playcount":
            current = _safe_int(current, 0)
        elif field == "userrating":
            current = _live_rating(current)
        elif field == "lastplayed":
            current = str(current or "")
        observed[field] = current
        published[field] = current
        versions[field] = _next_live_version(registry, client_id)
    entry["observed_state"] = observed
    entry["published_state"] = published
    entry["field_versions"] = versions
    registry["records"][key] = entry
    registry["updated_at"] = utc_now_precise()
    _write_local_json(_live_registry_file(), registry)


def _guard_live_item_against_playback_regression(item):
    """Remove destructive stale playback regressions from one remote plan item.

    A remote lastplayed value is never allowed to replace a strictly newer,
    non-empty local lastplayed value. Resume is paired with that protection because
    it belongs to the same newer local playback session. The guarded local fields
    are promoted in the registry so they can propagate to peers on the refresh.
    """
    changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
    guarded = []
    lp = changes.get("lastplayed") if isinstance(changes.get("lastplayed"), dict) else None
    if lp:
        current_dt = _parse_kodi_datetime(lp.get("from"))
        target_dt = _parse_kodi_datetime(lp.get("to"))
        if current_dt is not None and target_dt is not None and current_dt > target_dt:
            guarded.append("lastplayed")
            if "resume" in changes:
                guarded.append("resume")
    if not guarded:
        return item, []
    _promote_guarded_local_fields(item, guarded)
    safe = dict(item)
    safe_changes = dict(changes)
    for field in guarded:
        safe_changes.pop(field, None)
    safe["changes"] = safe_changes
    target_versions = item.get("target_versions") if isinstance(item.get("target_versions"), dict) else {}
    safe["target_versions"] = {k: v for k, v in target_versions.items() if k not in guarded}
    return safe, guarded


def collect_live_snapshot(show_notification=False, suppress_local_changes=False, notify_local_changes=False):
    """Collect a versioned live snapshot after first synchronization is complete.

    Each Kodi is the only writer of its own snapshot. Per-field versions are kept
    locally so later merges can implement last-real-change-wins without relying on
    Kodi database schemas. New library items and scan/NFO changes get a zero
    version and therefore cannot overwrite remembered shared state.
    """
    settings = addon_settings()
    shared = read_shared_config(settings["base_path"])
    initialization = shared.get("initialization") or {}
    if not initialization.get("completed", False):
        raise RuntimeError("Synchronizacja bieżąca wymaga zakończonej synchronizacji początkowej.")

    client_id = get_or_create_client_id()
    registry = _read_live_registry(client_id, initialization)
    first_bootstrap = not bool(registry.get("bootstrapped"))
    baseline = registry.get("baseline_version") or _baseline_version(initialization)
    movies, episodes = collect_library(first_import=False)
    current_records = {}
    local_changed_items = 0
    local_changed_fields = {field: 0 for field in LIVE_FIELDS}

    for record in list(movies) + list(episodes):
        key = _record_local_key(record)
        record["sync_local_key"] = key
        actual = _live_state_copy(record.get("state") or {})
        old = registry["records"].get(key)
        if isinstance(old, dict):
            observed = _live_state_copy(old.get("observed_state") or {})
            published = _live_state_copy(old.get("published_state") or old.get("observed_state") or {})
            versions = old.get("field_versions") if isinstance(old.get("field_versions"), dict) else {}
            versions = {field: dict(versions.get(field) or baseline) for field in LIVE_FIELDS}
            record_has_local_change = False
            for field in LIVE_FIELDS:
                if not _live_field_equal(field, observed.get(field), actual.get(field)):
                    published[field] = actual[field]
                    # A post-scan quarantine must not discard an explicit edit of
                    # an already existing rating (e.g. 7 -> 8 or 7 -> 0). Such a
                    # change is user state, not a first-import/NFO bootstrap value.
                    preserve_existing_rating_edit = (
                        field == "userrating"
                        and _live_rating(observed.get(field)) != 0
                    )
                    if suppress_local_changes and not preserve_existing_rating_edit:
                        versions[field] = dict(ZERO_VERSION)
                    else:
                        versions[field] = _next_live_version(registry, client_id)
                        local_changed_fields[field] += 1
                        record_has_local_change = True
            if record_has_local_change:
                local_changed_items += 1
            observed = actual
        else:
            observed = actual
            if first_bootstrap:
                # Preserve the first-import rule: old ratings outside 6/7/8 are
                # ignored until the user changes the rating after 0.4 starts.
                published = dict(actual)
                published["resume"] = dict(actual["resume"])
                published["userrating"] = actual["userrating"] if actual["userrating"] in RATING_IMPORT_WHITELIST else 0
                versions = {field: dict(baseline) for field in LIVE_FIELDS}
            else:
                # New media (including scan/NFO imports) must never trump a
                # remembered state from another Kodi merely because it appeared.
                published = dict(actual)
                published["resume"] = dict(actual["resume"])
                versions = {field: dict(ZERO_VERSION) for field in LIVE_FIELDS}

        record["state"] = _live_state_copy(published)
        record["field_versions"] = {field: dict(versions[field]) for field in LIVE_FIELDS}
        current_records[key] = {
            "observed_state": observed,
            "published_state": _live_state_copy(published),
            "field_versions": record["field_versions"],
            "type": record.get("type") or "",
            "file": record.get("file") or "",
            "local": record.get("local") or {},
        }

    registry["records"] = current_records
    registry["bootstrapped"] = True
    registry["updated_at"] = utc_now_precise()
    _write_local_json(_live_registry_file(), registry)

    kodi = get_kodi_info()
    ensure_remote_layout(settings["base_path"])
    snapshot = {
        "format": "SudoSync client snapshot",
        "schema_version": SCHEMA_VERSION,
        "live_schema_version": LIVE_SCHEMA_VERSION,
        "mode": "live",
        "generated_at": utc_now_precise(),
        "client": {"id": client_id, "name": settings["client_name"], "kodi": kodi},
        "counts": {"movies": len(movies), "episodes": len(episodes)},
        "movies": movies,
        "episodes": episodes,
    }
    remote = snapshot_path(settings["base_path"], settings["client_name"], client_id)
    _write_vfs_json(remote, snapshot)
    cleanup_old_client_snapshots(settings["base_path"], client_id, remote)
    pulse_path = _live_pulse_path(settings["base_path"], settings["client_name"], client_id)
    _write_vfs_json(pulse_path, {
        "format": "SudoSync live pulse",
        "schema_version": LIVE_SCHEMA_VERSION,
        "client_id": client_id,
        "client_name": settings["client_name"],
        "snapshot_at": snapshot["generated_at"],
        "seq": registry.get("seq", 0),
    })
    _cleanup_old_live_pulses(settings["base_path"], client_id, pulse_path)

    status = read_local_status()
    status.update({
        "last_snapshot_at": snapshot["generated_at"],
        "remote_path": remote,
        "counts": snapshot["counts"],
        "kodi": kodi,
        "client_id": client_id,
        "client_name": settings["client_name"],
        "live_registry_bootstrapped": True,
        "last_live_published_items": local_changed_items,
        "last_live_published_fields": dict(local_changed_fields),
    })
    if local_changed_items:
        status["last_live_published_at"] = snapshot["generated_at"]
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)
    if notify_local_changes and settings.get("notifications") and local_changed_items:
        xbmcgui.Dialog().notification(
            "SudoSync — wysłano zmianę",
            "{} poz. ({})".format(local_changed_items, _live_change_labels(local_changed_fields)),
            xbmcgui.NOTIFICATION_INFO,
            6000,
        )
    elif show_notification:
        xbmcgui.Dialog().notification(
            "SudoSync",
            "Snapshot LIVE: {} filmów, {} odcinków".format(len(movies), len(episodes)),
            xbmcgui.NOTIFICATION_INFO,
            4000,
        )
    return status


def collect_and_write(show_notification=False):
    """Collect an initial-import snapshot before convergence, live snapshot after it."""
    try:
        # Sync our settings alias quietly in the background
        get_or_sync_network_id_and_alias()
        initialization = (read_shared_config().get("initialization") or {})
    except Exception:
        initialization = {}
    if initialization.get("completed", False):
        return collect_live_snapshot(show_notification=show_notification, suppress_local_changes=False)
    return _collect_initial_snapshot(show_notification=show_notification)


def _snapshots_ready_for_live(snapshots):
    not_ready = []
    now_ts = datetime.now(timezone.utc).timestamp()
    for snap in snapshots:
        client = snap.get("client") if isinstance(snap.get("client"), dict) else {}
        file_mtime = float(snap.get("mtime") or 0.0)
        if file_mtime > 0.0 and (now_ts - file_mtime > 14 * 86400):
            continue
            
        if snap.get("mode") != "live" or _safe_int(snap.get("live_schema_version"), 0) < LIVE_SCHEMA_VERSION:
            not_ready.append(str(client.get("name") or client.get("id") or "Kodi"))
    return not_ready


def _accept_remote_into_registry(item):
    client_id = get_or_create_client_id()
    shared = read_shared_config()
    registry = _read_live_registry(client_id, shared.get("initialization") or {})
    key = str(item.get("local_key") or "")
    entry = registry.get("records", {}).get(key)
    if not isinstance(entry, dict):
        return
    observed = _live_state_copy(entry.get("observed_state") or {})
    published = _live_state_copy(entry.get("published_state") or observed)
    versions = entry.get("field_versions") if isinstance(entry.get("field_versions"), dict) else {}
    target_versions = item.get("target_versions") if isinstance(item.get("target_versions"), dict) else {}
    changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
    for field, change in changes.items():
        if field not in LIVE_FIELDS:
            continue
        target = change.get("to") if isinstance(change, dict) else None
        if field == "resume":
            target = target if isinstance(target, dict) else {}
            target = {"position": _safe_float(target.get("position"), 0.0), "total": _safe_float(target.get("total"), 0.0)}
        elif field == "playcount":
            target = _safe_int(target, 0)
        elif field == "userrating":
            target = _live_rating(target)
        elif field == "lastplayed":
            target = str(target or "")
        observed[field] = target
        published[field] = target
        tv = dict(target_versions.get(field) or versions.get(field) or ZERO_VERSION)
        versions[field] = tv
        registry["lc"] = max(_safe_int(registry.get("lc"), 0), _safe_int(tv.get("lc"), 0))
    entry["observed_state"] = observed
    entry["published_state"] = published
    entry["field_versions"] = versions
    registry["records"][key] = entry
    registry["updated_at"] = utc_now_precise()
    _write_local_json(_live_registry_file(), registry)


def _observe_remote_lamport(snapshots, client_id, initialization):
    """Advance the local Lamport clock from every observed remote field version."""
    registry = _read_live_registry(client_id, initialization)
    current_lc = _safe_int(registry.get("lc"), 0)
    max_remote_lc = current_lc

    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        for collection in ("movies", "episodes"):
            records = snapshot.get(collection) if isinstance(snapshot.get(collection), list) else []
            for record in records:
                versions = record.get("field_versions") if isinstance(record, dict) else {}
                if not isinstance(versions, dict):
                    continue
                for version in versions.values():
                    if isinstance(version, dict):
                        max_remote_lc = max(max_remote_lc, _safe_int(version.get("lc"), 0))

    if max_remote_lc > current_lc:
        registry["lc"] = max_remote_lc
        registry["updated_at"] = utc_now_precise()
        _write_local_json(_live_registry_file(), registry)

    return registry


def build_live_report(show_notification=False, collect_first=True, suppress_local_changes=False, notify_local_changes=False):
    settings = addon_settings()
    if collect_first:
        collect_live_snapshot(show_notification=False, suppress_local_changes=suppress_local_changes, notify_local_changes=notify_local_changes)
    snapshots, errors = read_remote_snapshots(settings["base_path"])
    client_id = get_or_create_client_id()
    shared_for_lamport = read_shared_config(settings["base_path"])
    _observe_remote_lamport(
        snapshots,
        client_id,
        shared_for_lamport.get("initialization") or {},
    )
    if len(snapshots) < 2:
        raise RuntimeError("Synchronizacja bieżąca wymaga co najmniej dwóch poprawnych snapshotów.")
    waiting = _snapshots_ready_for_live(snapshots)
    if waiting:
        report = {
            "format": "SudoSync live report",
            "schema_version": LIVE_SCHEMA_VERSION,
            "mode": "live_waiting",
            "generated_at": utc_now_precise(),
            "waiting_for_clients": waiting,
            "snapshot_read_errors": errors,
            "planned_changes": {},
            "summary": {"waiting_clients": len(waiting)},
        }
    else:
        report = build_live_plan(snapshots)
        report.update({
            "format": "SudoSync live report",
            "schema_version": LIVE_SCHEMA_VERSION,
            "generated_at": utc_now_precise(),
            "snapshot_read_errors": errors,
        })
    client_id = get_or_create_client_id()
    path = live_report_path(settings["base_path"], settings["client_name"], client_id)
    _write_vfs_json(path, report)
    status = read_local_status()
    status["last_live_plan_at"] = report.get("generated_at") or ""
    status["last_live_report_path"] = path
    status["live_waiting_clients"] = report.get("waiting_for_clients") or []
    local_plan = (report.get("planned_changes") or {}).get(client_id) or []
    status["live_local_plan"] = _planned_fields_count(local_plan)
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)
    return report


def run_live_sync_cycle(show_notification=False, suppress_local_changes=False, collect_local=True, skip_if_no_remote_change=False, notify_local_changes=False, receiver_notification_delay_ms=0):
    """Run one complete peer synchronization cycle on THIS Kodi only."""
    settings = addon_settings()
    # Sync our settings alias quietly in the background
    try:
        get_or_sync_network_id_and_alias(settings["base_path"])
    except Exception:
        pass
    shared = read_shared_config(settings["base_path"])
    initialization = shared.get("initialization") or {}
    client_id = get_or_create_client_id()
    registry = _read_live_registry(client_id, initialization)
    if initialization.get("completed", False) and not registry.get("bootstrapped"):
        return {"active": False, "reason": "client_needs_enrollment"}
    if not initialization.get("completed", False):
        return {"active": False, "reason": "initialization_not_completed"}
    if not settings.get("live_sync_enabled", True):
        return {"active": False, "reason": "live_sync_disabled"}
    try:
        if xbmc.Player().isPlayingVideo():
            # Remote writes stay forbidden during playback, but publishing our own
            # observed state is safe and improves resilience on Android/Google TV
            # where Kodi may be killed shortly after playback ends.
            if collect_local:
                collect_live_snapshot(show_notification=False, suppress_local_changes=suppress_local_changes, notify_local_changes=False)
            return {
                "active": True,
                "playing": True,
                "local_published": bool(collect_local),
                "message": "Zdalne zapisy LIVE są odłożone do zakończenia odtwarzania.",
            }
    except Exception:
        pass
    client_id = get_or_create_client_id()
    pulses = None
    if skip_if_no_remote_change and not collect_local:
        changed, pulses = _remote_pulse_changed(settings["base_path"], client_id)
        if not changed:
            return {"active": True, "idle": True, "applied_items": 0, "post_plan": {}}
    if not _acquire_live_lock():
        return {"active": True, "busy": True, "message": "Inny cykl SudoSync już trwa na tym Kodi."}

    try:
        report = build_live_report(show_notification=False, collect_first=collect_local, suppress_local_changes=suppress_local_changes, notify_local_changes=notify_local_changes)
        waiting = report.get("waiting_for_clients") or []
        if pulses is None:
            pulses = _read_live_pulses(settings["base_path"])
        _save_peer_pulses(pulses)
        if waiting:
            return {"active": True, "waiting": True, "waiting_for_clients": waiting, "applied_items": 0}
        if report.get("snapshot_read_errors"):
            raise RuntimeError("Błąd odczytu snapshotów: {}".format(report.get("snapshot_read_errors")))

        items = (report.get("planned_changes") or {}).get(client_id) or []
        if not items:
            status = read_local_status()
            status["last_live_sync_at"] = utc_now_precise()
            status["last_live_applied_items"] = 0
            status["last_live_error"] = ""
            _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)
            return {"active": True, "applied_items": 0, "post_plan": {}}

        current_snapshot = snapshot_path(settings["base_path"], settings["client_name"], client_id)
        if xbmcvfs.exists(current_snapshot):
            _write_vfs_json(_live_backup_path(settings["base_path"], settings["client_name"], client_id), _read_vfs_json(current_snapshot))

        apply_report = {
            "format": "SudoSync live apply report",
            "schema_version": LIVE_SCHEMA_VERSION,
            "generated_at": utc_now_precise(),
            "client": {"id": client_id, "name": settings["client_name"]},
            "planned_counts": _planned_fields_count(items),
            "results": [],
            "errors": [],
        }
        for index, item in enumerate(items, 1):
            try:
                safe_item, guarded_fields = _guard_live_item_against_playback_regression(item)
                safe_changes = safe_item.get("changes") if isinstance(safe_item.get("changes"), dict) else {}
                if safe_changes:
                    rpc_result = _apply_one_planned_item(safe_item)
                    _accept_remote_into_registry(safe_item)
                else:
                    rpc_result = {
                        "skipped": True,
                        "reason": "newer_local_playback_protected",
                    }
                apply_report["results"].append({
                    "index": index,
                    "display": item.get("display") or "",
                    "type": item.get("type") or "",
                    "local": item.get("local") or {},
                    "changes": safe_changes,
                    "guarded_local_fields": guarded_fields,
                    "rpc": rpc_result,
                })
            except Exception as exc:
                apply_report["errors"].append({"index": index, "display": item.get("display") or "", "error": str(exc)})
                break

        apply_report["completed"] = not apply_report["errors"] and len(apply_report["results"]) == len(items)
        apply_report["finished_at"] = utc_now_precise()
        # Refresh our snapshot using registry versions already accepted above; this
        # prevents remote writes from being re-published as fresh local changes.
        collect_live_snapshot(show_notification=False, suppress_local_changes=False)
        post = build_live_report(show_notification=False, collect_first=False)
        post_items = (post.get("planned_changes") or {}).get(client_id) or []
        apply_report["post_plan"] = _planned_fields_count(post_items)
        apply_path = live_report_path(settings["base_path"], settings["client_name"], client_id).replace("--live.json", "--live-apply.json")
        _write_vfs_json(apply_path, apply_report)

        applied_results = [result for result in apply_report["results"] if result.get("changes")]
        guarded_count = sum(1 for result in apply_report["results"] if result.get("guarded_local_fields"))
        apply_report["applied_items"] = len(applied_results)
        apply_report["guarded_items"] = guarded_count
        # Rewrite after derived counters are known.
        _write_vfs_json(apply_path, apply_report)

        status = read_local_status()
        status["last_live_sync_at"] = apply_report["finished_at"]
        status["last_live_applied_items"] = len(applied_results)
        status["last_live_guarded_items"] = guarded_count
        status["last_live_apply_report"] = apply_path
        status["last_live_error"] = apply_report["errors"][0]["error"] if apply_report["errors"] else ""
        _write_local_json(os.path.join(_addon_profile_path(), "status.json"), status)

        if apply_report["errors"]:
            raise RuntimeError("Synchronizacja LIVE przerwana: {}".format(apply_report["errors"][0]["error"]))
        applied_counts = _planned_fields_count([
            {"changes": result.get("changes") or {}} for result in applied_results
        ]) if applied_results else {"items": 0}
        if (show_notification or settings.get("notifications")) and (applied_results or guarded_count):
            parts = []
            if applied_results:
                parts.append("zaktualizowano {} poz. z innego Kodi ({})".format(
                    len(applied_results),
                    _live_change_labels(applied_counts),
                ))
            if guarded_count:
                parts.append("ochroniono nowszy stan lokalny: {}".format(guarded_count))
            delay_ms = max(0, int(receiver_notification_delay_ms or 0))
            if delay_ms:
                xbmc.sleep(delay_ms)
            xbmcgui.Dialog().notification(
                "SudoSync — odebrano zmiany",
                "; ".join(parts),
                xbmcgui.NOTIFICATION_INFO,
                8000,
            )
        return {
            "active": True,
            "applied_items": len(applied_results),
            "applied_fields": applied_counts,
            "guarded_items": guarded_count,
            "post_plan": apply_report["post_plan"],
            "report_path": apply_path,
        }
    finally:
        _release_live_lock()

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



def _read_vfs_all(path, chunk_size=1024 * 1024):
    """Read an arbitrary-size VFS file without assuming local filesystem access."""
    f = xbmcvfs.File(path)
    parts = []
    try:
        while True:
            chunk = f.readBytes(chunk_size)
            if not chunk:
                break
            if isinstance(chunk, bytearray):
                chunk = bytes(chunk)
            elif not isinstance(chunk, bytes):
                chunk = str(chunk).encode("utf-8", errors="replace")
            parts.append(chunk)
            if len(chunk) < chunk_size:
                break
    finally:
        f.close()
    return b"".join(parts)


def _read_vfs_json(path):
    raw = _read_vfs_all(path)
    if not raw:
        raise ValueError("Empty JSON file: {}".format(path))
    return json.loads(raw.decode("utf-8-sig", errors="strict"))


def _validate_remote_snapshot(data):
    """Validate the actual SudoSync client snapshot schema used on NAS."""
    if not isinstance(data, dict):
        return "not a JSON dictionary"
    if data.get("format") != "SudoSync client snapshot":
        return "not a SudoSync client snapshot"
    if _safe_int(data.get("schema_version"), 0) < 1:
        return "unsupported schema_version"

    client = data.get("client")
    if not isinstance(client, dict):
        return "missing or invalid client payload"
    client_id = client.get("id")
    if not isinstance(client_id, str) or len(client_id) < 10:
        return "missing or invalid client.id"

    for collection in ("movies", "episodes"):
        values = data.get(collection)
        if not isinstance(values, list):
            return "missing or invalid {} collection".format(collection)
        for index, record in enumerate(values):
            if not isinstance(record, dict):
                return "invalid {} record at index {}".format(collection, index)

    return None


def read_remote_snapshots(base_path=None):
    settings = addon_settings()
    base = (base_path or settings["base_path"]).rstrip("/") + "/"
    clients_path = _join_vfs(base, "clients")
    if not xbmcvfs.exists(clients_path):
        return [], []
    try:
        _dirs, files = xbmcvfs.listdir(clients_path)
    except Exception as exc:
        raise IOError("Cannot list SudoSync clients: {}".format(exc))

    snapshots = []
    errors = []
    for filename in sorted(files):
        if not (filename or "").lower().endswith(".json"):
            continue
        path = clients_path + filename
        try:
            data = _read_vfs_json(path)
            error = _validate_remote_snapshot(data)
            if error:
                errors.append({"file": filename, "error": error})
                continue
            try:
                st = xbmcvfs.Stat(path)
                data["mtime"] = st.st_mtime()
            except Exception:
                pass
            snapshots.append(data)
        except Exception as exc:
            errors.append({"file": filename, "error": str(exc)})
    return snapshots, errors


def dry_run_report_path(base_path, client_name, client_id):
    reports = _join_vfs(base_path, "reports")
    return reports + "{}--{}--dryrun.json".format(_slug(client_name), client_id[:8])


def run_dry_run(show_notification=False):
    """Build a synchronization plan from all client snapshots. Never modifies Kodi library data."""
    settings = addon_settings()
    client_id = get_or_create_client_id()
    base = settings["base_path"]
    ensure_remote_layout(base)
    reports = _join_vfs(base, "reports")
    if not xbmcvfs.exists(reports):
        xbmcvfs.mkdirs(reports)

    snapshots, errors = read_remote_snapshots(base)
    if len(snapshots) < 2:
        raise RuntimeError("DRY RUN requires at least two valid client snapshots; found {}".format(len(snapshots)))

    shared_config = sync_initial_base_selection(show_notification=False)
    initialization = shared_config.get("initialization") or {}
    base_client_id = str(initialization.get("base_client_id") or "")
    waiting_live = _snapshots_ready_for_live(snapshots) if initialization.get("completed", False) else []
    if initialization.get("completed", False) and not waiting_live:
        report = build_live_plan(snapshots)
    elif initialization.get("completed", False) and waiting_live:
        # Never feed mixed 0.3/0.4 snapshots back into the first-import merger.
        # LIVE snapshots represent "no rating" as 0, while first-import snapshots
        # use null; mixing them used to create huge bogus null -> 0 DRY RUN plans.
        report = {
            "mode": "live_waiting",
            "clients": [
                {
                    "id": str((snap.get("client") or {}).get("id") or ""),
                    "name": str((snap.get("client") or {}).get("name") or "Kodi"),
                    "generated_at": snap.get("generated_at") or "",
                    "counts": snap.get("counts") or {},
                    "planned_changes": {},
                }
                for snap in snapshots
            ],
            "summary": {"waiting_clients": len(waiting_live)},
            "planned_changes": {},
            "waiting_for_clients": list(waiting_live),
        }
    else:
        report = build_dry_run(snapshots, base_client_id=base_client_id)
    report["initialization"] = dict(initialization)
    if waiting_live:
        report["waiting_for_live_clients"] = waiting_live
    report["format"] = "SudoSync dry-run report"
    report["schema_version"] = DRY_RUN_REPORT_SCHEMA
    report["generated_at"] = utc_now()
    report["snapshot_read_errors"] = errors
    report["generated_by"] = {
        "client_id": client_id,
        "client_name": settings["client_name"],
    }
    path = dry_run_report_path(base, settings["client_name"], client_id)
    _write_vfs_json(path, report)

    summary = report.get("summary") or {}
    this_client = None
    for client in report.get("clients") or []:
        if client.get("id") == client_id:
            this_client = client
            break
    local_status = read_local_status()
    local_status["last_dry_run_at"] = report["generated_at"]
    local_status["dry_run_report_path"] = path
    local_status["dry_run_summary"] = summary
    local_status["dry_run_local_plan"] = (this_client or {}).get("planned_changes") or {}
    local_status["dry_run_snapshot_errors"] = errors
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), local_status)

    log(
        "DRY RUN: {} shared safe, {} ambiguous, {} rating conflicts -> {}".format(
            summary.get("safe_shared_clusters", 0),
            summary.get("ambiguous_clusters", 0),
            summary.get("rating_conflicts", 0),
            path,
        )
    )
    if show_notification:
        plan = (this_client or {}).get("planned_changes") or {}
        xbmcgui.Dialog().notification(
            "SudoSync — DRY RUN",
            "Wspólne: {} | niejednoznaczne: {} | zmiany tutaj: {}".format(
                summary.get("safe_shared_clusters", 0),
                summary.get("ambiguous_clusters", 0),
                plan.get("items", 0),
            ),
            xbmcgui.NOTIFICATION_INFO,
            7000,
        )
    return report



def _backup_snapshot_path(base_path, client_name, client_id, stamp=None):
    backups = _join_vfs(base_path, "backups")
    client_dir = _join_vfs(backups, "{}--{}".format(_slug(client_name), client_id[:8]))
    if not xbmcvfs.exists(backups):
        xbmcvfs.mkdirs(backups)
    if not xbmcvfs.exists(client_dir):
        xbmcvfs.mkdirs(client_dir)
    stamp = stamp or utc_now().replace(":", "-")
    return client_dir + "pre-initial-sync--{}.json".format(stamp)


def _write_apply_report(base_path, client_name, client_id, report):
    reports = _join_vfs(base_path, "reports")
    if not xbmcvfs.exists(reports):
        xbmcvfs.mkdirs(reports)
    path = reports + "{}--{}--initial-apply.json".format(_slug(client_name), client_id[:8])
    _write_vfs_json(path, report)
    return path


def _planned_fields_count(items):
    counts = {"items": len(items), "playcount": 0, "lastplayed": 0, "userrating": 0, "resume": 0}
    for item in items:
        changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
        for field in ("playcount", "lastplayed", "userrating", "resume"):
            if field in changes:
                counts[field] += 1
    return counts


def _apply_one_planned_item(item):
    media_type = str(item.get("type") or "")
    local = item.get("local") if isinstance(item.get("local"), dict) else {}
    changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
    if media_type == "movie":
        local_id = _safe_int(local.get("movieid"), -1)
        if local_id < 0:
            raise ValueError("Brak poprawnego movieid dla {}".format(item.get("display") or "filmu"))
        method = "VideoLibrary.SetMovieDetails"
        params = {"movieid": local_id}
    elif media_type == "episode":
        local_id = _safe_int(local.get("episodeid"), -1)
        if local_id < 0:
            raise ValueError("Brak poprawnego episodeid dla {}".format(item.get("display") or "odcinka"))
        method = "VideoLibrary.SetEpisodeDetails"
        params = {"episodeid": local_id}
    else:
        raise ValueError("Nieobsługiwany typ materiału: {}".format(media_type))

    for field in ("playcount", "lastplayed", "userrating", "resume"):
        if field not in changes:
            continue
        target = changes[field].get("to") if isinstance(changes[field], dict) else None
        if field == "playcount":
            params[field] = _safe_int(target, 0)
        elif field == "userrating":
            if target is not None:
                params[field] = _safe_int(target, 0)
        elif field == "lastplayed":
            params[field] = str(target or "")
        elif field == "resume":
            target = target if isinstance(target, dict) else {}
            params[field] = {
                "position": _safe_float(target.get("position"), 0.0),
                "total": _safe_float(target.get("total"), 0.0),
            }

    if len(params) <= 1:
        return {"skipped": True, "method": method, "params": params}
    result = rpc(method, params)
    return {"skipped": False, "method": method, "params": params, "result": result}


def apply_initial_sync(show_notification=False):
    """Apply ONLY the current initial DRY-RUN plan to this Kodi.

    Safety properties:
      * explicit short-lived local write guard is required;
      * fresh local snapshot is collected immediately before planning;
      * snapshot read errors or first-import conflicts block all writes;
      * complete pre-write local snapshot is copied to NAS backups;
      * ambiguous/unidentified items never appear in the plan and are untouched;
      * no automatic/live synchronization is enabled by this function.
    """
    settings = addon_settings()
    if not is_initial_write_guard_armed():
        raise RuntimeError(
            "Bezpiecznik zapisu nie jest uzbrojony lub wygasł. Kliknij najpierw „Uzbrój jednorazową synchronizację początkową (10 min)”."
        )

    shared_config = sync_initial_base_selection(show_notification=False)
    initialization = shared_config.get("initialization") or {}

    if not initialization.get("base_client_id"):
        disarm_initial_write_guard()
        raise RuntimeError("Nie wybrano urządzenia bazowego. Użyj przycisku „Ustaw TO Kodi jako urządzenie bazowe pierwszej synchronizacji” na jednym nazwanym Kodi.")

    # One-shot guard: consume it when the real apply attempt starts.
    disarm_initial_write_guard()

    # Refresh this client immediately before planning.
    fresh_status = collect_and_write(show_notification=False)
    report = run_dry_run(show_notification=False)
    if report.get("snapshot_read_errors"):
        raise RuntimeError("Nie można rozpocząć zapisu: występują błędy odczytu snapshotów.")
    summary = report.get("summary") or {}
    if summary.get("rating_conflicts", 0) or summary.get("resume_conflicts", 0):
        raise RuntimeError("Nie można rozpocząć zapisu: DRY RUN zawiera nierozstrzygnięte konflikty ocen lub resume.")

    client_id = get_or_create_client_id()
    items = (report.get("planned_changes") or {}).get(client_id) or []
    counts = _planned_fields_count(items)
    if not items:
        # A new client can legitimately have nothing to apply. That still means
        # its enrollment is complete: it has published a fresh LIVE snapshot and
        # the current state is already converged.
        registry = _read_live_registry(client_id, initialization)
        registry["bootstrapped"] = True
        registry["updated_at"] = utc_now_precise()
        _write_local_json(_live_registry_file(), registry)
        return {
            "applied": False,
            "nothing_to_do": True,
            "enrolled": True,
            "counts": counts,
            "message": "To Kodi jest już zgodne z bezpiecznym stanem początkowym.",
        }

    # Backup the exact fresh local snapshot that the plan is based on.
    remote_snapshot = fresh_status.get("remote_path")
    snapshot_data = _read_vfs_json(remote_snapshot)
    backup_path = _backup_snapshot_path(settings["base_path"], settings["client_name"], client_id)
    _write_vfs_json(backup_path, snapshot_data)

    apply_report = {
        "format": "SudoSync initial apply report",
        "schema_version": 1,
        "generated_at": utc_now(),
        "client": {"id": client_id, "name": settings["client_name"]},
        "backup_path": backup_path,
        "pre_apply_snapshot": remote_snapshot,
        "planned_counts": counts,
        "results": [],
        "errors": [],
        "completed": False,
    }

    for index, item in enumerate(items, 1):
        try:
            result = _apply_one_planned_item(item)
            apply_report["results"].append({
                "index": index,
                "display": item.get("display") or "",
                "type": item.get("type") or "",
                "local": item.get("local") or {},
                "changes": item.get("changes") or {},
                "rpc": result,
            })
        except Exception as exc:
            apply_report["errors"].append({
                "index": index,
                "display": item.get("display") or "",
                "type": item.get("type") or "",
                "local": item.get("local") or {},
                "error": str(exc),
            })
            # Stop immediately. Some earlier writes may have succeeded, so preserve
            # the report and snapshot and do not pretend the operation was atomic.
            break

    apply_report["completed"] = not apply_report["errors"] and len(apply_report["results"]) == len(items)
    apply_report["finished_at"] = utc_now()
    report_path = _write_apply_report(settings["base_path"], settings["client_name"], client_id, apply_report)

    # Always refresh afterwards so the next DRY RUN shows the real resulting state.
    post_status = collect_and_write(show_notification=False)
    post_dry = run_dry_run(show_notification=False)
    initialization_completed = _mark_initialization_completed_if_converged(post_dry, settings["base_path"])
    post_plan = None
    for client in post_dry.get("clients") or []:
        if client.get("id") == client_id:
            post_plan = client.get("planned_changes") or {}
            break

    apply_report["apply_report_path"] = report_path
    apply_report["post_snapshot"] = post_status.get("remote_path")
    apply_report["post_plan"] = post_plan or {}
    apply_report["post_dry_run_at"] = post_dry.get("generated_at") or ""
    _write_apply_report(settings["base_path"], settings["client_name"], client_id, apply_report)

    # The short-lived guard was consumed before any writes.

    local_status = read_local_status()
    local_status["last_initial_apply_at"] = apply_report["finished_at"]
    local_status["last_initial_apply_completed"] = apply_report["completed"]
    local_status["last_initial_apply_report"] = report_path
    local_status["last_initial_apply_backup"] = backup_path
    local_status["last_initial_apply_post_plan"] = post_plan or {}
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), local_status)
    
    if apply_report["errors"]:
        first = apply_report["errors"][0]
        raise RuntimeError(
            "Synchronizacja została przerwana na pozycji {}: {}. Backup: {}. Raport: {}".format(
                first.get("display") or first.get("index"), first.get("error"), backup_path, report_path
            )
        )

    if apply_report["completed"]:
        registry = _read_live_registry(client_id, initialization)
        registry["bootstrapped"] = True
        registry["updated_at"] = utc_now_precise()
        _write_local_json(_live_registry_file(), registry)

    if show_notification or settings.get("notifications"):
        xbmcgui.Dialog().notification(
            "SudoSync — synchronizacja początkowa",
            "Zastosowano {} pozycji. Pozostały plan na tym Kodi: {}.".format(
                len(items), (post_plan or {}).get("items", 0)
            ),
            xbmcgui.NOTIFICATION_INFO,
            9000,
        )
    return {
        "applied": True,
        "completed": apply_report["completed"],
        "counts": counts,
        "backup_path": backup_path,
        "report_path": report_path,
        "post_plan": post_plan or {},
        "initialization_completed": initialization_completed,
    }

import hashlib

def get_or_sync_network_id_and_alias(base_path=None):
    addon = xbmcaddon.Addon(ADDON_ID)
    settings = addon_settings()
    base = base_path or settings["base_path"]
    config = read_shared_config(base)
    
    local_prefix = addon.getSetting("sudosync_id_prefix").strip()
    last_synced = addon.getSetting("sudosync_id_prefix_last_synced").strip()
    
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

def assign_sudosync_ids(show_notification=True):
    settings = addon_settings()
    if not settings.get("sudosync_id_enabled"):
        raise RuntimeError("Generowanie SudoSync ID jest wyłączone w ustawieniach.")

    movies, episodes = collect_library(first_import=False)
    prefix = get_or_sync_network_id_and_alias(settings["base_path"])
        
    modified_movies = 0
    modified_episodes = 0

    def _process_item(item, kind):
        if item.get("ids"):
            return False
        file_path = item.get("file")
        if not file_path:
            return False
            
        item_id = uuid.uuid4().hex[:12]
        full_id = "{}-{}".format(prefix, item_id)
        
        import xml.sax.saxutils
        escaped_full_id = xml.sax.saxutils.escape(full_id)
        
        best_path, _, _ = _find_best_nfo(file_path, kind)
        if not best_path:
            return False
                
        if xbmcvfs.exists(best_path):
            try:
                raw = _read_vfs_all(best_path)
                if not raw:
                    return False
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                else:
                    raw = bytearray(raw)

                if b"<uniqueid" in raw.lower():
                    return False

                closing_tag = b"</movie>" if kind == "movie" else b"</episodedetails>"
                pos = raw.lower().rfind(closing_tag)
                if pos < 0:
                    return False

                insert = ('    <uniqueid type="sudosync" default="true">' + escaped_full_id + '</uniqueid>\n').encode("utf-8")
                new_raw = raw[:pos] + insert + raw[pos:]

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
                    log("Blad przenoszenia nowej wersji pliku: {}".format(best_path), xbmc.LOGWARNING)
                    if xbmcvfs.exists(new_path):
                        xbmcvfs.delete(new_path)
                    return False
                
                if xbmcvfs.exists(new_path):
                    xbmcvfs.delete(new_path)

                _refresh_item(item, kind)
                return True
            except Exception as exc:
                log("Błąd zapisu NFO dla {}: {}".format(best_path, exc), xbmc.LOGWARNING)
                return False
        else:
            root_tag = "movie" if kind == "movie" else "episodedetails"
            title = item.get("title") or item.get("originaltitle") or "Nieznany"
            title = xml.sax.saxutils.escape(title)
            xml_content = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<{root}>\n  <title>{title}</title>\n  <uniqueid type="sudosync" default="true">{id}</uniqueid>\n</{root}>\n'.format(root=root_tag, id=escaped_full_id, title=title)
            try:
                _write_vfs_bytes(best_path, xml_content)
                _refresh_item(item, kind)
                return True
            except Exception as exc:
                log("Błąd tworzenia NFO dla {}: {}".format(best_path, exc), xbmc.LOGWARNING)
                return False

    def _refresh_item(item, kind):
        try:
            if kind == "movie":
                rpc("VideoLibrary.RefreshMovie", {"movieid": item.get("local", {}).get("movieid")})
            else:
                rpc("VideoLibrary.RefreshEpisode", {"episodeid": item.get("local", {}).get("episodeid")})
        except Exception:
            pass

    for m in movies:
        if _process_item(m, "movie"):
            modified_movies += 1

    for e in episodes:
        if _process_item(e, "episode"):
            modified_episodes += 1

    _NFO_ID_CACHE.clear()

    return {"movies": modified_movies, "episodes": modified_episodes}

