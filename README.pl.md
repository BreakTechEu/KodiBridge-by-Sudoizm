[English](README.md) | **Polski**

---

> [!NOTE]
> **Wydanie produkcyjne (KodiBridge by Sudoizm 2.3.0):**
> KodiBridge by Sudoizm 2.3.0 jest **stabilnym wydaniem produkcyjnym** nowej architektury v2 z transakcyjnym dziennikiem zmian, obsługą identyfikatorów KodiBridge ID dla prywatnych wideo i sieci lokalnej, strażnikiem pojedynczej aktywnej sesji Kodi oraz wymuszaniem zgodności wersji między urządzeniami. Starsza linia **SudoSync 1.0.0** pozostaje dostępna na zamrożonej gałęzi [v1-stable](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/tree/v1-stable).
> Obie wersje są dostępne do pobrania w sekcji **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)**.

# KodiBridge by Sudoizm

Lekka wtyczka usługi działająca w tle dla odtwarzacza Kodi, zapewniająca automatyczną, dwukierunkową synchronizację stanu obejrzenia, punktów wznowienia, ocen użytkownika oraz daty ostatniego odtworzenia pomiędzy wieloma instancjami Kodi w tej samej sieci lokalnej — bez konieczności stawiania zewnętrznego serwera bazy danych MySQL/MariaDB.

Synchronizacja opiera się na wspólnym zasobie sieciowym (SMB, NFS lub zasób lokalny) oraz bezpiecznych wywołaniach oficjalnego API JSON-RPC.

---

## Główne cechy

