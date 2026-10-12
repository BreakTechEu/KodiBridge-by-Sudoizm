# SudoSync — trwały kontekst projektu

Ten plik jest krótką, trwałą pamięcią wymagań użytkownika. Przeczytaj go na
początku każdej sesji pracy. Szczegółowy przepływ użytkownika znajduje się w
[`V2_USER_FLOW.md`](V2_USER_FLOW.md), a projekt techniczny w
[`V2_DESIGN.md`](V2_DESIGN.md). Jeśli bieżąca prośba użytkownika coś zmienia,
nadrzędna jest bieżąca prośba; potem zaktualizuj te pliki.

## Cel i repozytorium

- Projekt: `BreakTechEu/SudoSync`.
- `master` jest docelową gałęzią główną dla v2.
- `v1-stable` to działająca, stabilna wersja v1 i jest tylko do odczytu.
- Rozwój v2 prowadzimy na `v2/rewrite-2026-10-10`, a do `master` integrujemy
  v2 uzgodnioną ścieżką przeglądu. Nie traktuj `master` jako gałęzi v1 ani
  `v1-stable` jako celu zmian v2.
- Stan gałęzi, kodu i testów sprawdzaj na początku sesji — ten plik nie jest
  źródłem aktualnego statusu Git.
- Nie scalaj PR ani nie publikuj wydania bez wyraźnej zgody Romana.
- Audyt zewnętrzny wykonuj dopiero przed przekazaniem zmian do `master` albo
  przygotowaniem wtyczki do testu na rzeczywistym Kodi.

## Uzgodniony model użycia

- Roman i docelowy użytkownik korzystają tylko z jednego Kodi naraz. Najpierw
  kończą pracę na jednym urządzeniu, potem uruchamiają następne.
- SudoSync działa jako usługa w tle Kodi, więc pozostałe urządzenia nie mają
  w tym czasie uruchomionej usługi SudoSync.
- Ochrona przed równoczesnym uruchomieniem jest dodatkiem. Nie projektuj jej
  jako warunku rozpoczęcia lub ukończenia zwykłego przepływu. Nie wracaj do
  wielokrotnego pytania, czy inne urządzenia pracują w tle — użytkownik już
  określił zasadę pracy jednym Kodi.
- Weryfikacja na rzeczywistych urządzeniach jest sekwencyjna: wykonać zmianę
  na jednym Kodi, zakończyć jego pracę, uruchomić kolejne Kodi i sprawdzić
  rezultat. Nie wymagać fizycznej równoczesnej pracy dwóch Kodi.

## Zachowanie wtyczki

- Szczegóły pierwszego uruchomienia, `SudoSync.cfg`, konfiguratora, migracji
  i aktualizacji są opisane w `docs/V2_USER_FLOW.md`. Traktuj ten dokument
  jako wymagania użytkownika, a nie sugestię.
- Ścieżkę udziału wybiera się selektorem katalogu Kodi; użytkownik nie ma
  ręcznie przepisywać adresu SMB/NFS. Plik wspólny to
  `<base_path>/SudoSync.cfg`.
- Konfiguracja sieciowa jest wspólna. ID klienta, nazwa i ustawienia
  konkretnego Kodi pozostają lokalne i nie mogą być kopiowane przez plik
  wspólny.
- Aktualizacja jest dystrybuowana przez paczkę umieszczoną przez
  administratora w `.SudoSync` i sprawdzaną przez uruchomioną wtyczkę.
  Użytkownik nie może jej pominąć. Nie dodawaj funkcji wgrywania paczki przez
  wtyczkę bez osobnego polecenia.
- Synchronizowane pola: licznik odtworzeń, data ostatniego odtworzenia,
  pozycja wznowienia i ocena użytkownika dla istniejących filmów i odcinków.
- Zmiany biblioteki wykonuj wyłącznie przez Kodi JSON-RPC. Nigdy nie edytuj
  `MyVideos*.db` bezpośrednio.
- Dopasowuj po silnych identyfikatorach z uwzględnieniem rodzaju medium.
  Tytuł, rok, ścieżka i lokalny ID Kodi same nie wystarczają. Nie dodawaj
  zdalnych pozycji do lokalnej biblioteki. Konflikty/niejednoznaczności
  raportuj i pomijaj zamiast zgadywać.
- Nie zakładaj, że każdy ma IMDb ID. Łącz obsługiwane mocne ID z Kodi
  JSON-RPC i `.nfo` (m.in. `<uniqueid type="…">`, filmowe `<tmdbid>` oraz
  legacy IMDb `<id>tt…</id>`). Jeśli brak mocnego ID i nie można przypisać
  SudoSync ID, pomiń rekord zamiast dopasowywać go po tytule.
