# Architektura i mechanizmy SudoSync (wersja 2.x)

KodiBridge by Sudoizm 2.x (dawniej SudoSync) to rozwinięcie tego samego dodatku z wersji 1.x. Sprawdzone zachowania ścieżki LIVE z 1.x realizuje obecnie silnik v2, który od wydania 2.2.0 jest jedynym silnikiem synchronizacji. Niniejszy dokument opisuje architekturę techniczną, model danych, mechanizmy współbieżności i procedury synchronizacji wtyczki **SudoSync**. Opisy szczegółów implementacyjnych mogą dotyczyć wcześniejszego wydania i należy je weryfikować wobec bieżącego kodu.

---

## 1. Architektura systemu

Cała synchronizacja działa w jednym silniku: modułowym podsystemie v2 (`sudosync_v2`). Dawny silnik 1.x (`sudosync_merge.py` i cykl LIVE v1 w `sudosync_core.py`) został usunięty; jego pliki na NAS nie są ani czytane, ani zapisywane, ani kasowane.

### 1.1. Punkty wejściowe usługi
- **`service.py` (Usługa w tle):** Główna pętla działająca w procesie Kodi jako usługa (`xbmc.service`). Monitoruje zdarzenia odtwarzacza (`Player.OnPlay`, `Player.OnStop`), biblioteki wideo (`VideoLibrary.OnUpdate`, `VideoLibrary.OnScanFinished`) oraz cyklicznie wyzwala synchronizację stanów (LIVE).
- **`default.py` (Interfejs użytkownika i akcje):** Obsługuje wywołania z menu dodatków, konfigurator pierwszego uruchomienia (Bootstrap Wizard), okno podglądu statusu, ręczne nadawanie SudoSync ID oraz sprawdzanie i instalację aktualizacji.

### 1.2. Moduły rdzenne (Core / Bootstrap)
- **`sudosync_core.py`:** Funkcje wspólne: ustawienia, dostęp VFS, wspólna konfiguracja sieci, plik `status.json`, nadawanie i wstrzykiwanie KodiBridge ID do plików `.nfo` (`assign_sudosync_ids`) oraz walidacja i instalacja paczek aktualizacji. Nie zawiera już logiki synchronizacji.
- **`sudosync_bootstrap.py`:** Realizuje kreator pierwszego uruchomienia, weryfikację dostępu do udziału sieciowego, rejestrację klienta w sieci oraz zarządzanie sieciową dzierżawą aktywnego Kodi (`active_kodi_lock.json`).

### 1.3. Podsystem SudoSync v2 (`sudosync_v2`)
- **`SudoSyncRuntime` (`runtime.py`):** Centralny komponent orkiestrujący cykle synchronizacji, planowanie zmian (`planning.py`), generowanie i weryfikację snapshotów (`snapshot.py`) oraz uzgadnianie stanów początkowych (`initialization.py`). Przy włączonej w sieci opcji KodiBridge ID planowanie dopasowuje pozycje wyłącznie po KodiBridge ID (`sudosync`); przy wyłączonej po IMDb/TMDb/TVDb/Trakt.
- **`TransactionRunner` (`transaction.py`):** Wykonuje atomowe modyfikacje bazy danych Kodi za pośrednictwem JSON-RPC. Każda wieloetapowa operacja rejestrowana jest w dzienniku transakcji (`.journal`) z możliwością natychmiastowego wycofania (rollback) w przypadku błędu.
- **`recover_incomplete_transactions`:** Mechanizm automatycznego odzyskiwania spójności po awarii (crash recovery). Przy starcie usługi skanuje profil w poszukiwaniu przerwanych dzienników transakcji i doprowadza je do stanu spójnego. Pozycje zmienione w Kodi od chwili przerwania są pomijane bez zapisu (aktualna wartość w Kodi wygrywa), więc przerwany zapis nie blokuje synchronizacji na stałe.
- **`KodiJsonRpcAdapter` (`kodi_rpc.py`):** Izoluje komunikację z oficjalnym API JSON-RPC Kodi. Zawiera zabezpieczenia specyficzne dla wersji (np. eliminacja błędu `-32602: Invalid params` w Kodi 21 poprzez pominięcie parametru `sort` w zapytaniu `VideoLibrary.GetEpisodes` i deterministyczne sortowanie w Pythonie) oraz wzbogacanie metadanych o identyfikatory z plików `.nfo`.

---

## 2. Mechanizmy współbieżności i blokady ochronne