- **Brak dedykowanej bazy danych:** Wykorzystuje współdzielony zasób sieciowy dostępny dla urządzeń (serwer NAS, lokalny komputer lub udział sieciowy).
- **Bezpieczeństwo i brak destrukcyjnych zmian:** Działa wyłącznie za pośrednictwem oficjalnego API JSON-RPC Kodi. Nigdy nie modyfikuje ani nie blokuje bezpośrednio wewnętrznych plików bazy SQLite (`MyVideos*.db`).
- **Jeden silnik synchronizacji:** Cała synchronizacja działa w jednym silniku (v2: snapshoty, dziennik transakcji, odzyskiwanie). Dawny silnik 1.x nie jest już używany; stare pliki 1.x na NAS pozostają nietknięte.
- **Tylko jedno aktywne Kodi w sieci naraz:** Strażnik aktywnej sesji (`active_kodi_lock.json`) pilnuje, aby w danej chwili w sieci działało tylko jedno urządzenie Kodi z wtyczką KodiBridge, eliminując wyścigi i konflikty zapisu. Przy starcie strażnik sprawdza dzierżawę jako pierwszy (tylko odczyt), zanim cokolwiek zostanie zapisane w folderze wspólnym. Wiek dzierżawy jest liczony według zegara NAS-a, nieczytelna blokada po kilku próbach zatrzymuje start, a dwa Kodi uruchomione w tej samej chwili są wykrywane zaraz po zapisaniu dzierżawy.
- **Niezawodny model rozwiązywania konfliktów:** Każde synchronizowane pole zachowuje własny znacznik czasu oraz wersję sekwencji klienta, zapobiegając wyścigom (*race conditions*) i pętlom zwrotnym.
- **Wybór przy konflikcie zmian:** Gdy to samo pole zmieniono na dwóch Kodi tak, że żadna zmiana nie jest nowsza (np. liczba odtworzeń 1 na jednym i 2 na drugim), KodiBridge pokazuje tabelę: wiersze to pola, które się różnią, kolumny to urządzenia. W każdym wierszu wybierasz właściwą wartość (także z różnych urządzeń), a wybór zapisuje się jako najnowszy, więc trafia na wszystkie Kodi. Tabela działa z myszą, klawiaturą i pilotem. Okno pojawia się po wykryciu konfliktu, a podczas odtwarzania czeka na zatrzymanie filmu. Wybór „Później” odkłada pytanie do następnego uruchomienia Kodi; w każdej chwili można je wywołać przyciskiem „Rozwiąż konflikty” w ustawieniach. Po tym kliknięciu w rogu ekranu widać pasek postępu sprawdzania (przy dużej bibliotece na wolnym NAS może to potrwać kilka minut); znika dopiero przy wyniku: tabeli konfliktów albo komunikacie „Brak konfliktów do rozwiązania”. Do czasu wyboru to pole nie jest synchronizowane, pozostałe działają normalnie.
- **Optymalizacja dla Android TV i przystawek Smart TV:** Każdy cykl synchronizacji buduje lokalny snapshot od nowa i publikuje go tylko wtedy, gdy się zmienił. Obejmuje to także platformy, na których Kodi czasem gubi powiadomienia `OnUpdate` lub `OnStop`.
- **Kwarantanna podczas skanowania i plików NFO:** Nowo zeskanowane elementy biblioteki oraz importy NFO otrzymują starsze wersje wewnętrzne, co zapobiega przypadkowemu nadpisaniu istniejącego, współdzielonego postępu oglądania.
- **KodiBridge ID i częściowy dostęp do biblioteki:** Automatycznie generuje i bezpiecznie wstrzykuje losowe unikalne identyfikatory (UUID) do plików `.nfo` dla wszystkich pozycji wideo w bibliotece jako niezależny, równoległy identyfikator sieci lokalnej (niezależnie od obecności ID z IMDb/TMDb/TVDb), z pominięciem wyłącznie plików posiadających już KodiBridge ID. W plikach `.nfo` KodiBridge ID jest zapisywany jako `<uniqueid type="kodibridge">`. Kolejne numery z wersji do 2.2.11 (`<uniqueid type="sudosync">`) są pomijane, zarówno w plikach `.nfo`, jak i w bazie Kodi. Po zakończeniu skanowania biblioteki wideo usługa w tle sprawdza i przypisuje brakujące identyfikatory z zachowaniem blokady sieciowej NAS, a po nadaniu nowych ID natychmiast publikuje zaktualizowany snapshot v2 na NAS. Gdy żaden plik `.nfo` nie potrzebuje nowego ID, dzieje się to niewidocznie; w przeciwnym razie pokazuje się to samo okno postępu co przy ręcznym nadawaniu (z przyciskiem Anuluj), bo każda zmieniona pozycja jest odświeżana w bibliotece i listy pod oknem by migały. Ręczne nadawanie uruchomione w trakcie innego mówi o tym, zamiast zamykać się bez słowa. Każde Kodi nadaje ID samodzielnie, bez wspólnego licznika, więc urządzenia o różnym zasięgu zasobów nie wchodzą sobie w drogę; pozostałe Kodi odczytują ID ze wspólnych plików `.nfo`.
- **Pokrycie KodiBridge ID w oknie statusu:** Okno statusu pokazuje, ile filmów i odcinków tego Kodi ma KodiBridge ID, oraz wskazuje problemy: pozycje bez ID, zdublowane ID, wspólne identyfikatory zewnętrzne przy braku KodiBridge ID i nieczytelne pliki .nfo. Przy każdym problemie podaje tylko, gdzie szukać: film albo serial i sezon (bez ścieżek i bez porad naprawy). Przykłady ze ścieżkami trafiają do dziennika Kodi. Ten sam plik wpisany do biblioteki Kodi dwa razy pod ścieżkami różniącymi się tylko wielkością liter, danymi logowania w adresie (`smb://użytkownik:hasło@host/...`) albo zapisem `\\host\udział` zamiast `smb://host/udział` jest zgłaszany osobno, a nie jako zdublowane ID. Dwa różne pliki z tym samym KodiBridge ID, które mają też wspólny identyfikator zewnętrzny (IMDb/TMDb/TVDB/Trakt) i nie różnią się żadnym, to kopie jednego wideo (np. na dysku lokalnym i na NAS-ie): nie są zdublowanym ID, naprawa duplikatów ich nie zmienia, a synchronizacja traktuje je jako jedną pozycję, więc obejrzenie dowolnej kopii oznacza obie, na każdym Kodi. Gdy czegoś brakuje, KodiBridge sam pokazuje powiadomienie w rogu ekranu, przy starcie Kodi i po każdej zmianie liczby problemów (nie w trakcie skanowania biblioteki i nadawania ID po nim). Gdy problemów nie ma, KodiBridge się nie odzywa: zmiany odebrane z innych Kodi nie są już ogłaszane.
- **Naprawa zdublowanych KodiBridge ID:** Przycisk „Napraw zdublowane KodiBridge ID” w ustawieniach nadaje nowe KodiBridge ID pozycjom biblioteki, które mają to samo KodiBridge ID (odcinki jednego pliku wieloodcinkowego nie są duplikatami). W każdej grupie jedna pozycja zachowuje swoje ID: ta, którą inne Kodi już synchronizuje pod tym ID (rozpoznana po identyfikatorze IMDb/TMDb/TVDB/Trakt w migawkach na NAS), a jeśli takiej nie ma, pierwsza według ścieżki pliku (liczą się tylko migawki innych Kodi; gdy pod jednym ID są różne pozycje, ID zachowuje ta znana największej liczbie urządzeń, a podsumowanie o tym ostrzega). Nowe ID to losowe UUID. Każdy zmieniony plik `.nfo` dostaje kopię `.repair.bak` (bez nadpisywania starszych kopii), wpis do wycofania jest zapisywany przed zmianą pliku, podsumowanie wymienia wszystkie zmiany, a przycisk „Wycofaj ostatnio przypisane ID” pozwala cofnąć naprawę. Pozostałe Kodi dowiadują się o zmianie z `system/config.json`, ponownie odczytują pliki `.nfo` i zachowują historię synchronizacji pozycji z nowym ID.
- **Dopasowanie pozycji:** Przy włączonej w sieci opcji KodiBridge ID pozycje są dopasowywane między urządzeniami Kodi wyłącznie po KodiBridge ID. Identyfikatory IMDb/TMDb/TVDb/Trakt służą do tego tylko wtedy, gdy opcja jest wyłączona (np. bez prawa zapisu na NAS). Pozycje bez użytecznego identyfikatora są pomijane i nigdy nie są dopasowywane po tytule.
- **Rozdzielenie ID sieciowego od Aliasu UI:** Wyraźnie oddziela wewnętrzny identyfikator sieciowy od przyjaznego aliasu wyświetlanego w interfejsie. Zapewnia to stabilność przypisań przy jednoczesnym czytelnym nazewnictwie urządzeń.
- **Aktualizacje z GitHub Releases i ochrona wersji w sieci:** Wtyczka sprawdza raz na dobę dostępność nowego wydania na GitHub Releases (za jawną zgodą użytkownika), umożliwia ręczną aktualizację ze wskazanego pliku ZIP i pilnuje w `SudoSync.cfg` wymaganej minimalnej wersji (`min_addon_version`). Nowsza wersja zapisana przez inne Kodi od razu wywołuje pytanie o aktualizację.
- **Dziennik Zdarzeń (Journaling):** Synchronizacja używa mechanizmu transakcyjnego z dziennikiem postępu, pozwalając na bezpieczne wznowienie po nieoczekiwanym błędzie. Jeśli pozycja zmieniła się w Kodi po przerwanym zapisie, odzyskiwanie zostawia aktualną wartość z Kodi, pomija tę pozycję bez zapisu i działa dalej, więc przerwany zapis nigdy nie blokuje synchronizacji na stałe. Operację może dokończyć albo wycofać tylko Kodi, które ją rozpoczęło. Jeśli inne Kodi zostało nieprawidłowo zamknięte w jej trakcie (np. Kodi zamknięte przez Androida), jego niedokończona operacja wstrzymuje synchronizację na pozostałych urządzeniach: KodiBridge pokazuje wtedy okno z nazwą tamtego urządzenia i czasem rozpoczęcia operacji oraz prośbą, żeby uruchomić na nim Kodi na około 2 minuty i zamknąć je z menu Kodi. Okno pojawia się raz na uruchomienie Kodi, nie w trakcie odtwarzania. Inne Kodi nigdy nie przejmuje cudzej operacji.
- **Limity Bezpieczeństwa:** System wprowadza maksymalne limity pamięci, wielkości plików oraz liczby rekordów dla bezpieczeństwa klienta.

