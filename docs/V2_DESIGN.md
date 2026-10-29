# SudoSync 2.x — rozwój architektury dodatku

KodiBridge by Sudoizm 2.x (dawniej SudoSync) jest kolejną wersją tego samego dodatku, który zaczynał jako SudoSync 1.x. Funkcje sprawdzone w 1.x (m.in. model LIVE z wersjami pól i catch-up po długiej przerwie) zostały przeniesione do silnika v2. Od wydania 2.2.0 v2 jest jedynym silnikiem synchronizacji; kod silnika 1.x usunięto. Dokument opisuje architekturę v2.

## Nienaruszalne granice

- `master` jest aktywną gałęzią główną, na której rozwija się wersja 2.x. `v1-stable` pozostaje zamrożoną gałęzią archiwalnego wydania 1.0.0.
- Gałąź `v1-stable` i wydanie SudoSync 1.0.0 są zamrożone. Nie cherry-pickować do niej zmian, nie zmieniać tagów ani opublikowanych plików.
- Jeden silnik synchronizacji (v2). Nie przywracać równoległego działania silnika 1.x. Zachowania wymagane przez użytkownika (model LIVE, catch-up, ochrona przed pętlą zmian) realizuje v2.
- SudoSync pozostaje darmowym oprogramowaniem open source.
- **Model pracy:** użytkownik korzysta tylko z jednego Kodi naraz. Jest to podstawowa zasada operacyjna v2; blokady i odzyskiwanie są dodatkowymi zabezpieczeniami. Nie wymagaj fizycznego testu konkurencji dwóch aktywnych urządzeń jako warunku pracy w tym modelu. Ponieważ SudoSync działa jako usługa w tle, nie uruchamiaj usługi na pozostałych urządzeniach w tym samym czasie.
- Bez MySQL/MariaDB, bez serwera centralnego i bez chmury. Wspólnym transportem danych jest katalog dostępny dla wszystkich urządzeń Kodi: SMB, NFS lub lokalny udział.
- Współdzielony katalog nie może zawierać danych, które należą wyłącznie do jednej instancji Kodi.
- Zmiany biblioteki Kodi wyłącznie przez oficjalne JSON-RPC. Nie otwierać ani nie modyfikować plików `MyVideos*.db`.
- Aktualizacje są pobierane z oficjalnego GitHub Releases (`BreakTechEu/KodiBridge-by-Sudoizm`, sprawdzane najwyżej raz na dobę, a od razu, gdy inne Kodi zapisało nowszą wersję w `SudoSync.cfg`; instalowane wyłącznie za jawną zgodą użytkownika) lub instalowane ręcznie ze wskazanego przez użytkownika pliku `.zip`. Na NAS nie przechowuje się paczek ZIP.
- W pliku `SudoSync.cfg` utrzymywana jest minimalna wymagana wersja wtyczki w sieci (`min_addon_version`). Urządzenie ze starszą wersją wtyczki jest zatrzymywane na etapie bootstrapu (przed jakąkolwiek synchronizacją) lub odświeżenia dzierżawy (podczas odtwarzania po zatrzymaniu filmu) i nie synchronizuje do czasu aktualizacji lub następnego uruchomienia Kodi.
- Wspierane są dwie najnowsze główne wersje Kodi. Zmianę zakresu wsparcia dokumentować w każdym wydaniu.

## Cele v2

1. Konserwatywna synchronizacja stanu odtwarzania pomiędzy instancjami Kodi.
2. Bezpieczna inicjalizacja nowej sieci i jawny podgląd przed pierwszym zapisem.
3. Rozstrzyganie konfliktów deterministycznie, bez cichego nadpisywania niejednoznacznych danych.
4. Identyfikacja pozycji przez KodiBridge ID (dawniej SudoSync ID, w `.nfo` jako `uniqueid type="sudosync"`): przy włączonej opcji jest to jedyny klucz dopasowania między urządzeniami; IMDb/TMDb/TVDb/Trakt tylko przy wyłączonej opcji. Nigdy dopasowanie po tytule.
5. Odporny na przerwanie zapis plików współdzielonych i jawna obsługa błędów I/O.
6. Weryfikowalny, testowalny mechanizm aktualizacji oraz regresyjne testy bezpieczeństwa.
7. Czytelne komunikaty PL/EN i przewidywalne logowanie bez ujawniania sekretów.

## Proponowane warstwy

