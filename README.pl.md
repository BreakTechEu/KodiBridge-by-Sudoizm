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
   - Pobierz najnowszą paczkę `service.sudosync-1.2.0-beta.zip`.

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
- **Kryptograficzne Bezpieczeństwo:** Mechanizm self-update weryfikuje aktualizacje pobierane z NAS używając podpisu RSA-2048. Niezaufane archiwum ZIP jest odrzucane i blokowane.
- **Dziennik Zdarzeń (Journaling):** Synchronizacja początkowa używa mechanizmu transakcyjnego z dziennikiem postępu, pozwalając na bezpieczne wznowienie po nieoczekiwanym błędzie.
- **Limity Bezpieczeństwa:** System wprowadza maksymalne limity pamięci, wielkości plików oraz liczby rekordów dla bezpieczeństwa klienta.

---

## Aktualizacja wtyczki

> [!IMPORTANT]
> **Ważna informacja dotycząca aktualizacji:**
> Obecnie (czasowo) jedynym prawidłowym i nie psującym instalacji sposobem aktualizacji wtyczki jest umieszczenie nowej wersji (pliku `.zip`) bezpośrednio we wspólnym folderze sieciowym `.SudoSync` (np. `smb://.../.SudoSync/service.sudosync-X.X.X.zip`).
>
> SudoSync samoczynnie wykryje nowy pakiet w udziale sieciowym i przeprowadzi aktualizację (wyświetlając powiadomienie z pytaniem o instalację lub instalując go w tle, o ile włączono automatyczne aktualizacje w konfiguracji wtyczki). Nie należy instalować nowej wersji na istniejącą instalację poprzez standardową opcję Kodi „Zainstaluj z pliku zip”.

---

## Kompatybilność

> **Ważne:** Wtyczka wspiera wyłącznie dwie najnowsze główne wersje Kodi (obecnie v20 Nexus oraz v21 Omega). Starsze wersje przestają być wspierane.

- Kodi v20 Nexus oraz v21 Omega.
- Wieloplatformowość: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## Licencja

Projekt typu open-source udostępniany na licencji GPL-3.0. Jakiekolwiek komercyjne wykorzystanie oprogramowania wymaga uzyskania osobnej licencji komercyjnej. W tym celu należy skontaktować się z autorem.