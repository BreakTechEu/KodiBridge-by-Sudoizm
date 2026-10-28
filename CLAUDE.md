# CLAUDE.md — stałe zasady pracy

Zasady obowiązują w każdej sesji. Ten plik nie powtarza innych dokumentów —
przed pracą przeczytaj:

- [`AGENTS.md`](AGENTS.md) — start sesji, gałęzie, zakaz ingerencji w Kodi,
- [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) — trwałe wymagania i zasady współpracy,
- [`docs/V2_USER_FLOW.md`](docs/V2_USER_FLOW.md) — uzgodniony przepływ użytkownika,
- [`README.md`](README.md) / [`README.pl.md`](README.pl.md) — funkcje, instalacja, aktualizacje.

Przy sprzeczności z tamtymi dokumentami pierwszeństwo mają zasady z tego pliku.

## Język

1. Z właścicielem komunikuj się po polsku, z poprawną polską pisownią.
   Kod, komentarze w kodzie i komunikaty commitów — po angielsku.

## Git

2. Gałąź robocza: `master`. Gałęzi `v1-stable` i tagu `v1.0.0` nie wolno
   zmieniać ani usuwać.
3. Commity w konwencji Conventional Commits (`feat:`, `fix:`, `docs:`,
   `test:`, `refactor:`, `chore:` …).

## Pliki i bezpieczeństwo

4. Wszystkie pliki tekstowe zapisuj w UTF-8 bez BOM.
5. Żadnych haseł, tokenów ani kluczy w kodzie (także prywatnego klucza RSA).
   Dane wrażliwe wyłącznie ze zmiennych środowiskowych.
6. `STATUS_PROJEKTU.md` i `WIEDZA.md` nie trafiają do repozytorium — muszą
   pozostać w `.gitignore`.

## Dokumentacja

7. Po zmianach w logice, architekturze lub konfiguracji zaktualizuj
   `README.md`, jego tłumaczenia (`README.pl.md` i kolejne) oraz
   `service.sudosync/CHANGELOG.md`.

## Testy i raport

8. Przed każdym commitem uruchom pełny zestaw testów i pyflakes:

       python -m unittest discover -s tests
       python -m pyflakes service.sudosync tests tools

   W raporcie podaj liczbę testów oraz hash commita skopiowany dosłownie
   z `git log`.

## Kodi

9. Zakaz jakiejkolwiek ingerencji w lokalną instalację Kodi właściciela
   (szczegóły w `AGENTS.md`). Paczki ZIP instaluje i testuje on sam, ręcznie.

## Architektura

10. Jeden silnik synchronizacji — v2. Bez równoległego działania silnika v1.

## Wydania

11. Wersja w `service.sudosync/addon.xml` musi być identyczna z wersją
    w nazwie paczki (`service.sudosync-X.Y.Z.zip`). Publikacja w GitHub
    Releases oraz tworzenie, przesuwanie i usuwanie tagów — tylko na wyraźne
    polecenie właściciela.

## Identyfikacja repozytorium

12. Każdą odpowiedź dla właściciela zaczynaj od znacznika w nawiasach
    kwadratowych z nazwą repozytorium, w którym sesja faktycznie pracuje.
    Nazwę ustal na początku sesji z bieżącego adresu zdalnego
    (`git remote get-url origin`, część po `BreakTechEu/` bez `.git`),
    a nie z treści tego pliku ani z pamięci. Jeśli polecenie wygląda na
    dotyczące innego repozytorium niż to, zatrzymaj się i zapytaj, zamiast
    je wykonywać.
