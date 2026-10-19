SudoSync 2.0.7
==============

- Bezpieczny, atomowy zapis JSON w `sudosync_bootstrap.py` (`_write_json_file`), np. dla `SudoSync.cfg`:
  - najpierw zapis i weryfikacja pliku `.tmp` (istnieje i ma rozmiar większy od zera),
  - kopia zapasowa `.bak` z bezwzględnym sprawdzeniem wyniku `xbmcvfs.copy` - przy niepowodzeniu zapis jest przerywany, `.tmp` usuwany, a oryginał pozostaje nietknięty,
  - oryginał jest usuwany dopiero po potwierdzonej kopii `.bak`, a przy niepowodzeniu `rename` automatycznie przywracany z `.bak`.
- Weryfikacja integralności aktualizacji z GitHub Releases (`sudosync_core.py`): pobrany ZIP jest sprawdzany sumą SHA-256 przed rozpakowaniem. Oczekiwany skrót pochodzi z pola `digest` assetu, z treści wydania lub z towarzyszącego pliku `.sha256`. Niezgodność (lub brak skrótu) odrzuca aktualizację, a pobrany plik jest usuwany.
- Ochrona przed ZIP-bomb: limit 500 plików, 50 MB łącznie i 20 MB na plik, sprawdzany na podstawie nagłówków ZIP przed ekstrakcją oraz strumieniowo w trakcie wypakowywania.
- Poprawiony opis w CHANGELOG: wpis o weryfikacji RSA-2048 (wersja 2.0.0) nie odpowiadał kodowi - rzeczywisty mechanizm to HTTPS + SHA-256 + walidacja manifestu i struktury archiwum.
- CI (GitHub Actions): macierz Pythona 3.8, 3.10, 3.11, 3.12 w `ci.yml` i `v2-ci.yml`.
- Dokumentacja testu na Kodi (`docs/TEST_NA_KODI.md`): rozdzielono Wariant A (bezpieczny test na istniejącej bibliotece, bez ruszania `.SudoSync`) i Wariant B (izolowana czysta instalacja, wyłącznie środowisko laboratoryjne).

SudoSync 2.0.6
==============

- Pojedyncze modalne okno dialogowe postępu (`DialogProgress`) z aktywnym przyciskiem „Anuluj”:
  - Zastąpiono znikające powiadomienia narożne i okna w tle jednym modalnym oknem `xbmcgui.DialogProgress` na pierwszym planie, płynnie współdzielonym i aktualizowanym pomiędzy obiema czasochłonnymi fazami (Faza 1: odczyt i skanowanie biblioteki, Faza 2: nadawanie identyfikatorów SudoSync ID).
  - Wyświetlanie opisu bieżącego etapu, licznika pozycji `(X / Y)`, aktualnie przetwarzanego tytułu oraz wyraźnej informacji o automatycznym zamknięciu okna po zakończeniu operacji (`#32080`–`#32083` w `strings.po`).
  - Gwarantowane automatyczne zamykanie okna w bloku `finally`.
- Optymalizacja wydajności fazy 2 (`_PreviewResult`):
  - Wynik fazy 1 zachowuje pobrane elementy biblioteki (`.collected_items`), dzięki czemu faza 2 unika powtórnego czasochłonnego skanowania biblioteki przez JSON-RPC.
- Bezpieczna procedura anulowania i wycofywania transakcji (Graceful Abort & Rollback):
  - Regularne sprawdzanie stanu anulowania (`dialog.iscanceled()` oraz `xbmc.Monitor().abortRequested()`) w pętlach skanowania, odczytu NFO i zapisu identyfikatorów.
  - Anulowanie w fazie 1: natychmiastowe przerwanie generowania planu i bezpieczne pominięcie fazy 2.
  - Anulowanie w fazie 2: dokończenie bieżącej operacji atomowej, przerwanie pętli, oznaczenie manifestu `"cancelled": True`, bezwzględne zwolnienie blokady `assign_ids.lock` w `finally`.
  - Mechanizm transakcyjny v2 (`TransactionRunner`): bezpieczne wycofanie zmodyfikowanych pozycji do stanu `before`, oznaczenie statusu dziennika `"aborted"` i zwolnienie znacznika sesji.
  - Menedżer kontekstu (`__enter__` / `__exit__`) w `LocalServiceGuard`.

SudoSync 2.0.5
==============

