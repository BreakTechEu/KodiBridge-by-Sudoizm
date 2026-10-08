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
    log("Ustalono nowÄ… Ĺ›cieĹĽkÄ™ bazowÄ…: {}".format(path))

def test_vfs_rw(test_dir):
    try:
        if not xbmcvfs.exists(test_dir):
            if not xbmcvfs.mkdirs(test_dir):
                return False, "Nie moĹĽna utworzyÄ‡ katalogu .SudoSync"
        
        test_file = test_dir + "rw_test.tmp"
        f = xbmcvfs.File(test_file, 'w')
        f.write("test")
        f.close()
        
        f = xbmcvfs.File(test_file, 'r')
        data = f.read()
        f.close()
        
        xbmcvfs.delete(test_file)
        
        if data != "test":
            return False, "BĹ‚Ä…d weryfikacji zapisu/odczytu"
            
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
        f = xbmcvfs.File(filepath, 'r')
        data = f.read()
        f.close()
        return json.loads(data)
    except Exception as exc:
        log("Cannot read JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return None

def _write_json_file(filepath, obj):
    try:
        import json
        data = json.dumps(obj, indent=2, sort_keys=True)
        # Bezpieczny zapis przez plik tymczasowy i kopiÄ™ zapasowÄ…
        tmp_file = filepath + ".tmp"
        bak_file = filepath + ".bak"
        
        f = xbmcvfs.File(tmp_file, 'w')
        result = f.write(data)
        f.close()
        
        if not result:
            log("Nie udaĹ‚o siÄ™ zapisaÄ‡ peĹ‚nych danych do .tmp dla {}".format(filepath), xbmc.LOGERROR)
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

def run_migration_wizard(base_path):
    xbmcgui.Dialog().ok(
        "SudoSync - Migracja",
        "Wykryto istniejÄ…cÄ… instalacjÄ™ starszej wersji SudoSync w wybranym folderze.\n\nTwoje dotychczasowe dane i identyfikatory zostanÄ… zachowane. SudoSync wyodrÄ™bni teraz ustawienia sieciowe do nowego pliku SudoSync.cfg."
    )
    
    # WyciÄ…gamy ustawienia sieciowe, jeĹ›li jakieĹ› byĹ‚y w starym system/config.json
    _, sys_config = _read_json_file(base_path + "system/config.json")
    sys_config = sys_config or {}
    old_net = sys_config.get("network_settings", {})
    
    # JeĹ›li nie byĹ‚o starych, bierzemy lokalne preferencje z addon_data 
    # (to moĹĽe byÄ‡ przypadek gdy SudoSync ID wĹ‚Ä…czono lokalnie, ale nie opublikowano do starej bazy)
    addon = xbmcaddon.Addon(ADDON_ID)
    sudosync_id_enabled = old_net.get("sudosync_id_enabled", addon.getSetting("sudosync_id_enabled").strip().lower() == "true")
    sudosync_id_prefix = old_net.get("sudosync_id_prefix", addon.getSetting("sudosync_id_prefix").strip() or "NAS")
    
    new_cfg = {
        "format": "SudoSync configuration",
        "schema_version": 1,
        "configuration": {
            "completed": True,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        },
        "network_settings": {
            "sudosync_id_enabled": bool(sudosync_id_enabled),
            "sudosync_id_prefix": str(sudosync_id_prefix)
        }
    }
    
    if not write_sudosync_cfg(base_path, new_cfg):
        xbmcgui.Dialog().ok("BĹ‚Ä…d", "Nie udaĹ‚o siÄ™ zapisaÄ‡ zmigrowanej konfiguracji do SudoSync.cfg")
        return False
        
    xbmcgui.Dialog().notification("Migracja", "Migracja zakoĹ„czona sukcesem.", xbmcgui.NOTIFICATION_INFO, 5000)
    return True

def run_new_config_wizard(base_path):
    dialog = xbmcgui.Dialog()
    if not dialog.yesno("SudoSync - Konfigurator sieciowy", "Skonfigurujemy teraz nowe Ĺ›rodowisko synchronizacji SudoSync dla Twojej domeny.\n\nCzy chcesz domyĹ›lnie uĹĽywaÄ‡ niezawodnego identyfikatora 'SudoSync ID' dla wideo pozbawionych standardowych ID (IMDb/TMDb)?", yeslabel="Tak (Zalecane)", nolabel="Nie, uĹĽyj IMDb"):
        sudosync_id_enabled = False
        sudosync_id_prefix = ""
    else:
        sudosync_id_enabled = True
        prefix = dialog.input("Podaj krĂłtki prefiks dla tej sieci (np. NAS lub DOM):", type=xbmcgui.INPUT_ALPHANUM)
        sudosync_id_prefix = prefix.strip() if prefix.strip() else "NAS"
        
    new_cfg = {
        "format": "SudoSync configuration",
        "schema_version": 1,
        "configuration": {
            "completed": True,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        },
        "network_settings": {
            "sudosync_id_enabled": sudosync_id_enabled,
            "sudosync_id_prefix": sudosync_id_prefix
        }
    }
    
    if not write_sudosync_cfg(base_path, new_cfg):
        xbmcgui.Dialog().ok("BĹ‚Ä…d", "Nie udaĹ‚o siÄ™ zapisaÄ‡ nowej konfiguracji do SudoSync.cfg")
        return False
        
    dialog.ok("Sukces", "Ĺšrodowisko .SudoSync skonfigurowane poprawnie!\nTeraz synchronizacja moĹĽe siÄ™ rozpoczÄ…Ä‡.")
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
        log("Konfiguracja sieciowa SudoSync ID zostaĹ‚a wymuszona na lokalnym kliencie.")

def run_bootstrap():
    """Zwraca True, jeĹ›li bootstrap zakoĹ„czony i moĹĽna przejĹ›Ä‡ do logiki operacyjnej, False w przypadku wstrzymania/bĹ‚Ä™du."""
    log("RozpoczÄ™cie fazy BOOTSTRAP")
    base_path = get_base_path()
    
    # 1. USTALENIE BASE PATH
    # JeĹĽeli brak base_path LUB Ĺ›cieĹĽka to stara Ĺ›mieciowa defaultowa domyĹ›lna SMB
    if not base_path or base_path == "smb://192.168.69.100/Wideo/.SudoSync/" or not base_path.endswith("/"):
        dialog = xbmcgui.Dialog()
        dialog.ok("SudoSync", "Musisz wskazaÄ‡ folder sieciowy (np. SMB/NFS), w ktĂłrym SudoSync bÄ™dzie przechowywaÄ‡ wspĂłlnÄ… konfiguracjÄ™ i bazÄ™ dla wszystkich urzÄ…dzeĹ„ Kodi w tej sieci.")
        
        selected_path = dialog.browse(3, "Wybierz katalog wspĂłĹ‚dzielony (np. Wideo)", "network")
        if not selected_path:
            log("User cancelled path selection.")
            return False
            
        selected_path = selected_path.replace('\\', '/')
        if not selected_path.endswith('/'):
            selected_path += '/'
            
        # Zabezpieczenie przed zagnieĹĽdĹĽaniem .SudoSync/.SudoSync
        if selected_path.endswith(".SudoSync/"):
            base_path = selected_path
        else:
            base_path = selected_path + ".SudoSync/"
            
        set_base_path(base_path)
        
    # 2. TEST DOSTÄPU I READ/WRITE
    log("Testowanie dostÄ™pu R/W do {}".format(base_path))
    success, msg = test_vfs_rw(base_path)
    if not success:
        xbmcgui.Dialog().ok("SudoSync - Brak DostÄ™pu", "Nie udaĹ‚o siÄ™ zapisaÄ‡ plikĂłw w wybranym folderze:\n{}\n\nSprawdĹş uprawnienia do zapisu lub wskaĹĽ inny udziaĹ‚ w ustawieniach.".format(msg))
        return False
        
    # 3. SPRAWDZENIE SUDOSYNC.CFG
    status, cfg = read_sudosync_cfg(base_path)
    if status == "VALID":
        # WALIDACJA ISTNIEJÄ„CEGO SudoSync.cfg
        if not isinstance(cfg, dict):
            xbmcgui.Dialog().ok("BĹ‚Ä…d SudoSync.cfg", "Plik SudoSync.cfg nie jest prawidĹ‚owym sĹ‚ownikiem JSON.")
            return False
        if cfg.get("schema_version") not in (1, 2):
            xbmcgui.Dialog().ok("BĹ‚Ä…d SudoSync.cfg", "NieobsĹ‚ugiwana wersja schematu (schema_version) w SudoSync.cfg.")
            return False
        if not isinstance(cfg.get("network_settings"), dict):
            xbmcgui.Dialog().ok("BĹ‚Ä…d SudoSync.cfg", "Brak sekcji network_settings w SudoSync.cfg.")
            return False
        if not isinstance(cfg.get("network_settings", {}).get("sudosync_id_enabled"), bool):
            xbmcgui.Dialog().ok("BĹ‚Ä…d SudoSync.cfg", "ZĹ‚y typ danych w sudosync_id_enabled.")
            return False
        if not isinstance(cfg.get("network_settings", {}).get("sudosync_id_prefix"), str):
            xbmcgui.Dialog().ok("BĹ‚Ä…d SudoSync.cfg", "ZĹ‚y typ danych w sudosync_id_prefix.")
            return False
        if not cfg.get("configuration", {}).get("completed"):
            xbmcgui.Dialog().ok("BĹ‚Ä…d SudoSync.cfg", "Konfiguracja w SudoSync.cfg nie jest oznaczona jako ukoĹ„czona.")
            return False
            
        log("SudoSync.cfg poprawnie zweryfikowany. Aplikowanie ustawieĹ„ sieciowych.")
        apply_network_settings(cfg)
        return True
    elif status in ("INVALID", "IO_ERROR"):
        import time
        corrupted_path = base_path + "SudoSync.cfg"
        backup_path = base_path + "SudoSync.cfg.corrupted_" + str(int(time.time()))
        if xbmcvfs.exists(corrupted_path):
            xbmcvfs.copy(corrupted_path, backup_path)
        xbmcgui.Dialog().ok("BĹ‚Ä…d Krytyczny", "Plik SudoSync.cfg jest uszkodzony lub nieczytelny. ZostaĹ‚a utworzona kopia zapasowa. Napraw plik rÄ™cznie lub usuĹ„ go, aby wymusiÄ‡ nowÄ… konfiguracjÄ™.")
        return False
    else:
        # BRAK SUDOSYNC.CFG
        # 4. Sprawdzenie, czy to starsza instalacja
        if is_legacy_installation(base_path):
            log("Wykryto starszÄ… instalacjÄ™ bez SudoSync.cfg -> MIGRATION WIZARD")
            if not run_migration_wizard(base_path):
                return False
        else:
            log("Brak starej instalacji -> NEW CONFIG WIZARD")
            if not run_new_config_wizard(base_path):
                return False
                
        # 5. PONOWNY ODCZYT I WALIDACJA PO WIZARDZIE
        status, cfg = read_sudosync_cfg(base_path)
        if status == "VALID" and cfg.get("configuration", {}).get("completed"):
            apply_network_settings(cfg)
            return True
            
        xbmcgui.Dialog().ok("BĹ‚Ä…d Krytyczny", "Nie udaĹ‚o siÄ™ odczytaÄ‡ SudoSync.cfg pomimo jego zapisu.")
        return False

def check_and_enforce_updates_before_bootstrap():
    """Wymuszona aktualizacja przez bootstrap."""
    from resources.lib.sudosync_core import check_for_update, install_latest_update
    # Sprawdzamy czy base_path jest na tyle zdatny (jeĹ›li to puste albo stary dummy, to pominie)
    base_path = get_base_path()
    if not base_path or base_path == "smb://192.168.69.100/Wideo/.SudoSync/":
        return False # brak Ĺ›cieĹĽki do folderu aktualizacji
        
    try:
        # Wymuszamy ominiÄ™cie ustawieĹ„ (ustawienia lokalne ignorujemy dla instalacji aktualizacji z NAS)
        result = check_for_update(show_notification=False, manual=False)
        if result.get("available"):
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
                return True
    except Exception as exc:
        log("BĹ‚Ä…d wymuszonej aktualizacji: {}".format(exc), xbmc.LOGWARNING)
    return False