- **Adapter Kodi** — cienka warstwa dla JSON-RPC, ustawień, powiadomień, ścieżek VFS i informacji o wersji Kodi. Kod logiki synchronizacji nie powinien zależeć bezpośrednio od modułów Kodi.
- **Domena** — czyste funkcje do normalizacji identyfikatorów, walidacji rekordów, budowania planów i rozstrzygania konfliktów. Bez I/O i bez skutków ubocznych.
- **Repozytorium współdzielone** — schematy JSON wersjonowane, walidacja danych wejściowych, zapis przez plik tymczasowy i bezpieczną podmianę, kopie odzyskiwania oraz jawna obsługa równoczesnego dostępu.
- **Silnik synchronizacji** — osobne fazy: odkrywanie klientów, snapshot, analiza, podgląd, zatwierdzenie, zapis przez JSON-RPC, weryfikacja wyniku i dziennik operacji.
- **Aktualizator** — sprawdzanie wydań GitHub Releases (raz na dobę kalendarzową, przy pierwszym uruchomieniu Kodi danego dnia, za zgodą użytkownika), ręczna instalacja z pliku ZIP, pełna walidacja struktury paczki i manifestu `addon.xml`, atomowa podmiana katalogu z kopią zapasową oraz egzekwowanie `min_addon_version` w `SudoSync.cfg`.
- **Interfejs** — ustawienia i operacje administracyjne; każda operacja mogąca zmienić stan biblioteki musi wyświetlać zakres zmian i wymagać jawnego potwierdzenia.

## Model synchronizacji i bezpieczeństwo danych

- Klient otrzymuje stabilny, lokalnie przechowywany identyfikator. Nie generować nowego ID przy każdym uruchomieniu ani nie przechowywać go w pliku współdzielonym jako ustawienia pojedynczego Kodi.
- Najpierw łączyć rekordy po jednoznacznych identyfikatorach zewnętrznych (np. IMDb/TMDb/TVDb/Trakt), z uwzględnieniem typu medium. Nie używać lokalnych `movieid`/`episodeid` Kodi jako globalnych identyfikatorów.
- Jeśli tożsamość jest niejednoznaczna, rekord pomijać i raportować. Tytuł, rok, sezon/odcinek i ścieżka mogą pomagać w wykrywaniu kandydatów, ale same nie uprawniają do automatycznego scalania.
- SudoSync ID dla prywatnych materiałów musi być stabilne i jawnie zapisane w metadanych, z kontrolą duplikatów oraz możliwością podglądu i wycofania zmian.
- Wersjonować każde synchronizowane pole niezależnie. Reguły porównania muszą być deterministyczne i testować konflikt równoczesnych zapisów oraz różnice zegarów. Pierwszy snapshot klienta w katalogu v2 otrzymuje dla wszystkich pól wersje zerowe (`lc=0, seq=0`), aby nowo podłączone urządzenie nie nadpisywało historii peerów.
- Konflikt (najnowsza wersja pola jednakowa u kilku klientów, różne wartości) nie jest rozstrzygany automatycznie: pole jest pomijane w planie, a plan zawiera opis konfliktu (urządzenia, wartości, lokalny Kodi ID). Usługa pyta użytkownika, gdy nic nie jest odtwarzane; wybrana wartość jest zapisywana w Kodi przez zwykły mechanizm transakcji (gdy różni się od lokalnej) i publikowana z wersją `lc+1, seq+1` względem konfliktu, więc wygrywa na wszystkich urządzeniach. Odłożenie decyzji obowiązuje do ponownego uruchomienia Kodi lub do wywołania akcji „Rozwiąż konflikty”.
- W czasie odtwarzania nie zapisywać zdalnych zmian do biblioteki. Zmiany otrzymane w tym czasie odłożyć. Lokalne zmiany publikować także w trakcie odtwarzania: zmianę statusu obejrzenia lub oceny (powiadomienie `VideoLibrary.OnUpdate`) od razu, a postęp odtwarzania co `live_poll_seconds`.
- Operacje inicjalizacji muszą mieć plan tylko do odczytu, backup przed zapisem, wyraźne potwierdzenie i dziennik pozwalający wznowić lub bezpiecznie przerwać proces.
- Nie traktować uszkodzonego lub nieczytelnego pliku konfiguracyjnego jak braku konfiguracji. Zachować oryginał, zgłosić błąd i przerwać operację, która mogłaby nadpisać dane.

## Wymagania dla aktualizatora i strażnika wersji