- Atomowa rezerwacja wspólnego licznika SudoSync ID przed zapisem NFO:
  - Aktualizacja `SudoSync.cfg` (`network_settings.sudosync_id_last_number`) oraz `system/config.json` (`network_settings.sudosync_id_last_number` i `sudosync_id_last_number`) odbywa się atomowo wewnątrz tej samej blokady konfiguracyjnej (`update_shared_config`), z monotonicznym zachowaniem najwyższego numeru (`max(existing, target)`).
  - Przed zapisaniem każdego nowego identyfikatora do pliku `.nfo` (zanim zmodyfikowany zostanie plik na dysku/NAS), kandydat numeru jest najpierw trwale rezerwowany we wspólnym liczniku sieciowym na NAS oraz zapisywany w manifeście (`assign_ids_manifest.json` oraz `sudosync_id_manifest.json`). W razie niepowodzenia rezerwacji proces natychmiast przerywa działanie bez modyfikowania pliku `.nfo` (tryb fail-closed).
  - Gwarantuje to, że nawet przy nagłej awarii w trakcie indeksowania licznik na NAS jest zawsze `>=` najwyższemu numerowi w plikach `.nfo`, wykluczając duplikację ID na innych Kodi z częściowym dostępem do biblioteki.
- Lokalna i sieciowa blokada współbieżności wokół `assign_sudosync_ids`:
  - Wdrożono atomową blokadę procesu przypisywania identyfikatorów (`_acquire_assign_ids_lock` / `_release_assign_ids_lock`) w profilu lokalnym (`assign_ids.lock`) oraz w katalogu sieciowym NAS (`system/assign_ids.lock`) z mechanizmem automatycznego usuwania osieroconych blokad (TTL 1800 s) i odświeżaniem znacznika czasu w pętli.
  - Wywołanie równoległe w tle (`auto_mode=True`) cicho pomija przebieg i zwraca `0`, a wywołanie ręczne z GUI (`auto_mode=False`) wyświetla powiadomienie ostrzegawcze, że operacja już trwa w tle, i zwraca `0`.
  - Blok `try ... finally` zapewnia bezwzględne zwolnienie blokady po zakończeniu lub w razie wyjątku.
- Bezwzględne nadawanie SudoSync ID:
  - Identyfikator SudoSync ID jest nadawany wszystkim pozycjom wideo w bibliotece jako niezależny, równoległy identyfikator sieci lokalnej, niezależnie od obecności identyfikatorów zewnętrznych (IMDb, TMDb, TVDb, Trakt itp.).
  - Jedynym wyjątkiem są pozycje posiadające już niepusty identyfikator typu `sudosync` (zarówno w bazie Kodi, jak i w plikach `.nfo`), które są pomijane w celu ochrony przed nadpisaniem.
  - Podczas wstrzykiwania do istniejących plików `.nfo` zachowywane są wszystkie dotychczasowe tagi `<uniqueid>`, a nowy znacznik otrzymuje `default="false"` (jeżeli w pliku istnieje już jakikolwiek tag `default="true"`) lub `default="true"` (jeżeli w pliku nie było jeszcze znacznika domyślnego).
- Naprawa błędu JSON-RPC `-32602: Invalid params` w `VideoLibrary.GetEpisodes`:
  - W Kodi 21 usunięto przekazywanie parametru `"sort"` przy zapytaniu o całą kolekcję odcinków, eliminując błąd transportu JSON-RPC `-32602` i umożliwiając bezproblemowe pobieranie odcinków oraz budowanie podglądu synchronizacji. Deterministyczna kolejność zachowywana jest po stronie Pythona (sortowanie po `episodeid`).
- Komunikat startowy i informacja w GUI przed długim skanowaniem biblioteki:
  - W akcji przypisywania SudoSync ID (`assignids`), natychmiast przed rozpoczęciem skanowania biblioteki i plików NFO, wyświetlane jest modalne okno informacyjne oraz powiadomienie toast, ostrzegające o możliwym długim czasie trwania analizy (od kilku do kilkunastu minut przy dużej bibliotece na NAS) i zalecające nieprzerywanie pracy do momentu pojawienia się podsumowania.
  - Dodano pełne tłumaczenia w `strings.po` (pl_PL oraz en_GB).

SudoSync 2.0.4
==============

