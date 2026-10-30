[English](README.md) | **Polski**

---

> [!NOTE]
> **Wydanie produkcyjne (KodiBridge by Sudoizm 2.2.5):**
> KodiBridge by Sudoizm 2.2.5 jest **stabilnym wydaniem produkcyjnym** nowej architektury v2 z transakcyjnym dziennikiem zmian, obsługą identyfikatorów KodiBridge ID dla prywatnych wideo i sieci lokalnej, strażnikiem pojedynczej aktywnej sesji Kodi oraz wymuszaniem zgodności wersji między urządzeniami. Starsza linia **SudoSync 1.0.0** pozostaje dostępna na zamrożonej gałęzi [v1-stable](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/tree/v1-stable).
> Obie wersje są dostępne do pobrania w sekcji **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)**.

# KodiBridge by Sudoizm

Lekka wtyczka usługi działająca w tle dla odtwarzacza Kodi, zapewniająca automatyczną, dwukierunkową synchronizację stanu obejrzenia, punktów wznowienia, ocen użytkownika oraz daty ostatniego odtworzenia pomiędzy wieloma instancjami Kodi w tej samej sieci lokalnej — bez konieczności stawiania zewnętrznego serwera bazy danych MySQL/MariaDB.

Synchronizacja opiera się na wspólnym zasobie sieciowym (SMB, NFS lub zasób lokalny) oraz bezpiecznych wywołaniach oficjalnego API JSON-RPC.

---

## Główne cechy