---

## Synchronizowane pola

- Status obejrzenia i licznik odtworzeń (*watched / unwatched*),
- Dokładny punkt wznowienia odtwarzania (*resume position*),
- Data ostatniego odtworzenia (*last played*),
- Oceny użytkownika (1–10).

---

## Instalacja i konfiguracja

1. **Źródło instalacji:**
   - **KodiBridge by Sudoizm 2.3.0 (Produkcyjna):** Pobierz plik `kodibridge-by-sudoizm-2.3.0.zip` z sekcji **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)** (zalecane wydanie produkcyjne z architekturą v2 i KodiBridge ID).
   - **SudoSync 1.0.0 (Starsza wersja stabilna):** Pobierz plik `service.sudosync-1.0.0.zip` z sekcji **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)**.

2. **Instalacja w Kodi:**
   - W Kodi przejdź do: *Ustawienia -> Dodatki -> Zainstaluj z pliku zip*.
   - Wskaż przygotowane archiwum zip.

3. **Konfiguracja ścieżki sieciowej i identyfikatorów:**
   - Przy pierwszym uruchomieniu kreator KodiBridge poprosi o wskazanie wspólnego katalogu sieciowego (np. `smb://192.168.1.100/Wspoldzielony/`) i automatycznie utworzy w nim folder `.KodiBridge/` oraz zainicjuje urządzenie. Wersja 2.2.12 przenosi istniejący folder `.SudoSync/` na nowy folder `.KodiBridge/` obok niego: ustawienia sieciowe zostają przeniesione, stan synchronizacji zaczyna się od nowa (stan obejrzenia zostaje w bibliotece każdego Kodi), a starsze wersje dodatku dostają prośbę o aktualizację.
   - W ustawieniach dodatku możesz nadać swojemu urządzeniu przyjazną nazwę.