Bezawaryjna praca wielu instancji Kodi o różnym dostępie do zasobów wideo (np. częściowy vs pełny dostęp do udziałów NAS) oraz sekwencyjne, współdzielone numerowanie identyfikatorów SudoSync ID:
- Współdzielony licznik identyfikatorów `sudosync_id_last_number` zapisywany w konfiguracji sieciowej (`SudoSync.cfg` oraz `system/config.json`) i synchronizowany pomiędzy wszystkimi instancjami Kodi w sieci. Każde urządzenie wie, do jakiego numeru `SudoSync ID` (`{prefix}-{num:06d}`) zostały już przydzielone identyfikatory.
- Automatyczne nadawanie `SudoSync ID` nowym zasobom bez silnego identyfikatora podczas zbierania snapshotów (`_collect_initial_snapshot` oraz `collect_live_snapshot`), również wtedy, gdy dany zasób wideo występuje wyłącznie na jednym konkretnym urządzeniu Kodi.
- Bezawaryjne indeksowanie po stronie kolejnego urządzenia Kodi z szerszym dostępem do zasobów:
  - odświeżanie pamięci podręcznej `_NFO_ID_CACHE` przed każdym odczytem biblioteki, budowaniem bezpiecznej mapy tożsamości i przydzielaniem ID,
  - wstępne skanowanie istniejących plików `.nfo` na NAS przed nadawaniem nowych numerów (ochrona przed nadpisaniem lub duplikacją ID utworzonych wcześniej przez inne Kodi, nawet jeśli lokalna baza SQLite Kodi jeszcze ich nie zaimportowała),
  - obsługa istniejących plików `.nfo` zawierających jedynie słabe znaczniki `<uniqueid>` (np. `type="unknown"` lub `type="local"`) — dopisanie silnego identyfikatora `sudosync` bez błędu i bez naruszania plików posiadających już silne ID (`imdb`, `tmdb`, `tvdb`, `trakt`, `sudosync`),
  - wzbogacanie `get_movie_details()` i `get_episode_details()` w adapterze `KodiJsonRpcAdapter` (v2) o identyfikatory z plików `.nfo`, co zapobiega błędom weryfikacji transakcji przed odświeżeniem lokalnej bazy SQLite Kodi,
  - bezkonfliktowe łączenie stanu początkowego (`lc=0, seq=0`) w planerach `merge.py` (v2) i `sudosync_merge.py` (v1), gdy jedno urządzenie posiada już historię oglądania zasobu, a drugie urządzenie (z szerszym dostępem) dopiero zaindeksowało ten zasób jako nieobejrzany.

SudoSync 2.0.0
==============

Przebudowa architektury SudoSync (architektura v2) oraz kluczowe usprawnienia bezpieczeństwa i stabilności. Zgodnie z założeniami projektowymi, w usłudze Kodi z modułów v2 na starcie uruchamiane są jedynie procedury odzyskiwania oraz diagnostyczny podgląd (preview), podczas gdy zwykła synchronizacja nadal prowadzona jest sprawdzoną ścieżką v1:
- Nowe moduły domeny v2 przygotowane pod synchronizację: ścisła walidacja schematów snapshotów (`schema.py`), konserwatywne dopasowywanie tożsamości mediów (`identity.py`, znormalizowane typy, silne aliasy) oraz czytanie plików `.nfo` przez Kodi VFS (`kodi_vfs.py`).
- Silnik planowania i deterministycznego rozwiązywania konfliktów na poziomie poszczególnych pól (`planning.py`, `merge.py`) wykorzystywany do bezpiecznego podglądu synchronizacji (dry-run/preview) przy starcie usługi Kodi (`service.py`).
- Odporność podglądu synchronizacji przy starcie na uszkodzone pliki NFO (pomijanie i raportowanie błędów) oraz respektowanie sygnału przerwania (abortRequested).
- Silnik transakcyjny z dziennikiem zamiaru i rotacją kopii zapasowych (`transaction.py`, `transaction_store.py`) oraz mechanizmem markerów sesji (`session.py`) — wykorzystywany przy starcie usługi do weryfikacji stanu i bezpiecznego odzyskiwania przerwanych transakcji; nie jest jeszcze używany w zwykłej synchronizacji (która działa ścieżką v1).
- Opcjonalna blokada współdzielona na poziomie systemu operacyjnego (`native_shared_lock.py`) zabezpieczona przed ucieczką przez dowiązania symboliczne — pozostaje niepodłączona w uzgodnionym sekwencyjnym modelu pracy (jedno urządzenie Kodi naraz).
- Nowy katalog snapshotów współdzielonych (`catalog.py`) z weryfikacją historii i wersji pól.
- Konfigurator pierwszego uruchomienia (bootstrap) oparty na `SudoSync.cfg`, weryfikacji dostępu do zasobu sieciowego i obsłudze unikalnych identyfikatorów SudoSync ID z rozdzieleniem identyfikatora sieciowego od aliasu UI.
- Weryfikacja integralności pakietów aktualizacji: HTTPS, walidacja manifestu `addon.xml` i struktury archiwum oraz natychmiastowe blokowanie startu w razie odrzucenia paczki (weryfikacja sumy SHA-256 dodana w 2.0.7; wcześniej błędnie opisywana jako podpis RSA-2048).
- Ujednolicenie licencji do GPL-3.0-or-later oraz usunięcie zapisów o ograniczeniu użytku.
- Poprawki z 14.10.2026:
  - Naprawa podwójnie zakodowanych polskich znaków w komunikatach dialogowych oraz dodanie automatycznego testu kodowania UTF-8.
  - Usunięcie sztywnej ścieżki-wartownika (`SPECIAL_REMOTE_SENTINEL`) z kreatora bootstrapu i modułu głównego.
  - Zwalnianie blokady singletonu usługi lokalnej (`service.py` / `local_service_guard.py`) w bloku `finally` wokół wykonania usługi (nie tylko przez `atexit`).
  - Usprawnienie parsera wersji `_numeric_version` z pełnym porównywaniem etapów wersji przedwydaniowych (`alpha < beta < rc < final`, np. `beta2 > beta`).