### 2.1. Strażnik pojedynczego aktywnego Kodi w sieci (`active_kodi_lock.json`)
W celu uniknięcia konfliktów zapisu w środowisku współdzielonego zasobu sieciowego (SMB/NFS), SudoSync realizuje model pracy jednego aktywnego urządzenia:
- W katalogu systemowym udziału (`<base_path>/.KodiBridge/system/`, do wersji 2.2.11 `.SudoSync`) tworzony jest plik dzierżawy `active_kodi_lock.json`.
- **Dzierżawa czasowa (TTL):** Ważność dzierżawy wynosi 180 sekund.
- **Odświeżanie pulsu (Heartbeat):** Aktywna usługa odświeża dzierżawę co 60 sekund (`refresh_network_active_kodi_lease`).
- Jeśli inne urządzenie Kodi posiada aktywną dzierżawę, nowo uruchomione Kodi wstrzymuje wykonywanie operacji sieciowych. Po wygaśnięciu TTL (np. po nagłym zaniku zasilania poprzedniego urządzenia) dzierżawa może zostać bezpiecznie przejęta.
- Podczas każdego odświeżenia sprawdzana jest także minimalna wersja wtyczki (`min_addon_version`). Jeśli wersja na NAS jest wyższa niż lokalna, dzierżawa zostaje przerwana i od razu pojawia się propozycja aktualizacji (podczas odtwarzania po zatrzymaniu filmu). Po odmowie usługa nie synchronizuje do następnego uruchomienia Kodi.
- Przy normalnym zamykaniu Kodi lub zatrzymaniu wtyczki dzierżawa jest natychmiast zwalniana w bloku `finally`.

### 2.2. Lokalna blokada usługi (`local_service_guard.py`)
Zabezpiecza przed jednoczesnym uruchomieniem więcej niż jednej instancji usługi w tle w ramach tego samego profilu Kodi:
- Atomowe utworzenie pliku blokady `service.lock` z flagami `os.O_CREAT | os.O_EXCL`.
- Zwolnienie blokady realizowane w bloku `try/finally` funkcji `run_service_guarded` oraz zabezpieczone procedurą `atexit`.

### 2.3. Blokada nadawania identyfikatorów (`assign_ids.lock`)
Chroni procedurę skanowania biblioteki i nadawania identyfikatorów SudoSync ID przed równoległym wywołaniem (np. automatycznym w tle i ręcznym z GUI):
- Blokada tworzona lokalnie oraz na udziale sieciowym z czasem ważności TTL = 1800 sekund.
- Odświeżanie znacznika czasu następuje po przetworzeniu każdej pozycji w bibliotece.
- Wywołania równoległe w tle bezpiecznie rezygnują z pracy, a wywołania z GUI informują użytkownika o trwającej operacji.

---

## 3. Identyfikacja mediów i system SudoSync ID

### 3.1. Zasada uniwersalnej identyfikacji
W SudoSync 2.x identyfikacja nie ogranicza się do pozycji posiadających zewnętrzne identyfikatory z internetowych baz danych (IMDb, TMDb, TVDb, Trakt). W celu zapewnienia niezawodnej synchronizacji filmów domowych, nagrań prywatnych oraz pozycji z niepełnymi metadanymi, wdrożono uniwersalny system **SudoSync ID**:
- Wszystkie pozycje wideo w bibliotece (filmy, odcinki seriali, teledyski) otrzymują unikalny, równoległy identyfikator: losowy UUID w wersji 4 zapisany małymi literami (np. `3f9c2a7e-5b1d-4e8a-9c0f-7d2e1b6a4c90`).
- Wstrzykiwanie do plików `.nfo`: Znacznik `<uniqueid type="kodibridge" default="...">` jest trwale zapisywany w pliku `.nfo` danego wideo (lub tworzony jest minimalny `.nfo` w razie jego braku).
- **Stary znacznik:** Do wersji 2.2.11 identyfikator miał postać `{prefix}-{num:06d}` w `<uniqueid type="sudosync">`. Od 2.2.12 ten typ jest pomijany przy odczycie `.nfo` i bazy Kodi. Wewnątrz dodatku i w migawkach na NAS identyfikator nadal ma rodzaj `sudosync`; mapowanie `kodibridge` → `sudosync` wykonują `internal_id_kind` i `internal_ids_from_external` w `sudosync_v2/identity.py`.
- **Zasada nieingerencji w istniejące ID:** Jedynym wyjątkiem od nadania nowego identyfikatora są pozycje posiadające już niepusty identyfikator typu `kodibridge` w Kodi lub w pliku `.nfo`. Zapobiega to nadpisywaniu już ustalonych tożsamości.
- **Zachowanie atrybutu domyślnego:** Jeśli plik `.nfo` posiadał już znacznik domyślny (`default="true"`), wstrzykiwany tag otrzymuje `default="false"`. Gdy plik nie posiadał żadnego taga z atrybutem domyślnym, nowy identyfikator otrzymuje `default="true"`.

### 3.2. Nadawanie ID bez wspólnego licznika
- Każde Kodi nadaje UUID samodzielnie (`new_kodibridge_id`); wspólny licznik `sudosync_id_last_number` z wersji do 2.2.11 nie jest już używany przy nadawaniu ani naprawie.
- Blokada `system/assign_ids.lock` na NAS nadal szereguje przypisywanie, żeby dwa Kodi nie zapisywały jednocześnie tych samych plików `.nfo`.
- Duplikat może powstać tylko przez skopiowanie `.nfo` razem z plikiem wideo; naprawa nadaje wtedy nowy UUID.