- Sprawdzanie aktualizacji na GitHub Releases odbywa się raz na dobę kalendarzową, przy pierwszym uruchomieniu Kodi danego dnia (lub na żądanie w ustawieniach) i wymaga jawnej zgody użytkownika przed pobraniem i instalacją.
- Ręczna instalacja z lokalnego lub sieciowego pliku ZIP jest dostępna z poziomu ustawień wtyczki.
- Przed rozpakowaniem weryfikować całe archiwum ZIP i jego zawartość (`_validate_addon_zip_package`): odrzucać uszkodzone archiwa, obcy identyfikator dodatku, niezgodną wersję w `addon.xml`, ścieżki absolutne, `..` oraz pliki spoza prefiksu `service.sudosync/`.
- Rozpakowywać paczkę do katalogu tymczasowego obok dodatku i podmieniać katalog dodatku z zachowaniem kopii zapasowej poprzedniej wersji na wypadek błędu (`_extract_and_swap_addon`).
- Po udanej instalacji podbijać `min_addon_version` w `SudoSync.cfg`. Na starcie usługi oraz podczas odświeżania dzierżawy sesji (`refresh_network_active_kodi_lease`) egzekwować, by żadne Kodi w sieci nie działało ze starszą wersją wtyczki niż `min_addon_version`.

## Ochrona przed podwójnym uruchomieniem usługi lokalnie

- Na jednym profilu Kodi usługa przed bootstrapem, aktualizacją, odczytem biblioteki lub synchronizacją zdobywa lokalną blokadę systemową na pliku w katalogu profilu dodatku.
- Blokada jest utrzymywana przez cały czas życia procesu. System operacyjny zwalnia ją po zamknięciu lub awarii procesu; sam plik blokady pozostaje i nie jest traktowany jako dowód aktywności.
- Druga kopia usługi na tym samym profilu kończy start bez wykonywania pracy. Jeśli mechanizm blokady nie jest dostępny lub nie można go bezpiecznie ustanowić, usługa nie rozpoczyna pracy.
- Lokalna blokada nie zastępuje wspólnego znacznika sesji ani dziennika odzyskiwania dla wielu urządzeń Kodi.

## Wyłączność operacji synchronizacji

- Wyłączność dotyczy mutującego cyklu synchronizacji, nie całego czasu życia dodatku Kodi.
- Przy uzgodnionej pracy jednym Kodi naraz wystarcza blokada procesowa usługi oraz pomocnicza dzierżawa aktywnego Kodi na udziale NAS (`active_kodi_lock.json`, odświeżana w pętli usługi). Fizyczna blokada między hostami i jej test są wymagane dopiero wtedy, gdy projekt zacznie dopuszczać jednoczesną pracę wielu usług Kodi.
- Po zdobyciu blokady klient tworzy znacznik zawierający session ID, client ID i czas rozpoczęcia; kolejna instancja odmawia rozpoczęcia operacji, dopóki znacznik istnieje.
- Znacznik jest usuwany wyłącznie przez właściciela sesji po prawidłowym zakończeniu. Błąd odczytu, zapisu lub usunięcia oznacza odmowę dalszej pracy.
- Znacznik pozostawiony po awarii nie może być automatycznie uznawany za osierocony na podstawie wieku. Przy starcie każda wtyczka najpierw odczytuje właściciela: urządzenie zapisane jako właściciel automatycznie podejmuje odzyskiwanie z dziennika, a inne urządzenia pozostają zablokowane. Nie ma automatycznego przejęcia sesji przez inne Kodi.
- Protokół znacznika nie jest sam w sobie blokadą. W trybie dopuszczającym jednoczesne uruchomienie klientów blokada musi zostać zweryfikowana na docelowych udziałach SMB/NFS oraz systemach operacyjnych. Tryb sekwencyjny opiera się na zasadzie pracy jednym Kodi naraz i nie deklaruje ochrony między hostami.

## Testowanie i bramki jakości

- Testy domeny uruchamiane bez Kodi i bez sieci.
- Testy adapterów z mockami dla JSON-RPC/VFS oraz testy awarii i częściowego zapisu.
- Testy integracyjne scenariuszy wielu klientów, restartu podczas zapisu, duplikatów mediów i konfliktów wersji.
- CI: kompilacja/składnia, testy jednostkowe, kontrola formatowania/kodowania, analiza bezpieczeństwa oraz test pakowania dodatku.
- Żadne wydanie nie jest gotowe, dopóki wszystkie wymagane testy nie przejdą i paczka ZIP nie zostanie zbudowana oraz zweryfikowana od zera.
- Utrzymywać testy regresyjne dla każdej naprawionej usterki. Nie oznaczać testu jako poprawnego, jeśli sprawdza wyłącznie odrzucenie złych danych, a nie poprawny przypadek.