- Poprawki i uzupełnienia z 16.10.2026:
  - Domknięcie pełnego cyklu synchronizacji w runtime v2 (`sync_cycle`, `publish_local_snapshot`, `initialization_preview`, `apply_initialization`) z ochroną przed pętlą zwrotną (`apply_plan_to_snapshot`, zachowanie zwycięskich `target_versions`) oraz kwarantanną skanowania/nowych pozycji (`suppress_local_changes`, wersja zerowa `lc=0, seq=0` dla nowo wykrytych mediów).
  - Naprawa krytycznego błędu `NameError: name 'secure_map' is not defined` podczas aplikowania zmian w `run_live_sync_cycle` w `sudosync_core.py`.
  - Usunięcie 7-dniowego limitu wieku snapshotów w `_validate_snapshot` i `_validate_remote_snapshot` (obsługa nadrabiania zaległości po długim wyłączeniu urządzenia Kodi przy zachowaniu blokady zegarów z przyszłości) oraz bezpieczne ignorowanie plików snapshotów v2 w `read_remote_snapshots`.
  - Łączenie identyfikatorów z bazy Kodi (JSON-RPC) z identyfikatorami z plików `.nfo` (`_record` i `_get_secure_local_identity_map` w `sudosync_core.py`).
  - Usunięcie martwego kodu w `_read_json_file` w `sudosync_bootstrap.py`.
  - Kontrola tożsamości przy zapisie (`_apply_one_planned_item`) działa w trybie fail-closed: elementy planu (`build_live_plan` oraz `build_dry_run`) zawierają posortowaną listę aliasów rekordu klienta, a brak aliasów po stronie planu lub lokalnej mapy przerywa zapis przed wywołaniem JSON-RPC.
  - Jawne logowanie ostrzeżeń (`LOGWARNING`) przy błędach odczytu filmów lub odcinków podczas budowania bezpiecznej mapy tożsamości (`_get_secure_local_identity_map`).
  - Obsługa znaczników czasu `generated_at` z milisekundami w `_validate_remote_snapshot` (blokada zegarów z przyszłości o tolerancji 1 godziny działa również dla snapshotów LIVE).
  - Dodanie testów jednostkowych ścieżki zapisu LIVE i weryfikacji tożsamości (`tests/test_live_apply_identity.py`).
  - Pierwsza publikacja snapshotu klienta w katalogu v2 (`_record_versions` przy `previous_snapshot is None`) przypisuje wszystkim polom wersje zerowe (`lc=0, seq=0`), zapobiegając nadpisywaniu historii peerów przez nowo dołączone urządzenie.
  - Wywołanie `publish_local_snapshot` w `_initial_snapshot_and_analyse` bramkowane parametrem `init_completed=True` (brak publikacji lokalnego snapshotu do v2 przed ukończeniem inicjalizacji).
  - Publikacja snapshotu po zaaplikowaniu inicjalizacji bazowej (`_sync_v2_after_initial_apply`) z jawnym `suppress_local_changes=True`, zapobiegającym podbijaniu wersji pól przejętych ze snapshotu bazy.
  - Mechanizm backoffu po błędzie lub blokadzie cyklu LIVE (`_live_retry_delay_seconds` w `service.py` z minimalnym opóźnieniem `max(live_poll, 30)` sekund) z zachowaniem flag `dirty` na kolejną próbę.
  - Zestaw testów jednostkowych weryfikujących wersje zerowe pierwszego snapshotu, scenariusz audytu z peerem, brak publikacji przed inicjalizacją, backoff pętli usługi i kontrolę flag (`tests/test_v2_first_publish_and_backoff.py`).
  - Rozszerzenie backoffu w pętli usługi (`_live_retry_delay_seconds` w `service.py`) o wynik `busy` bez błędu i blokady (stała `LIVE_BUSY_RETRY_SECONDS = 10` s) wraz z testami jednostkowymi (`tests/test_v2_busy_backoff.py`).

