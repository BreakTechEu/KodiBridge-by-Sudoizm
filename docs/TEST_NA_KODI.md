# SudoSync 2.0.2 — instrukcja bezpiecznego testu na urządzeniu Kodi

Dokument prowadzi krok po kroku przez ręczny test wersji 2.0.2 (Beta) na pojedynczym urządzeniu Kodi (oraz sekwencyjnie między urządzeniami), z pełnym zabezpieczeniem danych przed rozpoczęciem pracy.

---

## Krok 1. Stan testu i identyfikacja paczki

- **Wersja wtyczki:** `2.0.2` (Beta), identyfikator dodatku w `service.sudosync/addon.xml`: `service.sudosync`.
- **Commit bazowy i paczka na GitHubie:** paczka `service.sudosync-2.0.2.zip` jest budowana bezpośrednio z wierzchołka gałęzi `master` i publikowana jako załącznik wydania `v2.0.2` w GitHub Releases (dzięki czemu wszystkie narzędzia i audytorzy widzą ten sam artefakt). Aktualny hash commitu sprawdzisz poleceniem:
  ```powershell
  git log -1 --oneline
  git rev-parse HEAD
  ```
- **Model aktualizacji:** wtyczka sprawdza aktualizacje w GitHub Releases (`find_github_update` / `maybe_check_github_update_daily`) oraz obsługuje ręczną instalację ze wskazanego pliku ZIP (`install_update_from_zip_path`), walidując strukturę archiwum i `addon.xml` w `_validate_addon_zip_package()`.
- **Podstawowy scenariusz testowy (99,9% użytkowników):** świeża instalacja po usunięciu poprzedniej wersji wtyczki (wraz z jej danymi profilu) ze wszystkich urządzeń Kodi oraz zmianie nazwy dotychczasowego folderu `.SudoSync` na NAS (np. na `.SudoSync.bak`), tak aby wtyczka utworzyła strukturę `.SudoSync` od zera.
- **Skutek:** potwierdzono pochodzenie i przeznaczenie pliku `service.sudosync-2.0.2.zip`.
- **Kryterium „zrobione”:** znasz dokładny hash commitu z `git rev-parse HEAD` i masz przygotowany plik `service.sudosync-2.0.2.zip`.

---

## Krok 2. Zasada jednego aktywnego Kodi i strażnika wersji (`min_addon_version`)

Rzeczywiste zachowanie kodu strażnika wersji i dzierżawy sesji (`sudosync_bootstrap.py:enforce_network_version_guard`, `acquire_network_active_kodi_lease`, `refresh_network_active_kodi_lease`):
- W sieci może działać jednocześnie tylko jedno urządzenie Kodi z aktywną usługą SudoSync (`active_kodi_lock.json`, TTL 180 s, odświeżany co 60 s w `service.py`).
- W pliku `.SudoSync/SudoSync.cfg` zapisywana jest najwyższa używana w sieci wersja wtyczki (`network_settings.min_addon_version`). Urządzenie ze starszą wersją SudoSync zostanie zatrzymane przy starcie (lub podczas odświeżenia dzierżawy w pętli usługi) do czasu aktualizacji wtyczki.

- **Skutek:** potwierdzono brak innych aktywnych instancji Kodi w sieci przed rozpoczęciem testu.
- **Kryterium „zrobione”:** wszystkie pozostałe urządzenia Kodi w sieci są wyłączone.

---

## Krok 3. Kopie zapasowe i oczyszczenie przed instalacją (przy wyłączonym Kodi)

Zamknij całkowicie program Kodi na wszystkich urządzeniach (zgodnie z zasadą z `docs/V2_USER_FLOW.md` w sieci nie może w tym czasie działać żadne inne Kodi) i zabezpiecz następujące elementy:

