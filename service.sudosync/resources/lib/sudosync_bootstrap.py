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
    try:
        if not xbmcvfs.exists(filepath):
            return None
        f = xbmcvfs.File(filepath, 'r')
        data = f.read()
        f.close()
        return json.loads(data)
    except Exception as exc:
        log("Cannot read JSON {}: {}".format(filepath, exc), xbmc.LOGERROR)
        return None

def _write_json_file(filepath, obj):
    try:
        data = json.dumps(obj, indent=2, sort_keys=True)
        # Atomowy zapis z tmp by uniknąć korupcji
        tmp_file = filepath + ".tmp"
        f = xbmcvfs.File(tmp_file, 'w')
        f.write(data)
        f.close()
        if xbmcvfs.exists(filepath):
            xbmcvfs.delete(filepath)
        xbmcvfs.rename(tmp_file, filepath)
        return True
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
        "Wykryto istniejącą instalację starszej wersji SudoSync w wybranym folderze.\n\nTwoje dotychczasowe dane i identyfikatory zostaną zachowane. SudoSync wyodrębni teraz ustawienia sieciowe do nowego pliku SudoSync.cfg."
    )
    
    # Wyciągamy ustawienia sieciowe, jeśli jakieś były w starym system/config.json
    sys_config = _read_json_file(base_path + "system/config.json") or {}
    old_net = sys_config.get("network_settings", {})
    
    # Jeśli nie było starych, bierzemy lokalne preferencje z addon_data 
    # (to może być przypadek gdy SudoSync ID włączono lokalnie, ale nie opublikowano do starej bazy)
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

def run_bootstrap():
    """Zwraca True, jeśli bootstrap zakończony i można przejść do logiki operacyjnej, False w przypadku wstrzymania/błędu."""
    log("Rozpoczęcie fazy BOOTSTRAP")
    base_path = get_base_path()
    
    # 1. USTALENIE BASE PATH
    # Jeżeli brak base_path LUB ścieżka to stara śmieciowa defaultowa domyślna SMB
    if not base_path or base_path == "smb://192.168.69.100/Wideo/.SudoSync/" or not base_path.endswith("/"):
        dialog = xbmcgui.Dialog()
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
        
    # 2. TEST DOSTĘPU I READ/WRITE
    log("Testowanie dostępu R/W do {}".format(base_path))
    success, msg = test_vfs_rw(base_path)
    if not success:
        xbmcgui.Dialog().ok("SudoSync - Brak Dostępu", "Nie udało się zapisać plików w wybranym folderze:\n{}\n\nSprawdź uprawnienia do zapisu lub wskaż inny udział w ustawieniach.".format(msg))
        return False
        
    # 3. SPRAWDZENIE SUDOSYNC.CFG
    cfg = read_sudosync_cfg(base_path)
    if cfg:
        # WALIDACJA ISTNIEJĄCEGO SudoSync.cfg
        if not isinstance(cfg, dict):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Plik SudoSync.cfg nie jest prawidłowym słownikiem JSON.")
            return False
        if cfg.get("schema_version") not in (1,):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Nieobsługiwana wersja schematu (schema_version) w SudoSync.cfg.")
            return False
        if not cfg.get("configuration", {}).get("completed"):
            xbmcgui.Dialog().ok("Błąd SudoSync.cfg", "Konfiguracja w SudoSync.cfg nie jest oznaczona jako ukończona.")
            return False
            
        log("SudoSync.cfg poprawnie zweryfikowany. Aplikowanie ustawień sieciowych.")
        apply_network_settings(cfg)
        return True
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
        cfg = read_sudosync_cfg(base_path)
        if cfg and cfg.get("configuration", {}).get("completed"):
            apply_network_settings(cfg)
            return True
            
        xbmcgui.Dialog().ok("Błąd Krytyczny", "Nie udało się odczytać SudoSync.cfg pomimo jego zapisu.")
        return False

def check_and_enforce_updates_before_bootstrap():
    """Wymuszona aktualizacja przez bootstrap."""
    from resources.lib.sudosync_core import check_for_update, install_latest_update
    # Sprawdzamy czy base_path jest na tyle zdatny (jeśli to puste albo stary dummy, to pominie)
    base_path = get_base_path()
    if not base_path or base_path == "smb://192.168.69.100/Wideo/.SudoSync/":
        return False # brak ścieżki do folderu aktualizacji
        
    try:
        # Wymuszamy ominięcie ustawień (ustawienia lokalne ignorujemy dla instalacji aktualizacji z NAS)
        result = check_for_update(show_notification=False, manual=False)
        if result.get("available"):
            installed = install_latest_update(show_dialogs=False)
            if installed.get("installed"):
                xbmcgui.Dialog().notification(
                    "SudoSync zaktualizowano",
                    "Zainstalowano {}. Zmiany będą aktywne po restarcie Kodi.".format(installed.get("version")),
                    xbmcgui.NOTIFICATION_INFO,
                    10000,
                )
                return True # zrestartowano addon
    except Exception as exc:
        log("Błąd wymuszonej aktualizacji: {}".format(exc), xbmc.LOGWARNING)
    return False