SudoSync 1.1.0 (v2 Beta)
======================

Dodano eksperymentalną obsługę "SudoSync ID" dla prywatnych materiałów wideo, które nie posiadają oficjalnego identyfikatora (IMDb/TMDb). SudoSync pozwala teraz wstrzykiwać własne unikalne ID prosto do plików `.nfo` po stronie współdzielonej biblioteki.

SudoSync 1.0.0
==============

0.4.1 naprawia też mylący DRY RUN przy mieszance snapshotów 0.3/0.4: zamiast tysięcy pozornych zmian oceny null -> 0 raport pokazuje oczekiwanie na aktualizację klientów.

SudoSync 0.4.x — historia wersji testowej

Local Kodi watch-state synchronization via a shared NAS folder.

The LIVE synchronization engine activates only after the shared
initialization is marked completed. Before that, the 0.3 safe initial workflow
remains in force.

LIVE synchronized fields:
- playcount / watched-unwatched state;
- lastplayed;
- resume position/total (including clearing resume);
- userrating 1-10 after initialization. Historical pre-initialization ratings
  outside 6/7/8 remain ignored until the user actually changes that rating.

Safety / conflict model:
- JSON-RPC only; never reads/writes Kodi SQLite/MyVideos directly;
- strong IDs (IMDb/TMDb/TVDb/Trakt) remain the cross-device identity;
- ambiguous ID groups and unidentified records remain skipped;
- verified local/network mirrors are supported and all local copies receive the
  winning state;
- each field has its own version (timestamp + sequence + client ID), so a newer
  real user change can lower playcount, clear resume, clear/change a rating, etc.;
- remote writes keep the winning version and therefore do not bounce back as a
  new local event (feedback-loop guard);
- new library items and scan/NFO-import changes get a deliberately old version,
  so they cannot overwrite remembered shared state;
- no writes are performed while Kodi is actively playing video;
- continuous applies keep a bounded rolling pre-live snapshot backup instead of
  producing unlimited full backups;
- a local cycle lock prevents double-running the same sync on one Kodi.

Operation:
- live synchronization defaults ON after completed initialization;
- local user changes normally trigger a cycle about 2 seconds after Kodi notifications;
- other clients are polled every 60 seconds by default (configurable 30-600 s);
- Player.OnStop triggers a prompt cycle;
- during VideoLibrary scan, user-change detection is suppressed; scan/NFO changes
  cannot become authoritative merely because they were imported;
- while upgrading from 0.3, 0.4 waits until every known client has written a
  versioned LIVE snapshot before any background LIVE write is allowed.

Shared path defaults to empty (configured via first-run setup wizard).

Existing 0.3 protections retained:
- explicit base-device selection and clear-before-replace rule;
- one-shot initial write safety interlock;
- two confirmations and pre-initial-write full backup;
- visible progress notifications for manual writes;
- self-update from the hidden .SudoSync folder.


0.4.2-alpha: powiadomienia LIVE (wysłano/odebrano), domyślnie włączone; ochrona nowszego lokalnego lastplayed/resume przed regresją; poprawka wyścigu skan biblioteki vs Player.OnStop.
0.4.3-alpha: powiadomienia LIVE tylko po odebraniu/zastosowaniu danych z innego Kodi; brak komunikatu na źródle; przy synchronizacji podczas startu komunikat odbiorczy jest opóźniony o 12 s, by nie zginął wśród komunikatów uruchomieniowych.

0.4.4-alpha: naprawa propagacji zmiany już istniejącej oceny; zdarzenie VideoLibrary.OnUpdate po skanie nie jest już mylone z kwarantanną skanu. Dodatkowo komunikat odbiorczy LIVE jest wymuszany tylko po faktycznym zastosowaniu zmian z innego Kodi.


1.0.0: pierwsze wydanie produkcyjne; logika synchronizacji zgodna z przetestowanym 0.4.4-alpha, bez zmian formatu danych wymagających ponownej inicjalizacji.
