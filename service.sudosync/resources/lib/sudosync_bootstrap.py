# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import os
import re
import time
import json
import xbmc
import xbmcgui
import xbmcvfs
import xbmcaddon

ADDON_ID = "service.sudosync"
# Shared folder on the network share. 2.2.12 replaced the sequential
# <uniqueid type="sudosync"> with <uniqueid type="kodibridge"> (UUID) and starts
# the shared state from scratch in .KodiBridge next to the old .SudoSync.
SHARED_DIR_NAME = ".KodiBridge"
LEGACY_SHARED_DIR_NAME = ".SudoSync"
_VFS_CRED_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s]+@)")

def mask_vfs_credentials(text):
    """Usuwa poświadczenia user[:password]@ ze ścieżek VFS przed wyświetleniem lub logowaniem."""
    if not isinstance(text, str) or "@" not in text:
        return text
    return _VFS_CRED_RE.sub(r"\1", text)

def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[SudoSync Bootstrap] {}".format(mask_vfs_credentials(str(msg))), level)

def get_addon_setting(key):
    return xbmcaddon.Addon(ADDON_ID).getSetting(key)

def set_addon_setting(key, value):
    xbmcaddon.Addon(ADDON_ID).setSetting(key, str(value))

def get_base_path():
    path = get_addon_setting("base_path").strip()
    return path

def set_base_path(path):
    set_addon_setting("base_path", path)
    log("Ustalono nową ścieżkę bazową: {}".format(path))

def _bootstrap_win_unc(path):
    """Return a native Windows UNC path inside real Kodi on Windows when the share is accessible."""
    import sys
    if os.name != "nt" or "unittest" in sys.modules or "pytest" in sys.modules:
        return None
    try:
        from resources.lib.sudosync_v2.kodi_vfs import _resolve_win_unc
        return _resolve_win_unc(path, allow_win_unc=True)
    except Exception:
        return None


def _b_exists(path):
    unc = _bootstrap_win_unc(path)
    if unc is not None:
        return os.path.exists(unc)
    if xbmcvfs.exists(path):
        return True
    stripped = mask_vfs_credentials(path)
    if stripped != path:
        return bool(xbmcvfs.exists(stripped))
    return False


def _b_mkdirs(path):
    unc = _bootstrap_win_unc(path)
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


def _b_delete(path):
    unc = _bootstrap_win_unc(path)
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


def test_vfs_rw(test_dir):
    try:
        unc = _bootstrap_win_unc(test_dir)
        if unc is not None:
            if not os.path.exists(unc):
                os.makedirs(unc, exist_ok=True)
            test_file = os.path.join(unc, "rw_test.tmp")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("test")
            with open(test_file, "r", encoding="utf-8") as f:
                data = f.read()
            os.remove(test_file)
            if data != "test":
                return False, "Błąd weryfikacji zapisu/odczytu"
            return True, "OK"

        if not _b_exists(test_dir):
            if not _b_mkdirs(test_dir):
                return False, "Nie można utworzyć folderu współdzielonego"
        
        test_file = test_dir + "rw_test.tmp"
        f = xbmcvfs.File(test_file, 'w')
        f.write("test")
        f.close()
        
        f = xbmcvfs.File(test_file, 'r')
        data = f.read()
        f.close()
        
        xbmcvfs.delete(test_file)
        
        if data != "test":
            return False, "Błąd weryfikacji zapisu/odczytu"
            
        return True, "OK"
    except Exception as exc:
        return False, str(exc)

MAX_BOOTSTRAP_JSON_BYTES = 8 * 1024 * 1024