---

## Aktualizacja wtyczki

> [!IMPORTANT]
> **Mechanizm bezpiecznej aktualizacji:**
> Wtyczka KodiBridge posiada wbudowany system aktualizacji chroniący spójność sieci:
> 1. **Automatyczne sprawdzanie w GitHub Releases (raz na dobę):** Wtyczka KodiBridge sprawdza dostępność nowego wydania w oficjalnym repozytorium GitHub (`BreakTechEu/KodiBridge-by-Sudoizm`) raz na dobę kalendarzową: przy pierwszym uruchomieniu Kodi danego dnia (albo tuż po północy, jeśli Kodi działa dalej). Nie wykonuje cichych aktualizacji w tle — po wykryciu nowej wersji wyświetla pytanie o zgodę na pobranie i instalację (można też wywołać sprawdzenie ręcznie w ustawieniach wtyczki).
> 2. **Aktualizacja z lokalnego pliku ZIP:** W ustawieniach wtyczki znajduje się opcja ręcznego wskazania i zainstalowania paczki `kodibridge-by-sudoizm-X.Y.Z.zip` z dysku lokalnego lub udziału sieciowego. Historyczne wydania zachowują dotychczasowe nazwy; wbudowany aktualizator rozpoznaje zarówno nową nazwę KodiBridge, jak i starszy format `service.sudosync-X.Y.Z.zip`.
> 3. **Strażnik zgodności wersji w sieci (`min_addon_version` w `SudoSync.cfg`):** Gdy którekolwiek urządzenie Kodi w sieci zostanie zaktualizowane do nowszej wersji, zapisuje wymaganą minimalną wersję we wspólnym pliku `SudoSync.cfg`. Inne Kodi ze starszą wersją wykrywa to przy starcie lub w trakcie pracy (w ciągu około minuty; podczas odtwarzania pytanie czeka na zatrzymanie filmu) i od razu proponuje aktualizację z GitHub Releases albo z pliku ZIP, bez czekania na dobowe sprawdzenie. Po odmowie KodiBridge nie synchronizuje tego Kodi i pyta ponownie po następnym uruchomieniu Kodi.

---

## Kompatybilność

> **Ważne:** Wtyczka oficjalnie wspiera wyłącznie dwie najnowsze **stabilne** główne wersje Kodi (obecnie v20 Nexus oraz v21 Omega). Wersje przedpremierowe / testowe nadchodzących wydań (np. Kodi v22 Piers RC) nie są jeszcze oficjalnie zweryfikowane. Starsze wersje przestają być wspierane.

- Kodi v20 Nexus oraz v21 Omega.
- Wieloplatformowość: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## Licencja

Projekt typu open-source udostępniany na licencji [GPL-3.0-or-later](LICENSE).