### 3.3. Przejście z `.SudoSync` na `.KodiBridge` (2.2.12)
- Przy starcie bootstrap zamienia ścieżkę kończącą się na `.SudoSync` na sąsiedni folder `.KodiBridge` (`kodibridge_shared_path`).
- `migrate_legacy_shared_folder` przenosi tylko ustawienia sieciowe z `SudoSync.cfg` (bez licznika), ustawia `min_addon_version` nowej wersji w obu folderach, a lokalny licznik zeruje. Migawki i transakcje zostają w starym folderze, bo odnoszą się do starych ID.
- Lokalna pamięć identyfikatorów `.nfo` (`nfo_id_cache.json`) ma wersję 2; starsza jest odrzucana. Raporty przypisania i naprawy bez `"id_scheme": "kodibridge-uuid"` nie są używane do wycofania ani do zasilenia pamięci.

---

## 4. Separacja konfiguracji i mechanizmy aktualizacji

### 4.1. Separacja konfiguracji sieciowej od lokalnej
- **Konfiguracja sieciowa (`SudoSync.cfg` / `system/config.json`):** Przechowuje parametry wspólne dla całej sieci lokalnej: włączenie KodiBridge ID, wersję schematu oraz wymaganą minimalną wersję dodatku (`min_addon_version`).
- **Konfiguracja lokalna klienta (`settings.xml` w profilu dodatku):** Przechowuje unikalny UUID klienta, lokalną przyjazną nazwę (alias wyświetlany w UI), lokalną ścieżkę do udziału oraz preferencje powiadomień. Ustawienia lokalne nigdy nie są współdzielone ani nadpisywane przez inne urządzenia.

### 4.2. Bezpieczny mechanizm aktualizacji
Wycofano przestarzały mechanizm aktualizacji z niesprawdzonych plików wrzucanych do folderu NAS. Wdrożono nowoczesny, bezpieczny proces dwutorowy:
1. **GitHub Releases API:** Usługa sprawdza dostępność nowych oficjalnych wydań (`BreakTechEu/KodiBridge-by-Sudoizm`) raz na dobę kalendarzową, przy pierwszym uruchomieniu Kodi danego dnia (lub na żądanie z poziomu ustawień). Wtyczka nigdy nie dokonuje cichej aktualizacji w tle — każdorazowo wymaga jawnej akceptacji użytkownika w oknie dialogowym.
2. **Aktualizacja z lokalnego pliku ZIP:** Użytkownik może w ustawieniach wskazać paczkę `service.sudosync-X.X.X.zip` z dysku lokalnego lub udziału sieciowego.
3. **Walidacja integralności paczki:** Przed podmianą plików każda paczka ZIP przechodzi rygorystyczną walidację (`_validate_addon_zip_package`): badana jest integralność archiwum, kanoniczna struktura katalogów wtyczki Kodi, poprawność manifestu `addon.xml` oraz monotoniczność numeru wersji.
4. **Atomowa podmiana z kopią bezpieczeństwa:** Instalacja odbywa się poprzez rozpakowanie do katalogu tymczasowego, utworzenie kopii zapasowej dotychczasowej wtyczki i atomową podmianę katalogów.
5. **Strażnik minimalnej wersji sieciowej (`min_addon_version`):** Gdy którekolwiek Kodi w sieci zostanie zaktualizowane, podnosi parametr `min_addon_version` w `SudoSync.cfg`. Starsze instancje Kodi przy próbie synchronizacji lub odświeżenia dzierżawy wykrywają niezgodność i wstrzymują pracę do czasu ich zaktualizowania, zabezpieczając sieć przed uszkodzeniem danych przez starszy protokół.

---

## 5. Model danych i synchronizacja LIVE

- **Brak bezpośredniego I/O na bazach SQLite:** Całość komunikacji z Kodi odbywa się wyłącznie poprzez oficjalne JSON-RPC API (`VideoLibrary.SetMovieDetails`, `VideoLibrary.SetEpisodeDetails`).
- **Niezależne wersjonowanie atrybutów:** Każde synchronizowane pole (`playcount`, `lastplayed`, `resume`, `userrating`) posiada własny zegar logiczny Lamporta (`lc`) oraz licznik sekwencji klienta (`seq`).
- **Rozstrzyganie konfliktów (LWW per-field):** Konflikty rozstrzygane są na poziomie pojedynczych pól na korzyść wyższego zegara Lamporta, eliminując problem rozbieżności zegarów czasu rzeczywistego (RTC) urządzeń.
- **Ochrona przed pętlami zwrotnymi (Feedback-loop guard):** Zapis zmian do lokalnego Kodi rejestruje wersje docelowe (`target_versions`), dzięki czemu kolejny lokalny snapshot nie traktuje zaaplikowanych zdalnych zmian jako nowych modyfikacji lokalnych.
- **Kwarantanna skanowania biblioteki:** Nowo zaindeksowane pozycje oraz importy metadanych NFO otrzymują wersję zerową (`lc=0, seq=0`) oraz flagę `suppress_local_changes`, chroniąc istniejący na NAS stan odtworzenia przed przypadkowym nadpisaniem.
- **Ochrona podczas aktywnego odtwarzania:** W trakcie trwania seansu wtyczka wstrzymuje zdalne zapisy i synchronizację pozycji, aby nie zakłócać płynności odtwarzania.
