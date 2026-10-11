# Fizyczna weryfikacja blokady współdzielonej SudoSync v2

Ten test jest wymagany przed użyciem blokady systemowej na udziałach SMB/NFS. Testy jednostkowe i zielone CI nie dowodzą, że blokada jest widoczna między hostami.

## Warunki

- Udział musi być zamontowany jako natywna ścieżka systemu operacyjnego na każdym urządzeniu. Ścieżki Kodi VFS typu `smb://...` i `nfs://...` są celowo odrzucane.
- Oba urządzenia muszą wskazywać na dokładnie ten sam plik na tym samym udziale.
- Nie usuwaj pliku blokady po teście. Usunięcie i utworzenie go ponownie podczas pracy klientów może rozdzielić blokady na różne obiekty plikowe.
- Zapisz system operacyjny, wersję Kodi, typ protokołu, serwer/NAS, opcje montowania oraz wynik każdego scenariusza.

## Test 1 — wyłączność dwóch hostów

Na obu urządzeniach uruchom z tą samą ścieżką do udziału:

```sh
python tools/sudosync_shared_lock_probe.py --path "/mnt/sudosync/.sudosync-lock-probe.lock" --label "Kodi-A" --hold-seconds 45
```

Uruchom drugie polecenie na Kodi-B z `--label "Kodi-B" --nonblocking`. Gdy A trzyma blokadę, B musi zgłosić `BUSY` i zakończyć kodem 2. Nie może pojawić się drugi komunikat `ACQUIRED` w czasie posiadania blokady przez A.

Powtórz test z odwróconą kolejnością i dla każdej kombinacji systemów operacyjnych/protokołów, którą zamierzasz wspierać.

## Test 2 — zwolnienie po awarii

Uruchom proces A bez `--nonblocking`, a następnie zakończ go wymuszenie podczas trzymania blokady. Po potwierdzeniu zakończenia procesu uruchom test B. B powinien zdobyć blokadę. Powtórz po utracie połączenia sieciowego i po restarcie hosta, jeśli te awarie są w zakresie wsparcia.

## Kryterium

Każdy test musi wykazać jednego właściciela naraz, poprawne zwolnienie blokady i brak sytuacji, w której obaj klienci raportują `ACQUIRED` jednocześnie. Każda niejednoznaczność oznacza, że ten wariant systemu plików nie może być użyty do synchronizacji mutującej. Nie ustawiaj `filesystem_verified=True` na podstawie samego CI.
