# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import os
import time
import json
import xbmc
import xbmcgui
import xbmcvfs
import xbmcaddon

ADDON_ID = "service.sudosync"

def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[SudoSync Bootstrap] {}".format(msg), level)

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

def test_vfs_rw(test_dir):
    try:
        if not xbmcvfs.exists(test_dir):
            if not xbmcvfs.mkdirs(test_dir):
                return False, "Nie można utworzyć katalogu .SudoSync"
        
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

def _read_json_file(filepath):
    if not xbmcvfs.exists(filepath):
        return "NOT_FOUND", None
    try:
        f = xbmcvfs.File(filepath, 'r')
        data = f.read()
        f.close()
    except Exception as exc:
        log("Cannot read JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return "IO_ERROR", None

    try:
        import json
        obj = json.loads(data)
        return "VALID", obj
    except json.JSONDecodeError as exc:
        log("Cannot parse JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return "INVALID", None

def _write_json_file(filepath, obj):
    try:
        import json
        data = json.dumps(obj, indent=2, sort_keys=True)
        # Bezpieczny zapis przez plik tymczasowy i kopię zapasową
        tmp_file = filepath + ".tmp"
        bak_file = filepath + ".bak"
        
        f = xbmcvfs.File(tmp_file, 'w')
        result = f.write(data)
        f.close()
        
        if not result:
            log("Nie udało się zapisać pełnych danych do .tmp dla {}".format(filepath), xbmc.LOGERROR)
            return False

        if xbmcvfs.exists(filepath):
            if xbmcvfs.exists(bak_file):
                xbmcvfs.delete(bak_file)
            xbmcvfs.copy(filepath, bak_file)
            xbmcvfs.delete(filepath)
            
        if xbmcvfs.rename(tmp_file, filepath):
            if xbmcvfs.exists(tmp_file):
                xbmcvfs.delete(tmp_file)
            return True
        else:
            if xbmcvfs.exists(bak_file):
                xbmcvfs.rename(bak_file, filepath)
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

def is_legacy_installation(base_path):
    # Sprawdzamy czy istnieje system/config.json oraz clients/
    sys_config = base_path + "system/config.json"
    clients_dir = base_path + "clients/"
    return xbmcvfs.exists(sys_config) and xbmcvfs.exists(clients_dir)

def _get_local_addon_version():
    try:
        raw = xbmcaddon.Addon(ADDON_ID).getAddonInfo("version")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return ""


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


def enforce_network_version_guard(base_path, cfg):
    """Ensure this Kodi's SudoSync version is not older than min_addon_version in SudoSync.cfg.

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

    net = cfg.setdefault("network_settings", {})
    if not isinstance(net, dict):
        return True

    remote_min = str(net.get("min_addon_version") or "").strip()
    local_num = _numeric_version(local_version)
    remote_num = _numeric_version(remote_min) if remote_min else (0, 0, 0, 3, 0)

    if not remote_min or local_num > remote_num:
        if local_num > (0, 0, 0, 3, 0):
            net["min_addon_version"] = local_version
            write_sudosync_cfg(base_path, cfg)
            log("Zaktualizowano minimalną wersję sieciową SudoSync w SudoSync.cfg: {}".format(local_version))
        return True

    if local_num == remote_num:
        return True

    # local_num < remote_num -> inne Kodi w sieci ma już nowszą wersję!
    log(
        "Wykryto nowszą wersję w sieci SudoSync ({}) niż zainstalowana lokalnie ({}). Wstrzymuję start.".format(
            remote_min, local_version
        ),
        xbmc.LOGERROR,
    )
    dialog = xbmcgui.Dialog()
    if dialog.yesno(
        "SudoSync — wymagana aktualizacja",
        "Na innym urządzeniu Kodi w tej sieci zainstalowano już nowszą wersję SudoSync ({remote}), "
        "natomiast tutaj działa starsza wersja ({local}).\n\n"
        "Aby chronić spójność biblioteki, SudoSync jest wyłączony do czasu aktualizacji. "
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
                "SudoSync — błąd pobierania z GitHub",
                "Nie udało się pobrać aktualizacji z GitHub:\n{}".format(exc),
            )

    if dialog.yesno(
        "SudoSync — aktualizacja z pliku ZIP",
        "Wymagana jest wersja co najmniej {remote} (obecna: {local}).\n\n"
        "Czy chcesz wskazać plik aktualizacji .zip z dysku lokalnego lub udziału sieciowego?".format(
            remote=remote_min, local=local_version
        ),
        yeslabel="Wybierz plik ZIP",
        nolabel="Wyłącz SudoSync",
    ):
        zip_path = dialog.browse(1, "Wybierz pakiet aktualizacji SudoSync (.zip)", "files", ".zip")
        if zip_path:
            try:
                install_update_from_zip_path(zip_path, show_dialogs=True)
            except Exception as exc:
                dialog.ok("SudoSync — błąd aktualizacji", str(exc))
            return False

    dialog.ok(
        "SudoSync — usługa wstrzymana",
        "SudoSync został wyłączony na tym urządzeniu do czasu aktualizacji wtyczki do wersji {}.".format(
            remote_min
        ),
    )
    return False


def run_migration_wizard(base_path):
    xbmcgui.Dialog().ok(
        "SudoSync - Migracja",
        "Wykryto istniejącą instalację starszej wersji SudoSync w wybranym folderze.\n\nTwoje dotychczasowe dane i identyfikatory zostaną zachowane. SudoSync wyodrębni teraz ustawienia sieciowe do nowego pliku SudoSync.cfg."
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
    local_version = _get_local_addon_version()
    
    net_settings = {
        "sudosync_id_enabled": bool(sudosync_id_enabled),
        "sudosync_id_prefix": str(sudosync_id_prefix),
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
    if not dialog.yesno("SudoSync - Konfigurator sieciowy", "Skonfigurujemy teraz nowe środowisko synchronizacji SudoSync dla Twojej domeny.\n\nCzy chcesz domyślnie używać niezawodnego identyfikatora 'SudoSync ID' dla wideo pozbawionych standardowych ID (IMDb/TMDb)?", yeslabel="Tak (Zalecane)", nolabel="Nie, użyj IMDb"):
        sudosync_id_enabled = False
        sudosync_id_prefix = ""
    else:
        sudosync_id_enabled = True
        prefix = dialog.input("Podaj krótki prefiks dla tej sieci (np. NAS lub DOM):", type=xbmcgui.INPUT_ALPHANUM)
        sudosync_id_prefix = prefix.strip() if prefix.strip() else "NAS"
    local_version = _get_local_addon_version()
    net_settings = {
        "sudosync_id_enabled": sudosync_id_enabled,
        "sudosync_id_prefix": sudosync_id_prefix,
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
        
    dialog.ok("Sukces", "Środowisko .SudoSync skonfigurowane poprawnie!\nTeraz synchronizacja może się rozpocząć.")
    return True

def apply_network_settings(cfg):
    net = cfg.get("network_settings", {})
    if not net:
        return
        
    remote_enabled = bool(net.get("sudosync_id_enabled", False))
    remote_prefix = str(net.get("sudosync_id_prefix") or "").strip()

    addon = xbmcaddon.Addon(ADDON_ID)
    local_enabled = addon.getSetting("sudosync_id_enabled").strip().lower() == "true"
    local_prefix = addon.getSetting("sudosync_id_prefix").strip()

    changed = False
    if local_enabled != remote_enabled:
        addon.setSetting("sudosync_id_enabled", "true" if remote_enabled else "false")
        changed = True
    if local_prefix != remote_prefix:
        addon.setSetting("sudosync_id_prefix", remote_prefix)
        changed = True

    if changed:
        log("Konfiguracja sieciowa SudoSync ID została wymuszona na lokalnym kliencie.")

def run_bootstrap(force_select_path=False):
    """Zwraca True, jeśli bootstrap zakończony i można przejść do logiki operacyjnej, False w przypadku wstrzymania/błędu."""
    log("Rozpoczęcie fazy BOOTSTRAP")
    base_path = get_base_path()
    
    # 1. USTALENIE BASE PATH
    # Brak ustawienia (pusty ciąg) lub ręczne wywołanie konfiguratora z ustawień uruchamia kreator wyboru folderu
    choose_new_path = not base_path
    if base_path and force_select_path:
        dialog = xbmcgui.Dialog()
        if dialog.yesno(
            "SudoSync — Konfigurator",
            "Obecny folder synchronizacji:\n{}\n\nCzy chcesz wskazać inny folder współdzielony?".format(base_path),
            yeslabel="Zmień folder",
            nolabel="Sprawdź obecny",
        ):
            choose_new_path = True

    if choose_new_path:
        dialog = xbmcgui.Dialog()
        if not base_path:
            dialog.ok("SudoSync", "Musisz wskazać folder sieciowy (np. SMB/NFS), w którym SudoSync będzie przechowywać wspólną konfigurację i bazę dla wszystkich urządzeń Kodi w tej sieci.")
        
        selected_path = dialog.browse(3, "Wybierz katalog współdzielony (np. Wideo)", "network")
        if not selected_path:
            log("User cancelled path selection.")
            return False
            
        selected_path = selected_path.replace('\\', '/')
        if not selected_path.endswith('/'):
            selected_path += '/'
            
        # Zabezpieczenie przed zagnieżdżaniem .SudoSync/.SudoSync
        if selected_path.endswith(".SudoSync/"):
            base_path = selected_path
        else:
            base_path = selected_path + ".SudoSync/"
            
        set_base_path(base_path)
    elif not base_path.endswith("/"):
        base_path += "/"
        set_base_path(base_path)
        
    # 2. TEST DOSTĘPU I READ/WRITE
    log("Testowanie dostępu R/W do {}".format(base_path))
    success, msg = test_vfs_rw(base_path)
    if not success:
        xbmcgui.Dialog().ok("SudoSync - Brak Dostępu", "Nie udało się zapisać plików w wybranym folderze:\n{}\n\nSprawdź uprawnienia do zapisu lub wskaż inny udział w ustawieniach.".format(msg))
        return False
        
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
        min_ver = cfg.get("network_settings", {}).get("min_addon_version")
        if min_ver is not None and not isinstance(min_ver, str):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Zły typ danych w min_addon_version.")
            return False
        if not cfg.get("configuration", {}).get("completed"):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Konfiguracja w SudoSync.cfg nie jest oznaczona jako ukończona.")
            return False

        if not enforce_network_version_guard(base_path, cfg):
            return False
            
        log("SudoSync.cfg poprawnie zweryfikowany. Aplikowanie ustawień sieciowych.")
        apply_network_settings(cfg)
        return True
    elif status in ("INVALID", "IO_ERROR"):
        import time
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
            apply_network_settings(cfg)
            return True
            
        xbmcgui.Dialog().ok("Błąd Krytyczny", "Nie udało się odczytać SudoSync.cfg pomimo jego zapisu.")
        return False

def check_and_enforce_updates_before_bootstrap():
    """Check mandatory updates before bootstrap.

    Returns ``False`` when no update is available, ``"restart"`` after an
    update was installed, or ``"blocked"`` when a discovered update could not
    be verified or installed. A rejected update must not silently fall through
    to normal service startup.
    """
    from resources.lib.sudosync_core import check_for_update, install_latest_update
    # Sprawdzamy czy base_path jest ustawiony (jeśli pusty, to pomijamy)
    base_path = get_base_path()
    if not base_path:
        return False  # brak ścieżki do folderu aktualizacji
        
    try:
        result = check_for_update(show_notification=False, manual=False)
    except Exception as exc:
        log("Cannot check mandatory updates: {}".format(exc), xbmc.LOGWARNING)
        return False
    if result.get("error"):
        log("Cannot check mandatory updates: {}".format(result["error"]), xbmc.LOGWARNING)
        return False

    if result.get("available"):
        try:
            installed = install_latest_update(show_dialogs=False)
            if installed.get("installed"):
                xbmcgui.Dialog().notification(
                    "SudoSync zaktualizowano",
                    "Zainstalowano {}. Kodi zostanie zrestartowane, aby wczytac nowa wersje.".format(installed.get("version")),
                    xbmcgui.NOTIFICATION_INFO,
                    10000,
                )
                xbmc.sleep(2000)
                xbmc.executebuiltin("RestartApp")
                return "restart"
            return False
        except Exception as exc:
            filename = result.get("filename") or "nieznany plik ZIP"
            log("Mandatory update {} was rejected or could not be installed: {}".format(filename, exc), xbmc.LOGERROR)
            xbmcgui.Dialog().ok(
                "SudoSync — aktualizacja zablokowana",
                "W folderze .SudoSync znaleziono nowszy pakiet aktualizacji ({}), "
                "ale nie można potwierdzić jego autentyczności lub poprawnie go "
                "zainstalować. SudoSync nie uruchomi synchronizacji na starej "
                "wersji. Administrator powinien zastąpić albo usunąć tę paczkę. "
                "Szczegóły zapisano w dzienniku Kodi.".format(filename),
            )
            return "blocked"
    return False