## Etapy wdrożenia

1. **Specyfikacja i schematy** — zatwierdzić formaty danych, model konfliktów, wymagania update i politykę kompatybilności.
2. **Rdzeń bez Kodi** — walidacja schematów, identyfikacja mediów, wersjonowanie i czyste funkcje planowania; testy deterministyczne.
3. **Warstwa współdzielonych plików** — zapis/odczyt odporny na przerwanie, backup, blokady/rozwiązywanie konfliktów współbieżności.
4. **Adapter Kodi i podgląd** — JSON-RPC, bezpieczna inicjalizacja, backup i jawne zatwierdzanie.
5. **Synchronizacja LIVE** — wykrywanie zmian, opóźnianie podczas odtwarzania, idempotencja i ochrona przed pętlami.
6. **SudoSync ID** — generowanie, walidacja, podgląd, backup i rollback.
7. **Aktualizator i strażnik wersji** — sprawdzanie GitHub Releases, walidacja ZIP i egzekwowanie `min_addon_version`.
8. **Dokumentacja, CI i wydanie beta** — dopiero po przejściu wszystkich bramek i osobnej zgodzie właściciela na publikację.

## Zasada migracji

Nie zakładać, że istniejące pliki współdzielone można nadpisać nowym schematem. Każda migracja musi być jawna, odwracalna i przetestowana na kopii. W razie niepewności v2 ma odmówić zapisu, zachowując dane źródłowe.

## Stan implementacji v2

Stan aktualnego kodu w repozytorium (wydanie Beta — wymaga odróżnienia testów jednostkowych od testów funkcjonalnych na rzeczywistym Kodi):

- Zaimplementowano walidację snapshotów, normalizację silnych identyfikatorów, wersjonowanie pól, budowanie snapshotu z wyników JSON-RPC oraz czysty planer dry-run.
- Planer grupuje rekordy tylko po typowanych silnych identyfikatorach. Niejednoznaczne grupy i konflikty pól są raportowane, nie nadpisywane. Rekordy zdalne nie są dodawane do lokalnej biblioteki.
- Zaimplementowano osobny planer inicjalizacji z wybranego urządzenia bazowego. Kopiuje wartości do pustych pól, zachowuje wyższy licznik odtworzeń celu i raportuje konflikty niepustych resume/lastplayed/userrating.
- Zaimplementowano testowalny adapter JSON-RPC, adapter plikowy xbmcvfs, katalog współdzielonych snapshotów oraz abstrakcję repozytorium z kopią odzyskiwania.
- Dodano runner transakcji z backupem przed zapisem, trwałym dziennikiem zamiaru, weryfikacją po każdej zmianie i pozostawieniem blokady przy niepewnym wyniku. Dodano też repozytorium backupów i dzienników z walidacją, kontrolą odczytu zwrotnego i odmową traktowania brakującego dziennika jako pustej operacji. Nie wykonuje automatycznego rollbacku.
- Dodano protokół znacznika aktywnej sesji, rozpoznawanie właściciela po restarcie oraz runner odzyskiwania, który weryfikuje rzeczywisty stan Kodi, bezpiecznie wznawia nieukończone pozycje. Pozycja, której stan w Kodi zmienił się od przerwania (albo pod tym Kodi ID jest już inna pozycja), jest pomijana bez zapisu i oznaczana w dzienniku jako `superseded`; aktualna wartość w Kodi jest uznawana za nowszą. Blokada zostaje tylko przy błędzie zapisu lub odczytu, a odzyskiwanie ponawia się w kolejnym cyklu. `service.py` wywołuje go przy starcie przed podglądem v2. Sam znacznik nie stanowi blokady.
- `service.py` używa wyłącznie `v2_runtime`: na starcie wykonuje odzyskiwanie (`v2_runtime.recover()`), a w cyklu synchronizacji wywołuje `v2_runtime.sync_cycle()` (w `_execute_integrated_live_cycle`) oraz publikację snapshotu (`v2_runtime.publish_local_snapshot()`). Silnik v1 został usunięty. Dodatkowo w `sudosync_bootstrap.py` i `service.py` działa dzierżawa pojedynczego aktywnego Kodi (`active_kodi_lock.json`) oraz egzekwowanie `min_addon_version`.
- Powyższe moduły oraz ich integracja z `service.py` są przetestowane w testach jednostkowych z użyciem atrap (mocków) API Kodi. Weryfikacja funkcjonalna na rzeczywistych urządzeniach Kodi jest wykonywana osobno przez Agenta Antigravity i właściciela projektu.

