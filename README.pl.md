[English](README.md) | **Polski**

---

> **Uwaga:** To repozytorium (gałąź master) zawiera teraz nową wersję **SudoSync v2 (Beta)**, wprowadzającą własne identyfikatory SudoSync ID dla prywatnych wideo pozbawionych wpisów w bazach online. Czekamy na Wasze zgłoszenia problemów i potrzeb! Starsza, w pełni stabilna wersja v1 znajduje się na gałęzi [v1-stable](https://github.com/BreakTechEu/SudoSync/tree/v1-stable).

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

---

## Synchronizowane pola

- Status obejrzenia i licznik odtworzeń (*watched / unwatched*),
- Dokładny punkt wznowienia odtwarzania (*resume position*),
- Data ostatniego odtworzenia (*last played*),
- Oceny użytkownika (1–10).

---

## Instalacja i konfiguracja

1. **Pobranie:**
   - Przejdź do sekcji **Releases** po prawej stronie repozytorium.
   - Pobierz najnowszą paczkę `service.sudosync-1.1.1-beta.zip`.

2. **Instalacja w Kodi:**
   - W Kodi przejdź do: *Ustawienia -> Dodatki -> Zainstaluj z pliku zip*.
   - Wskaż pobrane archiwum.

3. **Konfiguracja ścieżki sieciowej i identyfikatorów:**
   - Otwórz ustawienia dodatku SudoSync w Kodi.
   - Wskaż wspólny katalog sieciowy, do którego uprawnienia odczytu i zapisu mają wszystkie instancje Kodi, np.:
     ```text
     smb://192.168.1.100/Wspoldzielony/.SudoSync/
     ```
   - W ustawieniach określ swój **Identyfikator sieciowy** (używany wewnętrznie do synchronizacji plików) oraz **Alias UI** (przyjazną nazwę wyświetlananą na ekranie).

---

## Kompatybilność

- Kodi v19 Matrix, v20 Nexus oraz v21 Omega.
- Wieloplatformowość: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## Licencja

Projekt typu open-source udostępniany na licencji GPL-3.0.