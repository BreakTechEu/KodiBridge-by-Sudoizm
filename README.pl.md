[English](README.md) | **Polski**

---

> [!WARNING]
> **Ostrzeżenie o wersji Beta (SudoSync 2.0.0):**
> SudoSync 2.0.0 jest **wersją testową (Beta)** wprowadzającą przebudowaną architekturę v2 oraz eksperymentalną obsługę identyfikatorów SudoSync ID dla prywatnych wideo. Mimo szczegółowych testów jednostkowych i odpornościowych, wydanie to jest przeznaczone do testów i dla wczesnych użytkowników. Jeżeli potrzebujesz w 100% sprawdzonego i stabilnego środowiska produkcyjnego, zalecamy korzystanie ze stabilnej wersji **SudoSync 1.0.0** na gałęzi [v1-stable](https://github.com/BreakTechEu/SudoSync-Kodi-addon/tree/v1-stable).
> Obie wersje są dostępne do pobrania w sekcji **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)**.

# SudoSync dla Kodi

Lekka wtyczka usługi działająca w tle dla odtwarzacza Kodi, zapewniająca automatyczną, dwukierunkową synchronizację stanu obejrzenia, punktów wznowienia, ocen użytkownika oraz daty ostatniego odtworzenia pomiędzy wieloma instancjami Kodi w tej samej sieci lokalnej — bez konieczności stawiania zewnętrznego serwera bazy danych MySQL/MariaDB.

Synchronizacja opiera się na wspólnym zasobie sieciowym (SMB, NFS lub zasób lokalny) oraz bezpiecznych wywołaniach oficjalnego API JSON-RPC.

---

## Główne cechy

- **Brak dedykowanej bazy danych:** Wykorzystuje współdzielony zasób sieciowy dostępny dla urządzeń (serwer NAS, lokalny komputer lub udział sieciowy).
- **Bezpieczeństwo i brak destrukcyjnych zmian:** Działa wyłącznie za pośrednictwem oficjalnego API JSON-RPC Kodi. Nigdy nie modyfikuje ani nie blokuje bezpośrednio wewnętrznych plików bazy SQLite (`MyVideos*.db`).
- **Niezawodny model rozwiązywania konfliktów:** Każde synchronizowane pole zachowuje własny znacznik czasu oraz wersję sekwencji klienta, zapobiegając wyścigom (*race conditions*) i pętlom zwrotnym.
- **Optymalizacja dla Android TV i przystawek Smart TV:** Lekki silnik badania różnic w tle eliminuje problemy z gubieniem powiadomień systemowych `OnUpdate` i `OnStop` na przystawkach multimedialnych.
- **Kwarantanna podczas skanowania i plików NFO:** Nowo zeskanowane elementy biblioteki oraz importy NFO otrzymują starsze wersje wewnętrzne, co zapobiega przypadkowemu nadpisaniu istniejącego, współdzielonego postępu oglądania.
- **Eksperymentalne wsparcie dla SudoSync ID:** Automatycznie generuje i bezpiecznie wstrzykuje unikalne identyfikatory do plików `.nfo` dla prywatnych domowych wideo, które nie posiadają własnych wpisów w internetowych bazach danych.
- **Rozdzielenie ID sieciowego od Aliasu UI:** Wyraźnie oddziela wewnętrzny identyfikator sieciowy od przyjaznego aliasu wyświetlanego w interfejsie. Zapewnia to stabilność przypisań przy jednoczesnym czytelnym nazewnictwie urządzeń.
- **Kryptograficzne Bezpieczeństwo:** Mechanizm self-update weryfikuje aktualizacje pobierane z NAS używając podpisu RSA-2048. Niezaufane archiwum ZIP jest odrzucane i blokowane.
- **Dziennik Zdarzeń (Journaling):** Synchronizacja początkowa używa mechanizmu transakcyjnego z dziennikiem postępu, pozwalając na bezpieczne wznowienie po nieoczekiwanym błędzie.
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
   - **SudoSync 2.0.0 (Beta):** Pobierz plik `service.sudosync-2.0.0.zip` z sekcji **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** (zalecany do testowania nowych funkcji v2 i SudoSync ID).
   - **SudoSync 1.0.0 (Stabilna):** Pobierz plik `service.sudosync-1.0.0.zip` z sekcji **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** (zalecany do środowisk produkcyjnych wymagających maksymalnej stabilności).

2. **Instalacja w Kodi:**
   - W Kodi przejdź do: *Ustawienia -> Dodatki -> Zainstaluj z pliku zip*.
   - Wskaż przygotowane archiwum zip.

3. **Konfiguracja ścieżki sieciowej i identyfikatorów:**
   - Otwórz ustawienia dodatku SudoSync w Kodi.
   - Wskaż wspólny katalog sieciowy, do którego uprawnienia odczytu i zapisu mają wszystkie instancje Kodi, np.:
     ```text
     smb://192.168.1.100/Wspoldzielony/.SudoSync/
     ```
   - W ustawieniach określ swój **Identyfikator sieciowy** (używany wewnętrznie do synchronizacji plików) oraz **Alias UI** (przyjazną nazwę wyświetlananą na ekranie).

Szczegółowa instrukcja bezpiecznego ręcznego testu wersji 2.0.0 (Beta) krok po kroku znajduje się w pliku [`docs/TEST_NA_KODI.md`](docs/TEST_NA_KODI.md).

---

## Aktualizacja wtyczki

> [!IMPORTANT]
> **Mechanizm bezpiecznej aktualizacji:**
> Wtyczka SudoSync posiada wbudowany system aktualizacji chroniący spójność sieci:
> 1. **Automatyczne sprawdzanie w GitHub Releases (raz na dobę):** Wtyczka sprawdza raz na 24 godziny dostępność nowego wydania w oficjalnym repozytorium GitHub (`BreakTechEu/SudoSync-Kodi-addon`). Nie wykonuje cichych aktualizacji w tle — po wykryciu nowej wersji wyświetla pytanie o zgodę na pobranie i instalację (można też wywołać sprawdzenie ręcznie w ustawieniach wtyczki).
> 2. **Aktualizacja z lokalnego pliku ZIP:** W ustawieniach wtyczki znajduje się opcja ręcznego wskazania i zainstalowania paczki `service.sudosync-X.X.X.zip` z dysku lokalnego lub udziału sieciowego.
> 3. **Strażnik zgodności wersji w sieci (`min_addon_version` w `SudoSync.cfg`):** Gdy którekolwiek urządzenie Kodi w sieci zostanie zaktualizowane do nowszej wersji, zapisuje wymaganą minimalną wersję we wspólnym pliku `SudoSync.cfg`. Jeśli inne urządzenie Kodi uruchomi się ze starszą wersją wtyczki, SudoSync wymusi aktualizację (z GitHub Releases lub wskazanego pliku ZIP) albo zablokuje działanie usługi do czasu zaktualizowania wtyczki.

---

## Kompatybilność

> **Ważne:** Wtyczka wspiera wyłącznie dwie najnowsze główne wersje Kodi (obecnie v20 Nexus oraz v21 Omega). Starsze wersje przestają być wspierane.

- Kodi v20 Nexus oraz v21 Omega.
- Wieloplatformowość: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## Licencja

Projekt typu open-source udostępniany na licencji [GPL-3.0-or-later](LICENSE).