- **Brak dedykowanej bazy danych:** Wykorzystuje współdzielony zasób sieciowy dostępny dla urządzeń (serwer NAS, lokalny komputer lub udział sieciowy).
- **Bezpieczeństwo i brak destrukcyjnych zmian:** Działa wyłącznie za pośrednictwem oficjalnego API JSON-RPC Kodi. Nigdy nie modyfikuje ani nie blokuje bezpośrednio wewnętrznych plików bazy SQLite (`MyVideos*.db`).
- **Jeden silnik synchronizacji:** Cała synchronizacja działa w jednym silniku (v2: snapshoty, dziennik transakcji, odzyskiwanie). Dawny silnik 1.x nie jest już używany; stare pliki 1.x na NAS pozostają nietknięte.
- **Tylko jedno aktywne Kodi w sieci naraz:** Strażnik aktywnej sesji (`active_kodi_lock.json`) pilnuje, aby w danej chwili w sieci działało tylko jedno urządzenie Kodi z wtyczką KodiBridge, eliminując wyścigi i konflikty zapisu.
- **Niezawodny model rozwiązywania konfliktów:** Każde synchronizowane pole zachowuje własny znacznik czasu oraz wersję sekwencji klienta, zapobiegając wyścigom (*race conditions*) i pętlom zwrotnym.
- **Wybór przy konflikcie zmian:** Gdy to samo pole zmieniono na dwóch Kodi tak, że żadna zmiana nie jest nowsza (np. liczba odtworzeń 1 na jednym i 2 na drugim), KodiBridge pokazuje tabelę: wiersze to pola, które się różnią, kolumny to urządzenia. W każdym wierszu wybierasz właściwą wartość (także z różnych urządzeń), a wybór zapisuje się jako najnowszy, więc trafia na wszystkie Kodi. Tabela działa z myszą, klawiaturą i pilotem. Okno pojawia się po wykryciu konfliktu, a podczas odtwarzania czeka na zatrzymanie filmu. Wybór „Później” odkłada pytanie do następnego uruchomienia Kodi; w każdej chwili można je wywołać przyciskiem „Rozwiąż konflikty” w ustawieniach. Do czasu wyboru to pole nie jest synchronizowane, pozostałe działają normalnie.
- **Optymalizacja dla Android TV i przystawek Smart TV:** Każdy cykl synchronizacji buduje lokalny snapshot od nowa i publikuje go tylko wtedy, gdy się zmienił. Obejmuje to także platformy, na których Kodi czasem gubi powiadomienia `OnUpdate` lub `OnStop`.
- **Kwarantanna podczas skanowania i plików NFO:** Nowo zeskanowane elementy biblioteki oraz importy NFO otrzymują starsze wersje wewnętrzne, co zapobiega przypadkowemu nadpisaniu istniejącego, współdzielonego postępu oglądania.
- **KodiBridge ID i częściowy dostęp do biblioteki:** Automatycznie generuje i bezpiecznie wstrzykuje kolejne unikalne identyfikatory (`{prefix}-{num:06d}`) do plików `.nfo` dla wszystkich pozycji wideo w bibliotece jako niezależny, równoległy identyfikator sieci lokalnej (niezależnie od obecności ID z IMDb/TMDb/TVDb), z pominięciem wyłącznie plików posiadających już KodiBridge ID. W plikach `.nfo` KodiBridge ID jest zapisywany jako `<uniqueid type="sudosync">` (techniczny znacznik bez zmian, zgodny z istniejącymi bibliotekami). Po zakończeniu skanowania biblioteki wideo usługa w tle całkowicie niewidocznie (tryb silent/headless) sprawdza i przypisuje brakujące identyfikatory z zachowaniem blokady sieciowej NAS, a po nadaniu nowych ID natychmiast publikuje zaktualizowany snapshot v2 na NAS. Współdzielony licznik (`sudosync_id_last_number` w `SudoSync.cfg` i `system/config.json`) gwarantuje brak kolizji numerów w całej sieci domowej i płynną współpracę urządzeń o różnym zasięgu zasobów.
- **Pokrycie KodiBridge ID w oknie statusu:** Okno statusu pokazuje, ile filmów i odcinków tego Kodi ma KodiBridge ID, oraz wskazuje problemy: pozycje bez ID, zdublowane ID, wspólne identyfikatory zewnętrzne przy braku KodiBridge ID i nieczytelne pliki .nfo. Przykłady konkretnych pozycji trafiają do dziennika Kodi.
- **Dopasowanie pozycji:** Przy włączonej w sieci opcji KodiBridge ID pozycje są dopasowywane między urządzeniami Kodi wyłącznie po KodiBridge ID. Identyfikatory IMDb/TMDb/TVDb/Trakt służą do tego tylko wtedy, gdy opcja jest wyłączona (np. bez prawa zapisu na NAS). Pozycje bez użytecznego identyfikatora są pomijane i nigdy nie są dopasowywane po tytule.
- **Rozdzielenie ID sieciowego od Aliasu UI:** Wyraźnie oddziela wewnętrzny identyfikator sieciowy od przyjaznego aliasu wyświetlanego w interfejsie. Zapewnia to stabilność przypisań przy jednoczesnym czytelnym nazewnictwie urządzeń.
- **Aktualizacje z GitHub Releases i ochrona wersji w sieci:** Wtyczka sprawdza raz na dobę dostępność nowego wydania na GitHub Releases (za jawną zgodą użytkownika), umożliwia ręczną aktualizację ze wskazanego pliku ZIP i pilnuje w `SudoSync.cfg` wymaganej minimalnej wersji (`min_addon_version`). Nowsza wersja zapisana przez inne Kodi od razu wywołuje pytanie o aktualizację.
- **Dziennik Zdarzeń (Journaling):** Synchronizacja używa mechanizmu transakcyjnego z dziennikiem postępu, pozwalając na bezpieczne wznowienie po nieoczekiwanym błędzie. Jeśli pozycja zmieniła się w Kodi po przerwanym zapisie, odzyskiwanie zostawia aktualną wartość z Kodi, pomija tę pozycję bez zapisu i działa dalej, więc przerwany zapis nigdy nie blokuje synchronizacji na stałe.
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
   - **KodiBridge by Sudoizm 2.2.5 (Produkcyjna):** Pobierz plik `kodibridge-by-sudoizm-2.2.5.zip` z sekcji **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)** (zalecane wydanie produkcyjne z architekturą v2 i KodiBridge ID).
   - **SudoSync 1.0.0 (Starsza wersja stabilna):** Pobierz plik `service.sudosync-1.0.0.zip` z sekcji **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)**.

2. **Instalacja w Kodi:**
   - W Kodi przejdź do: *Ustawienia -> Dodatki -> Zainstaluj z pliku zip*.
   - Wskaż przygotowane archiwum zip.

3. **Konfiguracja ścieżki sieciowej i identyfikatorów:**
   - Przy pierwszym uruchomieniu kreator KodiBridge poprosi o wskazanie wspólnego katalogu sieciowego (np. `smb://192.168.1.100/Wspoldzielony/`) i automatycznie utworzy w nim folder `.SudoSync/` oraz zainicjuje urządzenie.
   - W ustawieniach dodatku możesz nadać swojemu urządzeniu przyjazną nazwę oraz skonfigurować **Nazwę sieci (prefiks KodiBridge ID)** dla prywatnych wideo.

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