- Przed zmianami wymagany jest podgląd, jawne zatwierdzenie, kopia i
  sprawdzenie wyniku. Podczas odtwarzania odraczaj zdalne zmiany.
- Zachowaj model LIVE v1: każde Kodi publikuje własny snapshot, zmiany każdego
  pola mają wersję logiczną, a uruchomione Kodi uzgadnia je z peer snapshotami
  niezależnie od współbieżności startu. Zachowuj zwycięską wersję przy
  replikacji, aby uniknąć pętli. Użytkownik wymaga catch-up mimo długich
  odstępów między uruchomieniami. W sprawdzonym v1 snapshot peer starszy niż
  7 dni powoduje błąd odczytu, który zatrzymuje cykl LIVE; nie przenoś tego
  ograniczenia do v2 bez zachowania catch-up po długiej przerwie.
- Przy wykryciu nowego ZIP-a aktualizacji, który nie przejdzie weryfikacji,
  pokaż jasny komunikat użytkownikowi, odmów instalacji i zatrzymaj start
  wtyczki do czasu zastąpienia lub usunięcia paczki.

## Zasady współpracy

- Kontynuuj pracę w spójnych etapach; nie pytaj ponownie o ustalenia zapisane
  w tym pliku ani w `V2_USER_FLOW.md`.
- Zachowuj niezapisane zmiany. Przed synchronizacją sprawdź status i historię;
  nie używaj ślepego resetu i nie rozwiązuj konfliktów przez zgadywanie.
- W trakcie implementacji wykonuj zwykłe testy i własny przegląd kodu, gdy
  są potrzebne do zmiany. Nie przedstawiaj planu ani dokumentacji jako
  ukończonej wtyczki.
- Raportuj zmienione pliki, potwierdzone testy, commit/push i najbliższy
  pozostały etap. Wyraźnie odróżniaj stan lokalny od tego, co jest na GitHubie.


## Stan przekazania — 2026-10-12

- Aktualny kod rozwoju znajduje się na GitHubie w `v2/rewrite-2026-10-10`.
  Zmiana adaptera JSON-RPC łączy dostępne identyfikatory z Kodi z
  identyfikatorami odczytanymi z plików `.nfo` (obsługiwane
  `<uniqueid type="…">`, filmowe `<tmdbid>` i legacy IMDb
  `<id>tt…</id>`). Sprzeczne identyfikatory zatrzymują odczyt zamiast
  automatycznego dopasowania.
- Potwierdzone: 13 testów `tests/test_sudosync_v2_kodi_rpc.py` przeszło
  lokalnie. To testy adaptera, nie test end-to-end na Kodi.
- Ograniczenie: `KodiJsonRpcAdapter` nie jest jeszcze podłączony do
  `service.py` / produkcyjnego runtime v2. Zmiana sama w sobie nie oznacza,
  że synchronizacja v2 działa end-to-end. Nie budować ZIP-a ani nie deklarować
  gotowości do testu na Kodi.
- Bieżący etap lokalny: dodano `KodiVFSMetadataReader` i kompozytor
  `build_kodi_v2_runtime`, który łączy adapter JSON-RPC z czytaniem NFO przez
  Kodi VFS. Testy VFS, runtime i JSON-RPC przeszły (33 testy, 1 pominięty z
  powodu braku uprawnień Windows do tworzenia symlinków). Etap zapisano w
  commicie `47fb544` na gałęzi rozwoju. `service.py` nadal używa v1;
  przed jego przełączeniem trzeba zapewnić i zweryfikować międzyprocesową
  blokadę wspólnego udziału oraz dokończyć bezpieczny przepływ preview,
  zatwierdzenia, transakcji i odzyskiwania.
- Następny etap: podłączyć adapter do runtime usługi, dostarczyć mu czytnik
  NFO przez Kodi VFS, a następnie zintegrować bezpieczny przepływ podglądu,
  transakcji i odzyskiwania zgodnie z `V2_DESIGN.md`.
- Gałąź GitHub została zaktualizowana przez integrację GitHub, ponieważ
  lokalny Git nie rozwiązywał `github.com`. Lokalny checkout domowy nie
  został pobrany ponownie; jego historia Git nie jest zsynchronizowana ze
  zdalną. Na początku kolejnej sesji sprawdź stan GitHuba i bezpiecznie
  zsynchronizuj checkout przed dalszymi commitami. Nie resetuj go w ciemno.
- `master` i `v1-stable` pozostają nietknięte.
