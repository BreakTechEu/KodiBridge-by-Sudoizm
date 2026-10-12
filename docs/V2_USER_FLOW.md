# SudoSync v2 — uzgodniony przepływ użytkownika

Ten dokument zapisuje wymagania Romana dla działania wtyczki. Treść zrzutu ekranu
„Ocena koncepcji wtyczki” traktujemy jako wymagania użytkownika; odpowiedzi
asystenta widoczne na zrzucie nie są źródłem wymagań.

## Zasada używania

- Użytkownik korzysta tylko z jednego Kodi naraz. Kończy pracę na urządzeniu A,
  a dopiero potem uruchamia urządzenie B.
- Usługa SudoSync działa w tle Kodi, dlatego na pozostałych urządzeniach nie
  uruchamia się w tym czasie Kodi z aktywną usługą SudoSync.
- Ochrona przed przypadkowym równoczesnym uruchomieniem jest dodatkowym
  zabezpieczeniem. Nie jest warunkiem zaprojektowania ani ukończenia zwykłego,
  sekwencyjnego przepływu użytkownika.

## Pierwsze uruchomienie

1. Użytkownik instaluje SudoSync i uruchamia Kodi. Usługa SudoSync startuje
   automatycznie.
2. Jeżeli lokalnie nie ma jeszcze zapisanej ścieżki do udziału, wtyczka otwiera
   selektor katalogu Kodi. Użytkownik wybiera folder sieciowy; nie musi ręcznie
   wpisywać adresu SMB/NFS. Jeśli wybrany folder nie jest już `.SudoSync`,
   wtyczka używa w nim podkatalogu `.SudoSync`.
3. Wtyczka sprawdza dostęp do wybranego miejsca i plik
   `<base_path>/SudoSync.cfg`.
4. Jeśli plik istnieje i jest poprawny, wtyczka odczytuje wspólne ustawienia
   sieciowe i stosuje je lokalnie. Uszkodzony lub nieczytelny plik nie może być
   uznany za brak konfiguracji ani nadpisany kreatorem.
5. Jeśli pliku nie ma, wtyczka uruchamia wizualny konfigurator pierwszego
   uruchomienia. Pyta o ustawienia sieciowe, ale nie pyta ponownie o ścieżkę,
   którą wybrano w selektorze. Domyślnie próbuje używać SudoSync ID; jeśli to
   się nie powiedzie, przechodzi na awaryjne identyfikatory IMDb, gdy są
   dostępne.
6. Po ukończeniu konfiguratora wtyczka zapisuje wspólną konfigurację do
   `SudoSync.cfg`, sprawdza zapis przez ponowny odczyt i oznacza konfigurator
   jako ukończony. Dopiero potem rozpoczyna normalną pracę.

## Istniejąca instalacja i aktualizacje

- Brak `SudoSync.cfg` nie oznacza automatycznie nowej instalacji. Wtyczka
  rozpoznaje istniejącą strukturę SudoSync i kieruje taką instalację do jawnej
  migracji, zachowując dotychczasowe identyfikatory oraz pliki i dane klientów.
- Aktualizację dystrybuuje administrator, umieszczając paczkę w istniejącym
  katalogu `.SudoSync`. Uruchomiona wtyczka sprawdza ją na początku działania.
  Użytkownik nie ma opcji pominięcia aktualizacji. Weryfikacja paczki musi
  zakończyć się powodzeniem przed instalacją.
- Zawartość `SudoSync.cfg` jest wspólna dla sieci. Nazwa tego pliku nie oznacza,
  że wolno umieszczać w nim ustawienia konkretnego urządzenia Kodi.

## Co jest wspólne, a co lokalne

- Wspólny udział zawiera konfigurację sieci, współdzielone snapshoty i dane
  potrzebne do synchronizacji.
- Każde Kodi zachowuje lokalnie własny stabilny `client_id`, nazwę urządzenia
  i ustawienia profilu. Dane lokalne nie mogą być kopiowane między klientami
  przez `SudoSync.cfg`.
- Współdzielone dane nie mogą zawierać ścieżek ani lokalnych identyfikatorów
  `movieid`/`episodeid` Kodi jako globalnych identyfikatorów.

## Zwykła synchronizacja

- Synchronizowane są stan odtworzenia, data ostatniego odtworzenia, pozycja
  wznowienia i ocena użytkownika dla filmów i odcinków już obecnych w lokalnej
  bibliotece.
- Dopasowanie odbywa się po mocnych identyfikatorach z uwzględnieniem typu
  medium: SudoSync ID, IMDb, TMDb, TVDb lub Trakt. Sam tytuł, rok, ścieżka ani
  lokalny ID Kodi nie uprawniają do automatycznego dopasowania.
- Nie dodajemy zdalnych pozycji do biblioteki. Niejednoznaczne rekordy i
  konflikty są raportowane i pomijane zamiast cichego nadpisania.
- Podgląd poprzedza operację pierwszej inicjalizacji. Zmiana biblioteki wymaga
  jawnego zatwierdzenia, kopii przed zapisem i weryfikacji wyniku przez Kodi
  JSON-RPC. Baza `MyVideos*.db` nigdy nie jest modyfikowana bezpośrednio.
- Podczas odtwarzania zdalne zmiany są odraczane. Weryfikację na urządzeniach
  prowadzi się sekwencyjnie: zmiana na jednym Kodi, zakończenie jego pracy,
  następnie sprawdzenie na kolejnym.

## Granica obecnej implementacji

W chwili zapisania tego dokumentu v2 ma logikę domenową, adaptery, podgląd,
planowanie, transakcje i odzyskiwanie testowane jednostkowo. Pełne podłączenie
tego przepływu do `service.py`, konfiguratora v2 i zwykłego cyklu Kodi pozostaje
pracą do wykonania. Obecny bootstrap i usługa uruchamiają jeszcze starą ścieżkę.