1. **Dotychczasowy folder `.SudoSync` na udziale NAS** — zmień jego nazwę (np. na `.SudoSync_kopia_przed_v2`) lub przenieś go poza udział sieciowy. Dzięki temu zachowasz pełną kopię dotychczasowych plików (`SudoSync.cfg`, `format.json`, `clients/`, `system/`, `live/`, `reports/`, `backups/`, `transactions/`), a nowa instalacja utworzy na NAS zupełnie czysty katalog `.SudoSync` (`sudosync_core.py:ensure_remote_layout`, `sudosync_bootstrap.py:read_sudosync_cfg`).
2. **Pliki bazy biblioteki wideo `MyVideos*.db`** z podkatalogu `userdata/Database/`. SudoSync komunikuje się z biblioteką wyłącznie przez JSON-RPC (`VideoLibrary.SetMovieDetails`, `VideoLibrary.SetEpisodeDetails`), ale kopia plików `MyVideos*.db` gwarantuje pełną odwracalność stanu biblioteki.
3. **Folder danych profilu wtyczki `userdata/addon_data/service.sudosync/`** — w kodzie odwoływany jako `special://profile/addon_data/service.sudosync/` (`sudosync_core.py:_addon_profile_path`, `local_service_guard.py:acquire_kodi_service_guard`). Zawiera m.in. `settings.xml`, `client.json`, `status.json`, `live_registry.json`, `live_peer_pulses.json`, `live_cycle_lock.json`, `sudosync-service.lock`, `initial_write_guard.json`, `initial_sync.journal`, `assign_ids_manifest.json`. Przy teście „na świeżo” skopiuj ten folder do kopii zapasowej, a następnie **usuń go z wszystkich instalacji Kodi** (lub usuń razem z odinstalowaniem poprzedniej wersji wtyczki w Kodi), aby wtyczka nie pamiętała starej ścieżki `base_path` i przeprowadziła pełny proces pierwszego uruchomienia.
4. **Folder kodu wtyczki `addons/service.sudosync/`** (jeśli istniał) — w kodzie odwoływany przez `xbmcaddon.Addon("service.sudosync").getAddonInfo("path")` oraz `special://home/addons/` (`sudosync_core.py:_extract_and_swap_addon`). Przy teście na świeżo upewnij się po odinstalowaniu poprzedniej wersji, że katalog `addons/service.sudosync/` nie istnieje na żadnym urządzeniu Kodi.
5. **Pliki `.nfo` filmów i seriali w folderach biblioteki mediów** — w kodzie (`sudosync_core.py:_nfo_candidates`) SudoSync odczytuje `<nazwa_pliku_wideo>.nfo` oraz dla filmów `movie.nfo`. Zwykły cykl v2 tylko czyta pliki `.nfo` (`KodiVFSMetadataReader`), natomiast ręczna akcja przypisywania identyfikatorów (`assign_sudosync_ids` w `sudosync_core.py`) modyfikuje istniejące pliki `.nfo` (tworząc kopie `.nfo.bak` i manifest `assign_ids_manifest.json` z sumą SHA-256) lub tworzy nowe pliki `.nfo`. Procedura wycofywania `rollback_sudosync_ids` została przetestowana wyłącznie w testach jednostkowych na atrapach (`tests/test_v2_service_and_nfo_rollback.py`), dlatego przed testem na rzeczywistej bibliotece należy wykonać niezależną kopię plików `.nfo`.