def _read_json_file(filepath):
    unc = _bootstrap_win_unc(filepath)
    if unc is not None:
        if not os.path.exists(unc):
            return "NOT_FOUND", None
        try:
            if os.path.getsize(unc) > MAX_BOOTSTRAP_JSON_BYTES:
                log("JSON file too large, ignored: {}".format(mask_vfs_credentials(filepath)), xbmc.LOGERROR)
                return "INVALID", None
            with open(unc, "r", encoding="utf-8-sig") as f:
                data = f.read()
        except Exception as exc:
            log("Cannot read JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
            return "IO_ERROR", None
        try:
            obj = json.loads(data)
            return "VALID", obj
        except (ValueError, TypeError) as exc:
            log("Cannot parse JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
            return "INVALID", None

    if not _b_exists(filepath):
        return "NOT_FOUND", None
    try:
        f = xbmcvfs.File(filepath, 'r')
        try:
            try:
                size = f.size()
            except Exception:
                size = None
            if isinstance(size, int) and not isinstance(size, bool) and size > MAX_BOOTSTRAP_JSON_BYTES:
                log("JSON file too large, ignored: {}".format(mask_vfs_credentials(filepath)), xbmc.LOGERROR)
                return "INVALID", None
            data = f.read()
        finally:
            f.close()
    except Exception as exc:
        log("Cannot read JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return "IO_ERROR", None
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8-sig", errors="replace")
    if isinstance(data, str) and len(data) > MAX_BOOTSTRAP_JSON_BYTES:
        log("JSON file too large, ignored: {}".format(mask_vfs_credentials(filepath)), xbmc.LOGERROR)
        return "INVALID", None

    try:
        obj = json.loads(data)
        return "VALID", obj
    except (ValueError, TypeError) as exc:
        log("Cannot parse JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return "INVALID", None

def _remove_quietly(path):
    try:
        if _b_exists(path):
            _b_delete(path)
    except Exception as exc:
        log("Cannot remove {}: {}".format(path, exc), xbmc.LOGWARNING)


def _read_back_size(path):
    """Return the number of bytes readable from ``path`` (0 when empty/unreadable)."""
    unc = _bootstrap_win_unc(path)
    if unc is not None:
        try:
            return os.path.getsize(unc) if os.path.exists(unc) else 0
        except Exception:
            return 0
    handle = None
    try:
        handle = xbmcvfs.File(path, 'r')
        data = handle.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return len(data or b"")
    except Exception as exc:
        log("Cannot read back {}: {}".format(path, exc), xbmc.LOGERROR)
        return 0
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _write_json_file(filepath, obj):
    """Write JSON atomically: verified .tmp -> confirmed .bak -> swap (with restore on failure).

    The original file is deleted only after the .bak copy has been confirmed,
    and it is restored from .bak if the final rename fails.
    """
    unc = _bootstrap_win_unc(filepath)
    if unc is not None:
        tmp_unc = unc + ".tmp"
        try:
            data = json.dumps(obj, indent=2, sort_keys=True)
            with open(tmp_unc, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
            if not os.path.exists(tmp_unc) or os.path.getsize(tmp_unc) <= 0:
                if os.path.exists(tmp_unc):
                    os.remove(tmp_unc)
                return False
            os.replace(tmp_unc, unc)
            return True
        except Exception as exc:
            log("Cannot write JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
            try:
                if os.path.exists(tmp_unc):
                    os.remove(tmp_unc)
            except Exception:
                pass
            return False

    tmp_file = filepath + ".tmp"
    bak_file = filepath + ".bak"
    try:
        data = json.dumps(obj, indent=2, sort_keys=True)

        # 1. Write and verify the temporary file (must exist and be non-empty).
        handle = xbmcvfs.File(tmp_file, 'w')
        try:
            written = handle.write(data)
        finally:
            handle.close()
        if not written or not xbmcvfs.exists(tmp_file) or _read_back_size(tmp_file) <= 0:
            log("Nie udało się zapisać poprawnego pliku .tmp dla {}".format(filepath), xbmc.LOGERROR)
            _remove_quietly(tmp_file)
            return False

        # 2. Back up the original and check the result of the copy.
        had_original = bool(xbmcvfs.exists(filepath))
        if had_original:
            _remove_quietly(bak_file)
            if not xbmcvfs.copy(filepath, bak_file):
                log("Nie udało się utworzyć kopii .bak dla {} - oryginał zachowany, zapis przerwany".format(filepath),
                    xbmc.LOGERROR)
                _remove_quietly(tmp_file)
                return False

            # 3. Only now is it safe to remove the original.
            if not xbmcvfs.delete(filepath):
                log("Nie udało się usunąć oryginału {} przed podmianą - zapis przerwany".format(filepath),
                    xbmc.LOGERROR)
                _remove_quietly(tmp_file)
                return False

        if xbmcvfs.rename(tmp_file, filepath):
            _remove_quietly(tmp_file)
            return True

        log("Nie udało się podmienić {} - przywracam z .bak".format(filepath), xbmc.LOGERROR)
        if had_original:
            if not xbmcvfs.copy(bak_file, filepath):
                xbmcvfs.rename(bak_file, filepath)
        _remove_quietly(tmp_file)
        return False

    except Exception as exc:
        log("Cannot write JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return False

def read_sudosync_cfg(base_path):
    cfg_path = base_path + "SudoSync.cfg"
    return _read_json_file(cfg_path)

def write_sudosync_cfg(base_path, config_data):
    cfg_path = base_path + "SudoSync.cfg"
    return _write_json_file(cfg_path, config_data)

def kodibridge_shared_path(base_path):
    """Return the .KodiBridge folder for a base path ending in .SudoSync, else None."""
    path = str(base_path or "").rstrip("/\\")
    cut = max(path.rfind("/"), path.rfind("\\"))
    if path[cut + 1:].casefold() != LEGACY_SHARED_DIR_NAME.casefold():
        return None
    # The rest of the add-on appends "/" to the base path, so do the same.
    return path[:cut + 1] + SHARED_DIR_NAME + "/"


def _is_shared_dir(path):
    """True when the folder name at the end of path is .KodiBridge or .SudoSync (any case)."""
    name = str(path or "").rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name in (SHARED_DIR_NAME.casefold(), LEGACY_SHARED_DIR_NAME.casefold())


def migrate_legacy_shared_folder(old_base, new_base):
    """Create the .KodiBridge configuration from the old .SudoSync one.

    Only the network settings are carried over; snapshots, transactions and the
    sequential ID counter stay behind, because they refer to the old IDs. The
    old folder gets min_addon_version of this version, so older add-ons stop
    synchronizing there and offer the update. Returns True when a new
    configuration was written.
    """
    old_base = old_base.rstrip("/\\") + "/"
    new_base = new_base.rstrip("/\\") + "/"
    local_version = _get_local_addon_version()
    written = False
    new_status, _new_cfg = read_sudosync_cfg(new_base)
    old_status, old_cfg = read_sudosync_cfg(old_base)
    if new_status != "VALID":
        if old_status == "VALID" and isinstance(old_cfg, dict) and isinstance(old_cfg.get("network_settings"), dict):
            old_net = old_cfg["network_settings"]
        else:
            # Old SudoSync.cfg missing or damaged: use this Kodi's settings
            # instead of starting the configuration wizard.
            try:
                addon = xbmcaddon.Addon(ADDON_ID)
                old_net = {
                    "sudosync_id_enabled": addon.getSetting("sudosync_id_enabled").strip().lower() == "true",
                    "sudosync_id_prefix": addon.getSetting("sudosync_id_prefix").strip(),
                }
            except Exception:
                old_net = {}
        net = {
            "sudosync_id_enabled": bool(old_net.get("sudosync_id_enabled", False)),
            "sudosync_id_prefix": str(old_net.get("sudosync_id_prefix") or ""),
            "sudosync_id_last_number": 0,
        }
        if local_version:
            net["min_addon_version"] = local_version
        new_cfg = {
            "format": "SudoSync configuration",
            "schema_version": 1,
            "configuration": {
                "completed": True,
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "migrated_from": mask_vfs_credentials(old_base),
            },
            "network_settings": net,
        }
        written = bool(write_sudosync_cfg(new_base, new_cfg))
    if local_version and old_status == "VALID":
        try:
            update_network_min_version(old_base, local_version)
        except Exception as exc:
            log("Cannot raise min_addon_version in {}: {}".format(mask_vfs_credentials(old_base), exc))
    try:
        # The local counter refers to the old sequential IDs.
        xbmcaddon.Addon(ADDON_ID).setSetting("sudosync_id_last_number", "0")
    except Exception:
        pass
    return written


def is_legacy_installation(base_path):
    # Sprawdzamy czy istnieje system/config.json oraz clients/
    sys_config = base_path + "system/config.json"
    clients_dir = base_path + "clients/"
    return _b_exists(sys_config) and _b_exists(clients_dir)

def _get_local_addon_version():
    try:
        raw = xbmcaddon.Addon(ADDON_ID).getAddonInfo("version")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return ""


def _discover_highest_network_addon_version(base_path, cfg=None):
    """Return the highest addon version string recorded across shared network files."""
    from resources.lib.sudosync_core import _numeric_version
    base = (base_path or get_base_path() or "").rstrip("/")
    if not base:
        return ""
    base_slash = base + "/"
    candidates = []

    if not isinstance(cfg, dict):
        st, loaded_cfg = read_sudosync_cfg(base_slash)
        if st == "VALID" and isinstance(loaded_cfg, dict):
            cfg = loaded_cfg

    if isinstance(cfg, dict):
        net = cfg.get("network_settings")
        if isinstance(net, dict):
            ver = str(net.get("min_addon_version") or "").strip()
            if ver:
                candidates.append(ver)

    for lock_name in (ACTIVE_KODI_LOCK_FILENAME, ACTIVE_KODI_LOCK_FILENAME + ".bak"):
        st, lock_doc = _read_json_file(base_slash + lock_name)
        if st == "VALID" and isinstance(lock_doc, dict):
            ver = str(lock_doc.get("addon_version") or "").strip()
            if ver:
                candidates.append(ver)

    st, sys_doc = _read_json_file(base_slash + "system/config.json")
    if st == "VALID" and isinstance(sys_doc, dict):
        sys_net = sys_doc.get("network_settings")
        if isinstance(sys_net, dict):
            ver = str(sys_net.get("min_addon_version") or "").strip()
            if ver:
                candidates.append(ver)
        clients = sys_doc.get("clients")
        if isinstance(clients, dict):
            for cinfo in clients.values():
                if isinstance(cinfo, dict):
                    ver = str(cinfo.get("addon_version") or "").strip()
                    if ver:
                        candidates.append(ver)

    best_ver = ""
    best_num = (0, 0, 0, 3, 0)
    for ver in candidates:
        num = _numeric_version(ver)
        if num > best_num:
            best_num = num
            best_ver = ver
    return best_ver


def is_network_addon_version_newer(base_path=None, cfg=None):
    """Return (is_newer, remote_min, local_version, cfg_dict)."""
    from resources.lib.sudosync_core import _numeric_version
    base = (base_path or get_base_path() or "").rstrip("/")
    local_version = _get_local_addon_version()
    if not base or not local_version:
        return False, "", local_version, cfg if isinstance(cfg, dict) else {}
    base_slash = base + "/"
    if not isinstance(cfg, dict):
        st, loaded_cfg = read_sudosync_cfg(base_slash)
        cfg = loaded_cfg if (st == "VALID" and isinstance(loaded_cfg, dict)) else {}
    remote_min = _discover_highest_network_addon_version(base_slash, cfg=cfg)
    if not remote_min:
        return False, "", local_version, cfg
    is_newer = _numeric_version(local_version) < _numeric_version(remote_min)
    return is_newer, remote_min, local_version, cfg


def update_network_min_version(base_path, new_version):
    """Update min_addon_version in SudoSync.cfg if new_version is newer."""
    from resources.lib.sudosync_core import _numeric_version
    if not base_path or not isinstance(new_version, str) or not new_version.strip():
        return False
    base = base_path.rstrip("/") + "/"
    status, cfg = read_sudosync_cfg(base)
    if status != "VALID" or not isinstance(cfg, dict):
        return False
    net = cfg.setdefault("network_settings", {})
    if not isinstance(net, dict):
        return False
    current_min = str(net.get("min_addon_version") or "").strip()
    if not current_min or _numeric_version(new_version) > _numeric_version(current_min):
        net["min_addon_version"] = new_version.strip()
        return write_sudosync_cfg(base, cfg)
    return False


def enforce_network_version_guard(base_path, cfg=None):
    """Ensure this Kodi's SudoSync version is not older than the highest version in the network.

    If this Kodi has a newer version than recorded in SudoSync.cfg, promotes
    min_addon_version in SudoSync.cfg.
    If another Kodi already promoted min_addon_version above this Kodi's version,
    prompts the user to update (from GitHub or local ZIP) or disables SudoSync
    until updated.
    """
    from resources.lib.sudosync_core import (
        _numeric_version,
        install_github_update,
        install_update_from_zip_path,
    )
    local_version = _get_local_addon_version()
    if not local_version:
        return True

    base = (base_path or get_base_path() or "").rstrip("/") + "/"
    if not isinstance(cfg, dict):
        st, loaded_cfg = read_sudosync_cfg(base)
        cfg = loaded_cfg if (st == "VALID" and isinstance(loaded_cfg, dict)) else {}

    net = cfg.setdefault("network_settings", {}) if isinstance(cfg, dict) else {}
    if not isinstance(net, dict):
        return True

    cfg_min = str(net.get("min_addon_version") or "").strip()
    remote_min = _discover_highest_network_addon_version(base, cfg=cfg) or cfg_min
    local_num = _numeric_version(local_version)
    remote_num = _numeric_version(remote_min) if remote_min else (0, 0, 0, 3, 0)
    cfg_num = _numeric_version(cfg_min) if cfg_min else (0, 0, 0, 3, 0)

    if not remote_min or local_num > remote_num:
        if local_num > (0, 0, 0, 3, 0) and local_num > cfg_num and isinstance(cfg, dict) and cfg.get("configuration"):
            net["min_addon_version"] = local_version
            write_sudosync_cfg(base, cfg)
            log("Zaktualizowano minimalną wersję sieciową SudoSync w SudoSync.cfg: {}".format(local_version))
        return True

    if remote_num > cfg_num and isinstance(cfg, dict) and cfg.get("configuration"):
        net["min_addon_version"] = remote_min
        write_sudosync_cfg(base, cfg)

    if local_num == remote_num:
        return True

    # local_num < remote_num -> inne Kodi w sieci ma już nowszą wersję!
    log(
        "Wykryto nowszą wersję w sieci KodiBridge ({}) niż zainstalowana lokalnie ({}). Wstrzymuję start.".format(
            remote_min, local_version
        ),
        xbmc.LOGERROR,
    )
    dialog = xbmcgui.Dialog()
    if dialog.yesno(
        "KodiBridge — wymagana aktualizacja",
        "Na innym urządzeniu Kodi w tej sieci zainstalowano już nowszą wersję KodiBridge ({remote}), "
        "natomiast tutaj działa starsza wersja ({local}).\n\n"
        "Aby chronić spójność biblioteki, KodiBridge jest wyłączony do czasu aktualizacji. "
        "Bez aktualizacji KodiBridge nie będzie synchronizował tego Kodi, a pytanie pojawi się "
        "ponownie dopiero po następnym uruchomieniu Kodi.\n\n"
        "Czy chcesz pobrać i zainstalować aktualizację z GitHub teraz?".format(
            remote=remote_min, local=local_version
        ),
        yeslabel="Aktualizuj z GitHub",
        nolabel="Inna opcja / Wyłącz",
    ):
        try:
            res = install_github_update(show_dialogs=True)
            if res.get("installed"):
                return False
        except Exception as exc:
            log("GitHub update during version guard failed: {}".format(exc), xbmc.LOGERROR)
            dialog.ok(
                "KodiBridge — błąd pobierania z GitHub",
                "Nie udało się pobrać aktualizacji z GitHub:\n{}".format(exc),
            )

    if dialog.yesno(
        "KodiBridge — aktualizacja z pliku ZIP",
        "Wymagana jest wersja co najmniej {remote} (obecna: {local}).\n\n"
        "Czy chcesz wskazać plik aktualizacji .zip z dysku lokalnego lub udziału sieciowego?".format(
            remote=remote_min, local=local_version
        ),
        yeslabel="Wybierz plik ZIP",
        nolabel="Wyłącz KodiBridge",
    ):
        zip_path = dialog.browse(1, "Wybierz pakiet aktualizacji KodiBridge (.zip)", "files", ".zip")
        if zip_path:
            try:
                res = install_update_from_zip_path(zip_path, show_dialogs=True)
                if isinstance(res, dict) and res.get("installed"):
                    # Kodi restarts the updated service; this instance stops here.
                    return False
            except Exception as exc:
                dialog.ok("KodiBridge — błąd aktualizacji", str(exc))

    dialog.ok(
        "KodiBridge — usługa wstrzymana",
        "KodiBridge nie będzie synchronizował tego Kodi do czasu aktualizacji wtyczki do wersji {}.\n\n"
        "Ponownie zapytam o aktualizację po następnym uruchomieniu Kodi. Możesz też w każdej chwili "
        "zaktualizować wtyczkę ręcznie w jej ustawieniach.".format(
            remote_min
        ),
    )
    return False


ACTIVE_KODI_LOCK_FILENAME = "active_kodi_lock.json"
ACTIVE_KODI_TTL_SECONDS = 180


def _active_kodi_lock_path(base_path):
    base = (base_path or get_base_path()).rstrip("/") + "/"
    return base + ACTIVE_KODI_LOCK_FILENAME


def _local_client_identity():
    from resources.lib.sudosync_core import addon_settings, get_or_create_client_id
    try:
        client_id = str(get_or_create_client_id() or "").strip()
    except Exception:
        client_id = ""
    try:
        client_name = str(addon_settings().get("client_name") or "").strip()
    except Exception:
        client_name = ""
    if not client_name:
        try:
            raw_friendly = xbmc.getInfoLabel("System.FriendlyName")
            client_name = raw_friendly.strip() if isinstance(raw_friendly, str) else ""
        except Exception:
            client_name = ""
    if not client_name:
        client_name = "Kodi"
    return client_id, client_name


LEASE_READ_ATTEMPTS = 3
LEASE_READ_RETRY_SECONDS = 5
LEASE_VERIFY_DELAY_MS = 2000
_LEASE_STATE = {"last_ok_epoch": None}
_KODI_TAG_RE = re.compile(r"\[/?[A-Za-z]+(?:[ =][^\]]*)?\]")


def _clean_client_name(name, fallback="Inne Kodi"):
    """Device name from a shared file, safe to show: no Kodi markup, printable, short."""
    text = _KODI_TAG_RE.sub("", str(name or ""))
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text[:64] or fallback


def _file_mtime(path):
    """Modification time of a file as set by the file server, or None."""
    try:
        unc = _bootstrap_win_unc(path)
        if unc is not None:
            return float(os.stat(unc).st_mtime) if os.path.exists(unc) else None
        value = xbmcvfs.Stat(path).st_mtime()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
    except Exception:
        pass
    return None


def _write_probe(path):
    unc = _bootstrap_win_unc(path)
    if unc is not None:
        with open(unc, "w", encoding="utf-8") as f:
            f.write("probe")
        return True
    f = xbmcvfs.File(path, 'w')
    try:
        return bool(f.write("probe"))
    finally:
        f.close()


def _nas_clock_age(base, lock_path):
    """Age of the lease file measured by the NAS clock, or None when unavailable.

    The lease file and a freshly written probe file both get their modification
    time from the file server, so device clocks do not matter.
    """
    lock_mtime = _file_mtime(lock_path)
    if lock_mtime is None:
        return None
    client_id, _ = _local_client_identity()
    probe = base.rstrip("/") + "/clock_probe_{}.tmp".format(re.sub(r"[^A-Za-z0-9-]", "", client_id) or "local")
    try:
        if not _write_probe(probe):
            return None
        server_now = _file_mtime(probe)
    except Exception as exc:
        log("Cannot read the NAS clock: {}".format(exc), xbmc.LOGWARNING)
        return None
    finally:
        try:
            _b_delete(probe)
        except Exception:
            pass
    if server_now is None:
        return None
    return server_now - lock_mtime


def _lease_is_fresh(base, lock_path, existing, ttl_seconds, now):
    ttl = float(ttl_seconds)
    age = _nas_clock_age(base, lock_path)
    if age is None:
        # Fallback: the owner's clock against this device's clock.
        try:
            updated_at = float(existing.get("updated_at_epoch") or 0.0)
        except Exception:
            updated_at = 0.0
        if updated_at <= 0.0:
            return False
        age = now - updated_at
    return -ttl < age < ttl


def _peer_lease_state(base_path=None, ttl_seconds=ACTIVE_KODI_TTL_SECONDS, now_epoch=None):
    """("peer", (id, name)), ("free", None) or ("unreadable", status). Read-only except for the NAS clock probe."""
    base = (base_path or get_base_path()).rstrip("/")
    if not base:
        return "free", None
    lock_path = _active_kodi_lock_path(base)
    client_id, _ = _local_client_identity()
    now = float(now_epoch if now_epoch is not None else time.time())

    status, existing = _read_json_file(lock_path)
    if status == "NOT_FOUND":
        return "free", None
    if status != "VALID" or not isinstance(existing, dict):
        return "unreadable", status
    owner_id = str(existing.get("client_id") or "").strip()
    if not owner_id or owner_id == client_id:
        return "free", None
    owner_name = _clean_client_name(existing.get("client_name") or owner_id)
    if _lease_is_fresh(base, lock_path, existing, ttl_seconds, now):
        return "peer", (owner_id, owner_name)
    return "free", None


def find_active_peer_kodi(base_path=None, ttl_seconds=ACTIVE_KODI_TTL_SECONDS, now_epoch=None):
    """Return (owner_id, owner_name) of another Kodi holding a fresh lease, else None."""
    state, info = _peer_lease_state(base_path, ttl_seconds=ttl_seconds, now_epoch=now_epoch)
    return info if state == "peer" else None


def _report_active_peer_kodi(owner_id, owner_name):
    log(
        "Wykryto inne aktywne urządzenie Kodi w sieci ({}, id={}). Zatrzymuję start.".format(
            owner_name, owner_id
        ),
        xbmc.LOGWARNING,
    )
    xbmcgui.Dialog().ok(
        "KodiBridge — inne Kodi jest aktywne",
        "W tej sieci działa już KodiBridge na urządzeniu: {}.\n"
        "Dozwolone jest tylko jedno aktywne Kodi naraz — zamknij tamto Kodi przed uruchomieniem kolejnego.".format(
            owner_name
        ),
    )


def ensure_no_active_peer_kodi(base_path=None, ttl_seconds=ACTIVE_KODI_TTL_SECONDS, now_epoch=None):
    """Start check: False (after telling the user) when another Kodi is active or the lease stays unreadable."""
    status = None
    for attempt in range(LEASE_READ_ATTEMPTS):
        state, info = _peer_lease_state(base_path, ttl_seconds=ttl_seconds, now_epoch=now_epoch)
        if state == "peer":
            _report_active_peer_kodi(*info)
            return False
        if state == "free":
            return True
        status = info
        if attempt + 1 < LEASE_READ_ATTEMPTS:
            log("Blokada aktywnego Kodi nieczytelna ({}), ponawiam odczyt.".format(status), xbmc.LOGWARNING)
            xbmc.sleep(LEASE_READ_RETRY_SECONDS * 1000)
    lock_path = mask_vfs_credentials(_active_kodi_lock_path((base_path or get_base_path()).rstrip("/")))
    log("Nie można odczytać blokady aktywnego Kodi ({}): {}. Zatrzymuję start.".format(status, lock_path),
        xbmc.LOGERROR)
    xbmcgui.Dialog().ok(
        "KodiBridge — nie można sprawdzić innych Kodi",
        "Nie udało się odczytać pliku blokady aktywnego Kodi na NAS-ie:\n{}\n\n"
        "KodiBridge nie wystartuje, dopóki nie sprawdzi, czy inne Kodi nie pracuje. Sprawdź połączenie z NAS-em "
        "i uruchom Kodi ponownie. Jeśli plik jest uszkodzony, a żadne inne Kodi nie pracuje, usuń go.".format(
            lock_path
        ),
    )
    return False


def acquire_network_active_kodi_lease(base_path=None, ttl_seconds=ACTIVE_KODI_TTL_SECONDS, now_epoch=None):
    """Ensure only one Kodi instance runs SudoSync in the shared network at a time."""
    base = (base_path or get_base_path()).rstrip("/")
    if not base:
        return True
    lock_path = _active_kodi_lock_path(base)
    client_id, client_name = _local_client_identity()
    now = float(now_epoch if now_epoch is not None else time.time())

    peer = find_active_peer_kodi(base, ttl_seconds=ttl_seconds, now_epoch=now)
    if peer:
        _report_active_peer_kodi(*peer)
        return False

    lease_payload = {
        "client_id": client_id,
        "client_name": client_name,
        "addon_version": _get_local_addon_version(),
        "updated_at_epoch": now,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(now))),
    }
    _write_json_file(lock_path, lease_payload)

    # Two Kodi started at the same moment may both have found no lease; the
    # later write wins. Read the lease back after a moment and give way if it
    # now belongs to another Kodi.
    xbmc.sleep(LEASE_VERIFY_DELAY_MS)
    status, current = _read_json_file(lock_path)
    if status == "VALID" and isinstance(current, dict):
        owner_id = str(current.get("client_id") or "").strip()
        if owner_id and owner_id != client_id:
            _report_active_peer_kodi(owner_id, _clean_client_name(current.get("client_name") or owner_id))
            return False
    _LEASE_STATE["last_ok_epoch"] = now
    return True


def refresh_network_active_kodi_lease(base_path=None, ttl_seconds=ACTIVE_KODI_TTL_SECONDS, now_epoch=None):
    """Refresh the active Kodi heartbeat on NAS; return False on peer takeover or outdated addon version."""
    try:
        base = (base_path or get_base_path()).rstrip("/")
        if not base:
            return True
        lock_path = _active_kodi_lock_path(base)
        client_id, client_name = _local_client_identity()
        local_version = _get_local_addon_version()
        now = float(now_epoch if now_epoch is not None else time.time())

        if local_version:
            remote_min = _discover_highest_network_addon_version(base + "/")
            if remote_min:
                from resources.lib.sudosync_core import _numeric_version
                if _numeric_version(local_version) < _numeric_version(remote_min):
                    log(
                        "Wykryto nowszą wymaganą wersję KodiBridge w sieci ({}) podczas działania usługi (lokalna: {}). Zatrzymuję usługę.".format(
                            remote_min, local_version
                        ),
                        xbmc.LOGWARNING,
                    )
                    return False

        status, existing = _read_json_file(lock_path)
        if status == "VALID" and isinstance(existing, dict):
            owner_id = str(existing.get("client_id") or "").strip()
            owner_name = _clean_client_name(existing.get("client_name") or owner_id)
            if (owner_id and owner_id != client_id
                    and _lease_is_fresh(base, lock_path, existing, ttl_seconds, now)):
                log(
                    "Dzierżawa aktywnego Kodi została przejęta przez inne urządzenie: {} (id={}).".format(
                        owner_name, owner_id
                    ),
                    xbmc.LOGWARNING,
                )
                return False

        lease_payload = {
            "client_id": client_id,
            "client_name": client_name,
            "addon_version": local_version,
            "updated_at_epoch": now,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(now))),
        }
        if not _write_json_file(lock_path, lease_payload):
            raise IOError("nie udało się zapisać dzierżawy")
        _LEASE_STATE["last_ok_epoch"] = now
        return True
    except Exception as exc:
        log("Błąd odświeżania dzierżawy aktywnego Kodi: {}".format(exc), xbmc.LOGWARNING)
        # Przejściowy błąd I/O sieci nie oznacza konfliktu urządzeń, ale po
        # upływie TTL inne Kodi może już uznać dzierżawę za wolną.
        now = float(now_epoch if now_epoch is not None else time.time())
        last_ok = _LEASE_STATE.get("last_ok_epoch")
        if last_ok is not None and (now - last_ok) >= float(ttl_seconds):
            log("Dzierżawa aktywnego Kodi nie była odświeżona od {:.0f} s. Zatrzymuję usługę.".format(now - last_ok),
                xbmc.LOGERROR)
            return False
        return True


def release_network_active_kodi_lease(base_path=None):
    """Release the active Kodi lease on NAS when this Kodi shuts down."""
    base = (base_path or get_base_path()).rstrip("/")
    if not base:
        return False
    lock_path = _active_kodi_lock_path(base)
    try:
        client_id, _ = _local_client_identity()
        status, existing = _read_json_file(lock_path)
        if status == "VALID" and isinstance(existing, dict):
            owner_id = str(existing.get("client_id") or "").strip()
            if owner_id and owner_id != client_id:
                return False
        if _b_exists(lock_path):
            _b_delete(lock_path)
        return True
    except Exception as exc:
        log("Nie udało się zwolnić blokady aktywnego Kodi: {}".format(exc), xbmc.LOGWARNING)
        return False


def _ensure_client_enrolled_and_initialized(base_path):
    """Automatically mark shared config initialized after wizard/bootstrap."""
    try:
        from resources.lib.sudosync_core import (
            read_shared_config,
            update_shared_config,
            utc_now,
        )
        client_id, client_name = _local_client_identity()
        shared = read_shared_config(base_path)
        init = shared.get("initialization") or {}
        if not init.get("completed", False):
            def mutator(cfg):
                cfg_init = cfg.setdefault("initialization", {})
                cfg_init["completed"] = True
                if not cfg_init.get("base_client_id"):
                    cfg_init["base_client_id"] = client_id
                    cfg_init["base_client_name"] = client_name
                if not cfg_init.get("completed_at"):
                    cfg_init["completed_at"] = utc_now()
                return True
            update_shared_config(mutator, base_path)

    except Exception as exc:
        log("Pominięto automatyczną inicjalizację rejestru klienta: {}".format(exc), xbmc.LOGWARNING)


def run_migration_wizard(base_path):
    xbmcgui.Dialog().ok(
        "KodiBridge - Migracja",
        "Wykryto istniejącą instalację starszej wersji KodiBridge w wybranym folderze.\n\nTwoje dotychczasowe dane i identyfikatory zostaną zachowane. KodiBridge wyodrębni teraz ustawienia sieciowe do nowego pliku SudoSync.cfg."
    )
    
    # Wyciągamy ustawienia sieciowe, jeśli jakieś były w starym system/config.json
    _, sys_config = _read_json_file(base_path + "system/config.json")
    sys_config = sys_config or {}
    old_net = sys_config.get("network_settings", {})
    
    # Jeśli nie było starych, bierzemy lokalne preferencje z addon_data 
    # (to może być przypadek gdy SudoSync ID włączono lokalnie, ale nie opublikowano do starej bazy)
    addon = xbmcaddon.Addon(ADDON_ID)
    sudosync_id_enabled = old_net.get("sudosync_id_enabled", addon.getSetting("sudosync_id_enabled").strip().lower() == "true")
    sudosync_id_prefix = old_net.get("sudosync_id_prefix", addon.getSetting("sudosync_id_prefix").strip() or "NAS")
    try:
        raw_last_num = old_net.get(
            "sudosync_id_last_number",
            sys_config.get("sudosync_id_last_number", addon.getSetting("sudosync_id_last_number").strip() or 0),
        )
        sudosync_id_last_number = max(0, int(raw_last_num or 0))
    except Exception:
        sudosync_id_last_number = 0
    local_version = _get_local_addon_version()
    
    net_settings = {
        "sudosync_id_enabled": bool(sudosync_id_enabled),
        "sudosync_id_prefix": str(sudosync_id_prefix),
        "sudosync_id_last_number": sudosync_id_last_number,
    }
    if local_version:
        net_settings["min_addon_version"] = local_version

    new_cfg = {
        "format": "SudoSync configuration",
        "schema_version": 1,
        "configuration": {
            "completed": True,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        },
        "network_settings": net_settings,
    }
    
    if not write_sudosync_cfg(base_path, new_cfg):
        xbmcgui.Dialog().ok("Błąd", "Nie udało się zapisać zmigrowanej konfiguracji do SudoSync.cfg")
        return False
        
    xbmcgui.Dialog().notification("Migracja", "Migracja zakończona sukcesem.", xbmcgui.NOTIFICATION_INFO, 5000)
    return True

def run_new_config_wizard(base_path):
    dialog = xbmcgui.Dialog()
    if not dialog.yesno("KodiBridge - Konfigurator sieciowy", "Skonfigurujemy teraz nowe środowisko synchronizacji KodiBridge dla Twojej domeny.\n\nCzy chcesz domyślnie używać niezawodnego identyfikatora 'KodiBridge ID' dla wideo pozbawionych standardowych ID (IMDb/TMDb)?", yeslabel="Tak (Zalecane)", nolabel="Nie, użyj IMDb"):
        sudosync_id_enabled = False
        sudosync_id_prefix = ""
    else:
        sudosync_id_enabled = True
        sudosync_id_prefix = ""
    local_version = _get_local_addon_version()
    net_settings = {
        "sudosync_id_enabled": sudosync_id_enabled,
        "sudosync_id_prefix": sudosync_id_prefix,
        "sudosync_id_last_number": 0,
    }
    if local_version:
        net_settings["min_addon_version"] = local_version
        
    new_cfg = {
        "format": "SudoSync configuration",
        "schema_version": 1,
        "configuration": {
            "completed": True,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        },
        "network_settings": net_settings,
    }
    
    if not write_sudosync_cfg(base_path, new_cfg):
        xbmcgui.Dialog().ok("Błąd", "Nie udało się zapisać nowej konfiguracji do SudoSync.cfg")
        return False
        
    dialog.ok("Sukces", "Środowisko .KodiBridge skonfigurowane poprawnie!\nTeraz synchronizacja może się rozpocząć.")
    return True

def apply_network_settings(cfg):
    net = cfg.get("network_settings", {})
    if not net:
        return
        
    remote_enabled = bool(net.get("sudosync_id_enabled", False))
    remote_prefix = str(net.get("sudosync_id_prefix") or "").strip()
    raw_remote_last = net.get("sudosync_id_last_number")
    remote_last_num = raw_remote_last if (isinstance(raw_remote_last, int) and not isinstance(raw_remote_last, bool) and raw_remote_last >= 0) else None

    addon = xbmcaddon.Addon(ADDON_ID)
    local_enabled = addon.getSetting("sudosync_id_enabled").strip().lower() == "true"
    local_prefix = addon.getSetting("sudosync_id_prefix").strip()
    try:
        local_last_num = max(0, int(addon.getSetting("sudosync_id_last_number").strip() or "0"))
    except Exception:
        local_last_num = 0

    changed = False
    if local_enabled != remote_enabled:
        addon.setSetting("sudosync_id_enabled", "true" if remote_enabled else "false")
        changed = True
    if local_prefix != remote_prefix:
        addon.setSetting("sudosync_id_prefix", remote_prefix)
        changed = True
    if remote_last_num is not None and remote_last_num > local_last_num:
        addon.setSetting("sudosync_id_last_number", str(remote_last_num))
        changed = True

    if changed:
        log("Konfiguracja sieciowa SudoSync ID została wymuszona na lokalnym kliencie.")

def run_bootstrap(force_select_path=False):
    """Zwraca True, jeśli bootstrap zakończony i można przejść do logiki operacyjnej, False w przypadku wstrzymania/błędu."""
    log("Rozpoczęcie fazy BOOTSTRAP")
    base_path = get_base_path()
    old_base_path = base_path
    
    # 1. USTALENIE BASE PATH
    # Brak ustawienia (pusty ciąg) lub ręczne wywołanie konfiguratora z ustawień uruchamia kreator wyboru folderu
    choose_new_path = not base_path
    pending_folder_change = False
    if base_path and force_select_path:
        dialog = xbmcgui.Dialog()
        if dialog.yesno(
            "KodiBridge — Konfigurator",
            "Obecny folder:\n{}\nCzy chcesz wskazać inny folder współdzielony?".format(mask_vfs_credentials(base_path)),
            yeslabel="Zmień folder",
            nolabel="Sprawdź obecny",
        ):
            choose_new_path = True

    if choose_new_path:
        dialog = xbmcgui.Dialog()
        if not base_path:
            dialog.ok("KodiBridge", "Musisz wskazać folder sieciowy (np. SMB/NFS), w którym KodiBridge będzie przechowywać wspólną konfigurację i bazę dla wszystkich urządzeń Kodi w tej sieci.")
        
        selected_path = dialog.browse(3, "Wybierz katalog współdzielony (np. Wideo)", "network")
        if not selected_path:
            log("User cancelled path selection.")
            return False
            
        selected_path = selected_path.replace('\\', '/')
        if not selected_path.endswith('/'):
            selected_path += '/'
            
        # Zabezpieczenie przed zagnieżdżaniem folderów współdzielonych
        if _is_shared_dir(selected_path):
            base_path = selected_path
        else:
            base_path = selected_path + SHARED_DIR_NAME + "/"
            
        # Releasing the old lease and saving the new folder wait until the
        # active-peer check below has passed (step 1b).
        pending_folder_change = True
    elif not base_path.endswith("/"):
        base_path += "/"
        set_base_path(base_path)
        
    # 1a. PRZEJŚCIE Z .SudoSync NA .KodiBridge (2.2.12: nowy KodiBridge ID, stan od zera)
    # Ustawienie zmienia się dopiero po udanym teście zapisu i migracji, więc
    # nieudana próba (np. NAS jeszcze niegotowy) zostanie powtórzona przy
    # następnym starcie.
    migrated_from = None
    new_shared = kodibridge_shared_path(base_path)
    if new_shared:
        migrated_from = base_path
        base_path = new_shared

    # 1b. INNE AKTYWNE KODI — sprawdzane przed pierwszym zapisem do folderu
    # wspólnego (test R/W, przejście z .SudoSync, min_addon_version w
    # SudoSync.cfg, kreatory). Tylko odczyt; własną blokadę zapisujemy dalej,
    # po kontroli wersji. Przy przejściu z .SudoSync starsze wersje trzymają
    # blokadę w starym folderze, więc sprawdzamy oba.
    log("Sprawdzanie, czy inne Kodi jest aktywne w {}".format(mask_vfs_credentials(base_path)))
    for lease_base in ([base_path, migrated_from] if migrated_from else [base_path]):
        if not ensure_no_active_peer_kodi(lease_base):
            return False
    if pending_folder_change:
        # A chosen .SudoSync folder is saved as chosen; the switch to
        # .KodiBridge is saved only after a successful migration (below).
        chosen = migrated_from or base_path
        if old_base_path and old_base_path.rstrip("/") != chosen.rstrip("/"):
            release_network_active_kodi_lease(old_base_path)
        set_base_path(chosen)

    # 2. TEST DOSTĘPU I READ/WRITE
    log("Testowanie dostępu R/W do {}".format(mask_vfs_credentials(base_path)))
    success, msg = test_vfs_rw(base_path)
    if not success:
        xbmcgui.Dialog().ok("KodiBridge - Brak Dostępu", "Nie udało się zapisać plików w wybranym folderze:\n{}\n\nSprawdź uprawnienia do zapisu lub wskaż inny udział w ustawieniach.".format(msg))
        return False

    if migrated_from:
        migrate_legacy_shared_folder(migrated_from, base_path)
        release_network_active_kodi_lease(migrated_from)
        set_base_path(base_path)
        log("Folder współdzielony zmieniony z {} na {}".format(
            mask_vfs_credentials(migrated_from), mask_vfs_credentials(base_path)))
        try:
            xbmcgui.Dialog().notification(
                "KodiBridge",
                "Nowy folder wspólny {}: synchronizacja zaczyna się od nowa".format(SHARED_DIR_NAME),
                xbmcgui.NOTIFICATION_INFO,
                8000,
            )
        except Exception:
            pass

    # 3. SPRAWDZENIE SUDOSYNC.CFG
    status, cfg = read_sudosync_cfg(base_path)
    if status == "VALID":
        # WALIDACJA ISTNIEJĄCEGO SudoSync.cfg
        if not isinstance(cfg, dict):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Plik SudoSync.cfg nie jest prawidłowym słownikiem JSON.")
            return False
        if cfg.get("schema_version") not in (1, 2):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Nieobsługiwana wersja schematu (schema_version) w SudoSync.cfg.")
            return False
        if not isinstance(cfg.get("network_settings"), dict):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Brak sekcji network_settings w SudoSync.cfg.")
            return False
        if not isinstance(cfg.get("network_settings", {}).get("sudosync_id_enabled"), bool):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Zły typ danych w sudosync_id_enabled.")
            return False
        if not isinstance(cfg.get("network_settings", {}).get("sudosync_id_prefix"), str):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Zły typ danych w sudosync_id_prefix.")
            return False
        last_num = cfg.get("network_settings", {}).get("sudosync_id_last_number")
        if last_num is not None and (not isinstance(last_num, int) or isinstance(last_num, bool) or last_num < 0):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Zły typ danych w sudosync_id_last_number.")
            return False
        min_ver = cfg.get("network_settings", {}).get("min_addon_version")
        if min_ver is not None and not isinstance(min_ver, str):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Zły typ danych w min_addon_version.")
            return False
        if not cfg.get("configuration", {}).get("completed"):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Konfiguracja w SudoSync.cfg nie jest oznaczona jako ukończona.")
            return False

        if not enforce_network_version_guard(base_path, cfg):
            return False
        if not acquire_network_active_kodi_lease(base_path):
            return False
            
        log("SudoSync.cfg poprawnie zweryfikowany. Aplikowanie ustawień sieciowych.")
        apply_network_settings(cfg)
        _ensure_client_enrolled_and_initialized(base_path)
        return True
    elif status in ("INVALID", "IO_ERROR"):
        corrupted_path = base_path + "SudoSync.cfg"
        backup_path = base_path + "SudoSync.cfg.corrupted_" + str(int(time.time()))
        if xbmcvfs.exists(corrupted_path):
            xbmcvfs.copy(corrupted_path, backup_path)
        xbmcgui.Dialog().ok("Błąd Krytyczny", "Plik SudoSync.cfg jest uszkodzony lub nieczytelny. Została utworzona kopia zapasowa. Napraw plik ręcznie lub usuń go, aby wymusić nową konfigurację.")
        return False
    else:
        # BRAK SUDOSYNC.CFG
        # 4. Sprawdzenie, czy to starsza instalacja
        if is_legacy_installation(base_path):
            log("Wykryto starszą instalację bez SudoSync.cfg -> MIGRATION WIZARD")
            if not run_migration_wizard(base_path):
                return False
        else:
            log("Brak starej instalacji -> NEW CONFIG WIZARD")
            if not run_new_config_wizard(base_path):
                return False
                
        # 5. PONOWNY ODCZYT I WALIDACJA PO WIZARDZIE
        status, cfg = read_sudosync_cfg(base_path)
        if status == "VALID" and cfg.get("configuration", {}).get("completed"):
            if not enforce_network_version_guard(base_path, cfg):
                return False
            if not acquire_network_active_kodi_lease(base_path):
                return False
            apply_network_settings(cfg)
            _ensure_client_enrolled_and_initialized(base_path)
            return True
            
        xbmcgui.Dialog().ok("Błąd Krytyczny", "Nie udało się odczytać SudoSync.cfg pomimo jego zapisu.")
        return False

