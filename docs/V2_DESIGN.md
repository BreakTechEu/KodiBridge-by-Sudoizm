# SudoSync v2 — projekt docelowej architektury

Status: projekt techniczny; nie jest jeszcze implementacją ani obietnicą zgodności z wydaniem produkcyjnym.

## Nienaruszalne granice

- Gałąź `v1-stable` i wydanie SudoSync 1.0.0 są zamrożone. Nie cherry-pickować do niej zmian, nie zmieniać tagów ani opublikowanych plików.
- Prace v2 prowadzić na osobnej gałęzi i przez Pull Request. Nie scalać i nie publikować bez osobnej zgody właściciela projektu.
- SudoSync pozostaje darmowym oprogramowaniem open source.
- Bez MySQL/MariaDB, bez serwera centralnego i bez chmury. Wspólnym transportem danych jest katalog dostępny dla wszystkich urządzeń Kodi: SMB, NFS lub lokalny udział.
- Współdzielony katalog nie może zawierać danych, które należą wyłącznie do jednej instancji Kodi.
- Zmiany biblioteki Kodi wyłącznie przez oficjalne JSON-RPC. Nie otwierać ani nie modyfikować plików `MyVideos*.db`.
- Aktualizacje są dostarczane jako ZIP umieszczony przez administratora w katalogu współdzielonym. Wtyczka sama wykrywa i weryfikuje pakiet; nie dodawać do jej UI funkcji przesyłania/wgrywania ZIP-a.
- Aktualizacje nie mogą być opcjonalnie wyłączane przez ustawienie użytkownika. Brak poprawnej, zaufanej aktualizacji nie może jednak oznaczać instalacji niezweryfikowanego kodu: w razie błędu instalacja zostaje przerwana i szczegółowo zalogowana.
- Wspierane są dwie najnowsze główne wersje Kodi. Zmianę zakresu wsparcia dokumentować w każdym wydaniu.

## Cele v2

1. Konserwatywna synchronizacja stanu odtwarzania pomiędzy instancjami Kodi.
2. Bezpieczna inicjalizacja nowej sieci i jawny podgląd przed pierwszym zapisem.
3. Rozstrzyganie konfliktów deterministycznie, bez cichego nadpisywania niejednoznacznych danych.
4. Obsługa filmów/odcinków bez stabilnych identyfikatorów zewnętrznych przez opcjonalne SudoSync ID, bez utożsamiania elementów wyłącznie na podstawie podobnych tytułów.
5. Odporny na przerwanie zapis plików współdzielonych i jawna obsługa błędów I/O.
6. Weryfikowalny, testowalny mechanizm aktualizacji oraz regresyjne testy bezpieczeństwa.
7. Czytelne komunikaty PL/EN i przewidywalne logowanie bez ujawniania sekretów.

## Proponowane warstwy

- **Adapter Kodi** — cienka warstwa dla JSON-RPC, ustawień, powiadomień, ścieżek VFS i informacji o wersji Kodi. Kod logiki synchronizacji nie powinien zależeć bezpośrednio od modułów Kodi.
- **Domena** — czyste funkcje do normalizacji identyfikatorów, walidacji rekordów, budowania planów i rozstrzygania konfliktów. Bez I/O i bez skutków ubocznych.
- **Repozytorium współdzielone** — schematy JSON wersjonowane, walidacja danych wejściowych, zapis przez plik tymczasowy i bezpieczną podmianę, kopie odzyskiwania oraz jawna obsługa równoczesnego dostępu.
- **Silnik synchronizacji** — osobne fazy: odkrywanie klientów, snapshot, analiza, podgląd, zatwierdzenie, zapis przez JSON-RPC, weryfikacja wyniku i dziennik operacji.
- **Aktualizator** — osobny moduł z walidacją manifestu, wersji, podpisu, zawartości ZIP i zgodności dodatku; nie mieszać go z logiką biblioteki.
- **Interfejs** — ustawienia i operacje administracyjne; każda operacja mogąca zmienić stan biblioteki musi wyświetlać zakres zmian i wymagać jawnego potwierdzenia.

## Model synchronizacji i bezpieczeństwo danych