Ścieżki katalogu profilu Kodi (`special://home/` oraz `special://profile/`):
- W kodzie repozytorium zweryfikowane są wyłącznie logiczne ścieżki VFS rozwiązywane przez `xbmcvfs.translatePath(...)`: `special://profile/addon_data/service.sudosync/` oraz `special://home/addons/service.sudosync/`.
- Standardowe fizyczne lokalizacje katalogu danych Kodi w poszczególnych systemach operacyjnych (nie występują bezpośrednio w kodzie repozytorium — **niezweryfikowane** w kodzie):
  - **Windows** (**niezweryfikowane** w kodzie): `%APPDATA%\Kodi\userdata\Database\MyVideos*.db`, `%APPDATA%\Kodi\userdata\addon_data\service.sudosync\`, `%APPDATA%\Kodi\addons\service.sudosync\`.
  - **Android / Google TV** (**niezweryfikowane** w kodzie): `/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/userdata/Database/MyVideos*.db`, `/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/userdata/addon_data/service.sudosync/`, `/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/addons/service.sudosync/`.
  - **CoreELEC / LibreELEC** (**niezweryfikowane** w kodzie): `/storage/.kodi/userdata/Database/MyVideos*.db`, `/storage/.kodi/userdata/addon_data/service.sudosync/`, `/storage/.kodi/addons/service.sudosync/`.

- **Skutek:** pełna kopia stanu NAS, bazy Kodi, danych profilu dodatku, kodu dodatku i plików `.nfo` znajduje się w bezpiecznym miejscu, a w udziale NAS i na urządzeniach Kodi nie ma pozostałości po poprzedniej instalacji.
- **Kryterium „zrobione”:** kopia zapasowa `MyVideos*.db` i `.nfo` jest gotowa, stary folder `.SudoSync` na NAS ma zmienioną nazwę, a ze wszystkich urządzeń Kodi usunięto poprzednią wtyczkę wraz z folderem `userdata/addon_data/service.sudosync/`.

---

## Krok 4. Sprawdzenie sumy SHA-256 paczki przed instalacją

Przed przeniesieniem paczki na urządzenie Kodi lub jej wyborem w instalatorze sprawdź sumę kontrolną SHA-256 pliku `service.sudosync-2.0.2.zip` i porównaj ją z wartością wygenerowaną podczas budowy paczki.

- **Windows (PowerShell):**
  ```powershell
  Get-FileHash -Path .\service.sudosync-2.0.2.zip -Algorithm SHA256
  ```
- **Linux / CoreELEC / LibreELEC:**
  ```sh
  sha256sum service.sudosync-2.0.2.zip
  ```

- **Skutek:** potwierdzono integralność archiwum instalacyjnego przed wgraniem do Kodi.
- **Kryterium „zrobione”:** wyświetlony skrót SHA-256 jest identyczny z sumą kontrolną zbudowanej paczki.

---

## Krok 5. Instalacja zależna od stanu wyjściowego (główny przebieg: Wariant a — świeża instalacja)

Wybierz właściwy wariant zależnie od stanu wyjściowego:

### Wariant (a) [GŁÓWNY SCENARIUSZ TESTU]: Czysta instalacja (brak `.SudoSync` na NAS po zmianie nazwy, poprzednia wtyczka odinstalowana z Kodi)
1. Co zrobić:
   - Upewnij się, że na NAS nie ma folderu `.SudoSync` (stary folder ma zmienioną nazwę), a na urządzeniu Kodi nie ma `addons/service.sudosync/` ani `userdata/addon_data/service.sudosync/` (jeśli podczas odinstalowywania w Kodi wybrano zachowanie ustawień albo ręcznie zostawiono `userdata/addon_data/service.sudosync/client.json`, zachowany zostanie tylko dotychczasowy `client_id`, ale aby wymusić pełny kreator wyboru ścieżki, plik `settings.xml` nie może mieć ustawionego `base_path`).
   - Uruchom Kodi, wybierz *Ustawienia -> Dodatki -> Zainstaluj z pliku zip* i wskaż plik `service.sudosync-2.0.2.zip`.
2. Czego nie robić: nie uruchamiaj w tym samym czasie drugiego urządzenia Kodi.
3. Zachowanie kodu (`sudosync_bootstrap.py:run_bootstrap`): po instalacji i starcie usługi `get_base_path()` zwróci pusty ciąg, więc wtyczka wyświetli komunikat o konieczności wskazania folderu, otworzy przeglądarkę `dialog.browse(3, ...)`, utworzy podkatalog `.SudoSync/` we wskazanym udziale, wykona test zapisu pliku `rw_test.tmp` (`test_vfs_rw`) i — przy braku `SudoSync.cfg` oraz braku starego `system/config.json` — uruchomi kreator `run_new_config_wizard()`, zapisując nowy plik `.SudoSync/SudoSync.cfg`, po czym automatycznie zarejestruje urządzenie (`_ensure_client_enrolled_and_initialized`) i utworzy lokalny snapshot pierwszego urządzenia.

### Wariant (b): Zainstalowany SudoSync 1.0.0 z pozostawionymi danymi v1 w `.SudoSync` (scenariusz migracji)
1. Co zrobić:
   - Przy wyłączonym Kodi upewnij się, że kopia zapasowa z kroku 3 jest gotowa.
   - Pozostaw bez zmian katalog `userdata/addon_data/service.sudosync/` oraz dotychczasowy `.SudoSync` na NAS.
   - Przy wyłączonym Kodi usuń stary katalog kodu `addons/service.sudosync/`.
   - Uruchom Kodi, zainstaluj `service.sudosync-2.0.2.zip` przez *Ustawienia -> Dodatki -> Zainstaluj z pliku zip*, a bezpośrednio po instalacji zamknij i uruchom Kodi ponownie.
2. Czego nie robić: nie usuwaj katalogu `userdata/addon_data/service.sudosync/` ani zawartości `.SudoSync/clients/` i `.SudoSync/system/config.json`.
3. Zachowanie kodu (`sudosync_bootstrap.py:is_legacy_installation`, `run_migration_wizard`): przy starcie `run_bootstrap()` odczyta istniejący `base_path`, stwierdzi brak `SudoSync.cfg`, ale wykryje obecność `.SudoSync/system/config.json` oraz `.SudoSync/clients/`. Zamiast kreatora nowej instalacji uruchomi `run_migration_wizard()`: wyświetli okno `SudoSync - Migracja`, przeniesie ustawienia sieciowe do nowego pliku `.SudoSync/SudoSync.cfg` (`schema_version: 1`, `configuration.completed: True`) i zachowa wszystkie dotychczasowe snapshoty oraz identyfikatory klientów.

### Wariant (c): Aktualizacja wcześniejszej wersji 2.0.x bez kasowania `.SudoSync`
1. Co zrobić:
   - Możesz zaktualizować wtyczkę bezpośrednio w ustawieniach SudoSync (*Sprawdź aktualizacje w GitHub Releases* lub *Zainstaluj aktualizację ze wskazanego pliku ZIP*) i zatwierdzić ponowne uruchomienie Kodi, albo przy wyłączonym Kodi zainstalować nową paczkę `service.sudosync-2.0.2.zip`.
2. Zachowanie kodu (`sudosync_bootstrap.py:run_bootstrap`): jeżeli w `.SudoSync` znajduje się już poprawny plik `SudoSync.cfg`, bootstrap nie wyświetli okien kreatora, podbije `min_addon_version` do `2.0.2`, wymusi spójność lokalnych ustawień przez `apply_network_settings(cfg)`, odnowi dzierżawę `active_kodi_lock.json` i od razu przekaże sterowanie do usługi.

- **Skutek:** w katalogu `addons/service.sudosync/` znajduje się wyłącznie czysty kod z testowanego commitu, a usługa startuje w procesie Kodi.
- **Kryterium „zrobione”:** dodatek SudoSync w wersji `2.0.2` jest widoczny jako aktywny w Kodi.

---

## Krok 6. Wyczyszczenie starych śladów snapshotu v2 (pomijane przy czystej instalacji po zmianie nazwy `.SudoSync`)

> **Uwaga dla świeżej instalacji (Wariant a):** Jeżeli przed testem zmieniłeś nazwę całego folderu `.SudoSync` na NAS (krok 3), katalog `.SudoSync/clients/` jeszcze nie istnieje i nie ma w nim żadnych starych snapshotów — **ten krok jest automatycznie spełniony i możesz od razu przejść do Kroku 7**.

Jeżeli jednak testujesz na istniejącym folderze `.SudoSync`, w którym działała wcześniej kompilacja v2 sprzed commitu `6c9f110` (np. `029edab`, `15e7011`), jej pierwszy opublikowany snapshot v2 mógł otrzymać niezerowe wersje logiczne `lc` (dziedziczone z zegara logicznego zamiast `lc: 0, seq: 0`). Poprawka z commitu `6c9f110` w `sudosync_v2/snapshot.py:_record_versions` nadaje wersje zerowe wyłącznie wtedy, gdy klient nie ma jeszcze poprzedniego snapshotu v2 (`has_previous_snapshot=False`). Jeśli stary plik v2 zostanie na NAS, `build_snapshot()` odczyta z niego zawyżone `old_version` i zachowa je w kolejnych cyklach.

Ścieżki i pliki zweryfikowane w kodzie (`sudosync_v2/catalog.py:SnapshotCatalog`, `sudosync_v2/storage.py:SharedJsonRepository`, `sudosync_core.py:snapshot_path`):
- Katalog współdzielony v2 używa tego samego folderu `.SudoSync/clients/` co silnik v1 (`self._clients_path = self._base_path + "/clients"` w `catalog.py:53`).
- Plik snapshotu v2 tego klienta nosi nazwę:
  `.SudoSync/clients/client-<client_id>.json`
  (gdzie `<client_id>` to pełny 36-znakowy kanoniczny UUID z pliku `userdata/addon_data/service.sudosync/client.json`, np. `client-11111111-1111-4111-8111-111111111111.json`, o polu `"format": "SudoSync v2 snapshot"`).
- Dodatkowo `SharedJsonRepository.save()` (`storage.py:116`) tworzy przy zapisie plik kopii zapasowej:
  `.SudoSync/clients/client-<client_id>.json.bak`
  Uwaga: w `SharedJsonRepository.load()` (`storage.py:103-110`), jeżeli usuniesz wyłącznie plik główny `client-<client_id>.json`, a pozostawisz `client-<client_id>.json.bak`, to przy pierwszym odczycie repozytorium automatycznie przywróci stary snapshot z pliku `.bak`.
- Pliki o nazwach `<nazwa_klienta>--<8_znakow_id>.json` (np. `Salon--11111111.json`, `"format": "SudoSync client snapshot"`) to snapshoty silnika v1 (`sudosync_core.py:1155`) — nie należy ich usuwać, jeżeli środowisko ma już ukończoną inicjalizację v1.

Jak rozpoznać, że ten krok jest potrzebny (gdy nie zmieniano nazwy `.SudoSync`):
1. Odczytaj `client_id` urządzenia z `userdata/addon_data/service.sudosync/client.json`.
2. Sprawdź przy wyłączonym Kodi, czy w `.SudoSync/clients/` istnieje plik `client-<client_id>.json` lub `client-<client_id>.json.bak`.
3. Jeśli istnieje i pochodzi z kompilacji sprzed `6c9f110` (lub w sekcji `"movies"` / `"episodes"` w `"versions"` ma `"lc"` większe od `0` przy `"seq": 0`), skopiuj oba pliki (`client-<client_id>.json` oraz `client-<client_id>.json.bak`) do katalogu kopii zapasowej poza NAS, a następnie usuń oba z `.SudoSync/clients/` przed pierwszym uruchomieniem nowej paczki.

- **Skutek:** przy pierwszym uruchomieniu nowej paczki `v2_runtime` zbuduje pierwszy snapshot v2 tego klienta z `previous_snapshot=None` i przypisze wszystkim polom bezpieczne wersje zerowe `{"lc": 0, "seq": 0, "client_id": "<client_id>"}`.
- **Kryterium „zrobione”:** na NAS nie ma starego folderu `.SudoSync` (zmieniono mu nazwę) albo w `.SudoSync/clients/` nie ma starego pliku `client-<client_id>.json` ani `client-<client_id>.json.bak` sprzed commitu `6c9f110`.

---

## Krok 7. Pierwsze uruchomienie i okna dialogowe

Uruchom Kodi i obserwuj komunikaty na ekranie. Zależnie od stanu `.SudoSync` mogą pojawić się następujące okna:

1. **Selektor katalogu (`sudosync_bootstrap.py:225-243`, tylko gdy `base_path` w ustawieniach jest pusty):**
   - Okno `SudoSync` z informacją o konieczności wskazania folderu sieciowego, a po nim przeglądarka `Wybierz katalog współdzielony (np. Wideo)`.
   - Co odpowiedzieć: wskaż udział sieciowy SMB/NFS (lub istniejący folder `.SudoSync`).
2. **Okno migracji (`sudosync_bootstrap.py:127-162`, gdy brak `SudoSync.cfg`, ale istnieją `system/config.json` i `clients/`):**
   - Okno `SudoSync - Migracja` informujące o zachowaniu danych i utworzeniu `SudoSync.cfg`.
   - Co odpowiedzieć: potwierdź przyciskiem `OK`.
3. **Konfigurator nowej sieci (`sudosync_bootstrap.py:164-192`, tylko przy zupełnie pustym `.SudoSync`):**
   - Pytanie `SudoSync - Konfigurator sieciowy` o domyślne używanie `SudoSync ID` (`Tak (Zalecane)` / `Nie, użyj IMDb`).
   - Co odpowiedzieć ostrożnie: sam wybór `Tak (Zalecane)` i podanie prefiksu zapisuje wyłącznie flagę w `SudoSync.cfg` i **nie modyfikuje jeszcze żadnych plików `.nfo`**. Jeżeli jednak podczas pierwszego testu chcesz całkowicie wykluczyć możliwość późniejszego generowania `SudoSync ID`, wybierz `Nie, użyj IMDb`.
4. **Ręczne zatwierdzenie inicjalizacji i zapisu NFO (`default.py`):**
   - Sama usługa w tle (`service.py`) przed zakończeniem inicjalizacji (`initialization.completed == False` w `system/config.json`) nigdy nie wyświetla okien zapisu do biblioteki i nie modyfikuje bazy Kodi — wykonuje wyłącznie zapis własnego snapshotu i analizę `DRY RUN`.
   - Okna zapisu pojawiają się wyłącznie po ręcznym wywołaniu akcji w ustawieniach dodatku:
     - `SudoSync — uzbrojenie zapisu` (`arminitial`, `default.py:260`), a następnie `SudoSync — UWAGA: zapis do biblioteki` (`applyinitial`, `default.py:209`) i `SudoSync — ostatnie potwierdzenie` (`default.py:223`) — zatwierdź wyłącznie wtedy, gdy w oknie podglądu liczba pozycji (`Pozycji: ...`, `playcount`, `lastplayed`, `oceny`, `resume`) oraz nazwa urządzenia bazowego są zgodne z oczekiwaniami.
     - `SudoSync - własne identyfikatory` (`assignids`, `default.py:484`) — pyta `Znaleziono X plików do zmiany... Czy na pewno chcesz nadpisać pliki .nfo?`. Nie zatwierdzaj tej operacji, dopóki nie masz pełnej kopii plików `.nfo` z kroku 3.

Kryteria natychmiastowego przerwania testu (zamknij Kodi i przejdź do kroku 10):
- Pojawienie się okna `SudoSync — aktualizacja zablokowana` (`sudosync_bootstrap.py:351`) lub `Błąd Krytyczny` dotyczący uszkodzonego `SudoSync.cfg` (`sudosync_bootstrap.py:287`).
- Pojawienie się pytania o zapis do biblioteki (`SudoSync — UWAGA: zapis do biblioteki`) bez Twojego ręcznego kliknięcia akcji w ustawieniach albo z nieoczekiwanie dużą liczbą zmian.
- Cyklicznie powtarzające się błędy poziomu `ERROR` w pliku logu Kodi pochodzące z `[SudoSync]`.

- **Skutek:** usługa SudoSync przeszła fazę bootstrap, wykonała startowy przegląd `v2_runtime.start()` i weszła w pętlę główną bez nieautoryzowanych zmian w bibliotece.
- **Kryterium „zrobione”:** Kodi działa stabilnie, a w oknie *Ustawienia dodatku -> Status* (`default.py:show_status`) widoczny jest nagłówek `Tryb: SudoSync 2.0.0 — LIVE + powiadomienia zmian i ochrona nowszego odtwarzania` oraz poprawny `Client ID`.

---

## Krok 8. Kontrola logu Kodi

Sprawdź wpisy diagnostyczne zapisane przez wtyczkę (prefiksy `[SudoSync]` w `sudosync_core.py:133` oraz `[SudoSync Bootstrap]` w `sudosync_bootstrap.py:19`).

Lokalizacje pliku logu Kodi (`kodi.log`, a z poprzedniej sesji `kodi.old.log` — dokładne ścieżki systemowe nie występują w kodzie wtyczki i są oznaczone jako **niezweryfikowane** w kodzie):
- **Windows** (**niezweryfikowane** w kodzie): `%APPDATA%\Kodi\kodi.log`
  ```powershell
  Select-String -Path "$env:APPDATA\Kodi\kodi.log" -Pattern "\[SudoSync"
  ```
- **Android / Google TV** (**niezweryfikowane** w kodzie): `/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/temp/kodi.log`
- **CoreELEC / LibreELEC** (**niezweryfikowane** w kodzie): `/storage/.kodi/temp/kodi.log`
  ```sh
  grep -E "\[SudoSync" /storage/.kodi/temp/kodi.log
  ```

Dokładne frazy istniejące w `service.py` i `sudosync_core.py` oraz ich znaczenie:
- `V2 startup preview active=True reason=` (`service.py:320`, `LOGINFO`) — startowy podgląd v2 przebiegł poprawnie; odzyskiwanie sesji nie wykryło blokady.
- `V2 startup preview skipped X corrupted NFO file(s)` (`service.py:324`, `LOGWARNING`) — czytnik v2 pominął uszkodzone pliki `.nfo` bez przerywania cyklu.
- `V2 startup blocked by unrecovered session marker.` (`service.py:312`, `LOGERROR`) oraz `Skipping startup synchronization because v2 session recovery is blocked.` (`service.py:342`, `LOGERROR`) — w `.SudoSync/transactions/active-operation.json` pozostał marker nieodzyskanej transakcji; synchronizacja startowa została bezpiecznie wstrzymana.
- `V2 startup preview unavailable: ...` (`service.py:329`, `LOGWARNING`) — wyjątek podczas budowania podglądu startowego v2.
- `V2 initial snapshot status=published` lub `V2 initial snapshot status=unchanged` (`service.py:97`, `LOGINFO`) — opublikowano (lub potwierdzono brak zmian) początkowy snapshot v2 w `.SudoSync/clients/client-<client_id>.json` (wykonywane tylko gdy `init_completed=True`).
- `V2 initialization preview status=ready` (`service.py:107`, `LOGINFO`) — podgląd inicjalizacji v2 względem urządzenia bazowego zakończony poprawnie.
- `V2 live sync committed X item(s).` (`service.py:203`, `LOGINFO`) — cykl v2 zastosował X zmian z sieci w lokalnej bibliotece Kodi.
- `V2 live sync deferred (reason=...).` (`service.py:197`, `LOGINFO`) — cykl v2 został odłożony (np. `reason=video_playing` podczas odtwarzania wideo).
- `V2 live sync cycle error: ...` (`service.py:152`, `LOGERROR`) — błąd wykonania `v2_runtime.sync_cycle()`; pętla nie przechodzi do silnika v1 i włącza opóźnienie backoffu `max(live_poll, 30)` s.
- `V2 live sync blocked by unrecovered session; skipping cycle until resolved.` (`service.py:173`, `LOGERROR`) — cykl LIVE v2 zablokowany przez nieodzyskaną sesję; pętla włącza opóźnienie backoffu `max(live_poll, 30)` s.
- `V2 live sync returned invalid result: ...` (`service.py:161`, `LOGERROR`) oraz `V2 live sync returned non-ok status '...'; stopping cycle without running v1.` (`service.py:185`, `LOGERROR`) — nieprawidłowy wynik cyklu v2 zatrzymujący cykl przed uruchomieniem v1.
- `Startup synchronization failed: ...` (`service.py:369`, `LOGERROR`) — błąd podczas synchronizacji startowej (np. w silniku v1 przy ukończonej inicjalizacji, gdy w `.SudoSync/clients/` znajduje się mniej niż 2 poprawne snapshoty v1: `Synchronizacja bieżąca wymaga co najmniej dwóch poprawnych snapshotów.` z `sudosync_core.py:1967`).
- `Service cycle failed: ...` (`service.py:464`, `LOGERROR`) — nieobsłużony wyjątek w pętli głównej usługi.
- `LIVE waiting for clients: ...` (`service.py:355`, `LOGINFO`) — oczekiwanie silnika v1 na opublikowanie snapshotów w trybie `live` przez pozostałe znane urządzenia.
- `Snapshot written: X movies, Y episodes -> ...` (`sudosync_core.py:1224`, `LOGINFO`) — zapisano snapshot początkowy v1.
- `DRY RUN: X shared safe, Y ambiguous, Z rating conflicts -> ...` (`sudosync_core.py:2374`, `LOGINFO`) — zapisano raport analizy przed pierwszą synchronizacją.

- **Skutek:** zweryfikowano w logu brak błędów krytycznych i poprawną publikację snapshotu.
- **Kryterium „zrobione”:** w `kodi.log` widoczny jest wpis `V2 startup preview active=True` oraz brak wpisów `V2 live sync cycle error`, `V2 live sync blocked` i `Service cycle failed`.

---

## Krok 9. Sekwencyjny test zapisu LIVE między dwoma snapshotami na NAS

Zgodnie z kodem (`sudosync_core.py:1966` wymaga co najmniej dwóch snapshotów w `.SudoSync/clients/`, a `sudosync_v2/engine.py:106` porównuje stan lokalny z listą snapshotów peerów `client-<peer_id>.json`) oraz zasadą jednego aktywnego Kodi naraz z `docs/V2_USER_FLOW.md`, test wymiany zmian LIVE wykonaj sekwencyjnie (warunek wstępny: w `.SudoSync/system/config.json` pole `initialization.completed` ma wartość `true` oraz w `.SudoSync/clients/` istnieją snapshoty co najmniej dwóch klientów):

1. **Urządzenie A (źródło zmiany):**
   - Uruchom Kodi tylko na urządzeniu A (urządzenie B pozostaje wyłączone).
   - Zmień stan jednej wybranej pozycji testowej posiadającej mocny identyfikator (np. oznacz jeden film jako obejrzany lub zmień jego ocenę użytkownika).
   - Odczekaj co najmniej 5–10 sekund po zmianie (monitor w `service.py:385` uruchamia cykl po ok. 2 sekundach od powiadomienia `VideoLibrary.OnUpdate` lub `Player.OnStop`, a w razie statusu `busy` ponawia próbę po `LIVE_BUSY_RETRY_SECONDS = 10` s).
   - Sprawdź na NAS, że plik `.SudoSync/clients/client-<client_id_A>.json` (oraz snapshot v1 `<nazwa_A>--<id8_A>.json`) został zaktualizowany, a w `client-<client_id_A>.json` zmienione pole otrzymało wersję `"lc"` większą od poprzedniej.
   - Zamknij całkowicie Kodi na urządzeniu A.
2. **Urządzenie B (odbiorca zmiany):**
   - Uruchom Kodi na urządzeniu B (gdy urządzenie A jest już wyłączone).
   - Podczas cyklu startowego (`service.py:344`) lub pierwszego cyklu w pętli głównej `_execute_integrated_live_cycle()` wywoła `v2_runtime.sync_cycle()`, który zastosuje zmianę przez JSON-RPC, zapisze kopię i dziennik w `.SudoSync/transactions/`, zaktualizuje `client-<client_id_B>.json` z zachowaniem zwycięskiej wersji `target_versions` i zsynchronizuje snapshot v1 z `suppress_local_changes=True`.
   - Sprawdź w bibliotece Kodi na urządzeniu B, czy wybrany film ma nowy stan obejrzenia / ocenę, oraz w `kodi.log`, czy pojawił się wpis `V2 live sync committed 1 item(s).`

- **Skutek:** zmiana wykonana na urządzeniu A została przeniesiona na urządzenie B bez równoczesnej pracy obu urządzeń i bez powstania pętli zwrotnej.
- **Kryterium „zrobione”:** na urządzeniu B stan testowanego filmu odpowiada zmianie z urządzenia A, w logu widnieje `V2 live sync committed`, a kolejny cykl synchronizacji raportuje `0` zmian.

---

## Krok 10. Powrót do stanu sprzed testu (procedura wycofania)

Jeżeli chcesz całkowicie wycofać test i przywrócić stan sprzed kroku 3, wykonaj poniższe czynności dokładnie w tej kolejności:

1. **Zamknij całkowicie Kodi** na wszystkich urządzeniach (upewnij się, że proces Kodi nie działa w tle i nie trzyma blokad `sudosync-service.lock` ani `live_cycle_lock.json`).
2. **Usuń testową wersję wtyczki:** usuń katalog `addons/service.sudosync/` (oraz ewentualną paczkę `addons/packages/service.sudosync-2.0.0.zip`). Jeżeli przed testem była zainstalowana inna wersja wtyczki, przywróć jej katalog `addons/service.sudosync/` z kopii zapasowej z kroku 3.
3. **Przywróć folder `.SudoSync` na NAS:** usuń nowo utworzony podczas testu katalog `.SudoSync` i zmień nazwę zabezpieczonego w kroku 3 oryginalnego folderu (np. `.SudoSync_kopia_przed_v2`) z powrotem na `.SudoSync` (lub zastąp go pełną kopią wykonaną w kroku 3). Przywraca to poprzednie `SudoSync.cfg`, `system/config.json`, `clients/`, `live/`, `backups/` i `reports/`.
4. **Przywróć lokalne dane profilu `userdata/addon_data/service.sudosync/`:** zastąp ten katalog kopią zapasową z kroku 3 (lub usuń go, jeżeli przed testem SudoSync nie był zainstalowany na tym urządzeniu).
5. **Przywróć pliki `.nfo` filmów i seriali:** jeżeli podczas testu uruchamiałeś przypisywanie identyfikatorów `SudoSync ID` (`assignids`), przywróć pliki `.nfo` z kopii zapasowej wykonanej w kroku 3 (oraz usuń ewentualne pliki `.nfo.bak` i nowo utworzone pliki `.nfo`).
6. **Przywróć bazę biblioteki Kodi `MyVideos*.db`:** skopiuj pliki `MyVideos*.db` z kopii zapasowej z kroku 3 z powrotem do `userdata/Database/`, nadpisując pliki zmodyfikowane podczas testu.
7. **Uruchom ponownie Kodi** i sprawdź, że biblioteka wideo oraz stan dodatków są identyczne ze stanem sprzed testu.

- **Skutek:** środowisko NAS, lokalna baza `MyVideos*.db`, profil dodatku i pliki `.nfo` wróciły dokładnie do stanu sprzed rozpoczęcia testu.
- **Kryterium „zrobione”:** po uruchomieniu Kodi biblioteka i pliki na NAS są zgodne z kopią zapasową z kroku 3.