- Klient otrzymuje stabilny, lokalnie przechowywany identyfikator. Nie generować nowego ID przy każdym uruchomieniu ani nie przechowywać go w pliku współdzielonym jako ustawienia pojedynczego Kodi.
- Najpierw łączyć rekordy po jednoznacznych identyfikatorach zewnętrznych (np. IMDb/TMDb/TVDb/Trakt), z uwzględnieniem typu medium. Nie używać lokalnych `movieid`/`episodeid` Kodi jako globalnych identyfikatorów.
- Jeśli tożsamość jest niejednoznaczna, rekord pomijać i raportować. Tytuł, rok, sezon/odcinek i ścieżka mogą pomagać w wykrywaniu kandydatów, ale same nie uprawniają do automatycznego scalania.
- SudoSync ID dla prywatnych materiałów musi być stabilne i jawnie zapisane w metadanych, z kontrolą duplikatów oraz możliwością podglądu i wycofania zmian.
- Wersjonować każde synchronizowane pole niezależnie. Reguły porównania muszą być deterministyczne i testować konflikt równoczesnych zapisów oraz różnice zegarów.
- W czasie odtwarzania nie zapisywać zdalnych zmian do biblioteki. Zmiany otrzymane w tym czasie odłożyć.
- Operacje inicjalizacji muszą mieć plan tylko do odczytu, backup przed zapisem, wyraźne potwierdzenie i dziennik pozwalający wznowić lub bezpiecznie przerwać proces.
- Nie traktować uszkodzonego lub nieczytelnego pliku konfiguracyjnego jak braku konfiguracji. Zachować oryginał, zgłosić błąd i przerwać operację, która mogłaby nadpisać dane.

## Wymagania dla auto-update

- Użyć standardowego, sprawdzonego w Pythonie mechanizmu RSA PKCS#1 v1.5 z SHA-256 albo innej dobrze zdefiniowanej biblioteki kryptograficznej, jeśli jest dostępna we wspieranych instalacjach Kodi. Nie implementować własnego parsera kryptograficznego, jeśli da się tego uniknąć.
- Podpis musi obejmować dokładnie określone bajty manifestu i identyfikować wersję, identyfikator dodatku, minimalną wersję Kodi oraz skrót SHA-256 ZIP-a. Nie ufać nazwie pliku ani polu wersji bez weryfikacji podpisu.
- Klucz prywatny nigdy nie może znaleźć się w repozytorium ani w ZIP-ie dodatku. Klucz publiczny jest przypięty w kliencie.
- Odrzucać błędny podpis, błędny/niezgodny manifest, obcy identyfikator dodatku, nieobsługiwaną wersję, downgrade bez jawnej procedury odzyskiwania, nieoczekiwane pliki, zduplikowane ścieżki ZIP, ścieżki absolutne, `..`, symlinki, nadmierną liczbę plików i nadmierny rozmiar rozpakowanej zawartości.
- Najpierw weryfikować całe archiwum i jego zawartość, dopiero potem rozpoczynać instalację. Nie rozpakowywać niezaufanych ścieżek bezpiecznie ograniczonych do katalogu dodatku.
- Po instalacji weryfikować wersję i stan; jeśli Kodi wymaga restartu, jasno to komunikować. Nie usuwać ostatniej działającej kopii, dopóki nowa instalacja nie przejdzie walidacji.
- Testy muszą obejmować poprawny podpis, zmieniony ZIP, zmieniony manifest, zły klucz, obcięty podpis, błędne kodowanie, złośliwe ścieżki, duplikaty, limity rozmiaru, downgrade i błędy I/O.

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
7. **Aktualizator** — manifest podpisany kryptograficznie, walidacja ZIP i pełne testy negatywne/pozytywne.
8. **Dokumentacja, CI i wydanie beta** — dopiero po przejściu wszystkich bramek i osobnej zgodzie właściciela na publikację.

## Zasada migracji

Nie zakładać, że istniejące pliki współdzielone można nadpisać nowym schematem. Każda migracja musi być jawna, odwracalna i przetestowana na kopii. W razie niepewności v2 ma odmówić zapisu, zachowując dane źródłowe.
