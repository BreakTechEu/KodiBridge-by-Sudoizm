# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

import sys

import xbmcgui

from resources.lib.sudosync_core import (
    apply_initial_sync,
    arm_initial_write_guard,
    disarm_initial_write_guard,
    initial_write_guard_status,
    check_for_update,
    clear_initial_base_selection,
    collect_and_write,
    find_latest_update,
    get_or_create_client_id,
    install_latest_update,
    read_local_status,
    read_shared_config,
    run_dry_run,
    run_live_sync_cycle,
    addon_settings,
    select_this_as_initial_base,
    sync_initial_base_selection,
    sync_network_settings,
)


def show_status():
    status = read_local_status()
    settings = addon_settings()
    kodi = status.get("kodi") or {}
    counts = status.get("counts") or {}
    rpcv = kodi.get("jsonrpc")
    if isinstance(rpcv, dict):
        rpcv = "{}.{}.{}".format(rpcv.get("major", 0), rpcv.get("minor", 0), rpcv.get("patch", 0))

    upd = check_for_update(show_notification=False)
    if upd.get("available"):
        update_text = "Dostępna: {} ({})".format(upd.get("version"), upd.get("filename"))
    elif upd.get("error"):
        update_text = "Nie udało się sprawdzić: {}".format(upd.get("error"))
    else:
        update_text = "Brak nowszej wersji (zainstalowana {})".format(upd.get("current_version", "—"))

    try:
        shared = sync_initial_base_selection(show_notification=False)
        sync_network_settings()
        init = shared.get("initialization") or {}
        base_name = init.get("base_client_name") or "—"
        init_state = "zakończona" if init.get("completed") else "niezakończona"
    except Exception as exc:
        base_name = "błąd: {}".format(exc)
        init_state = "—"

    dry = status.get("dry_run_summary") or {}
    plan = status.get("dry_run_local_plan") or {}
    dry_text = (
        "Ostatnia analiza: {when}\n"
        "Bezpieczne wspólne pozycje: {safe}\n"
        "Bezpieczne mirrory lokalny/NAS: {mir}\n"
        "Niejednoznaczne grupy (pomijane): {amb}\n"
        "Bez mocnego ID: {noid}\n"
        "Konflikty ocen: {ratings}\n"
        "Planowane zmiany na tym Kodi: {items}\n"
        "  playcount: {pc}\n"
        "  lastplayed: {lp}\n"
        "  userrating: {ur}\n"
        "  resume: {rs}\n"
        "Raport: {report}"
    ).format(
        when=status.get("last_dry_run_at", "—"),
        safe=dry.get("safe_shared_clusters", "—"),
        mir=dry.get("safe_mirror_clusters", 0),
        amb=dry.get("ambiguous_clusters", "—"),
        noid=dry.get("records_without_strong_id", "—"),
        ratings=dry.get("rating_conflicts", "—"),
        items=plan.get("items", 0),
        pc=plan.get("playcount", 0),
        lp=plan.get("lastplayed", 0),
        ur=plan.get("userrating", 0),
        rs=plan.get("resume", 0),
        report=status.get("dry_run_report_path", "—"),
    )

    live_enabled = settings.get("live_sync_enabled", True)
    waiting = status.get("live_waiting_clients") or []
    live_plan = status.get("live_local_plan") or {}
    if init_state == "zakończona":
        live_state = "WŁĄCZONA" if live_enabled else "WYŁĄCZONA"
    else:
        live_state = "OCZEKUJE NA ZAKOŃCZENIE INICJALIZACJI"
    live_text = (
        "Synchronizacja bieżąca: {state}\n"
        "Odpytywanie innych Kodi: co {poll} s\n"
        "Ostatni cykl LIVE: {when}\n"
        "Ostatnio odebrano: {applied} pozycji\n"
        "Ostatnio wysłano lokalnie: {published} pozycji\n"
        "Bieżący plan lokalny: {items} pozycji\n"
        "Ostatnia kontrola lokalna: {probe_when}\n"
        "Kontrola wykryła zmianę: {probe_changed} ({probe_items} pozycji)\n"
        "Oczekiwanie na aktualizację klientów: {waiting}\n"
        "Raport LIVE: {report}"
    ).format(
        state=live_state,
        poll=settings.get("live_poll_seconds", 60),
        when=status.get("last_live_sync_at") or status.get("last_live_plan_at") or "—",
        applied=status.get("last_live_applied_items", 0),
        published=status.get("last_live_published_items", 0),
        items=live_plan.get("items", 0),
        probe_when=status.get("last_local_probe_at") or "—",
        probe_changed="tak" if status.get("last_local_probe_changed") else "nie",
        probe_items=status.get("last_local_probe_changed_items", 0),
        waiting=", ".join(waiting) if waiting else "nie",
        report=status.get("last_live_report_path", "—"),
    )

    import xbmcaddon
    addon_version = xbmcaddon.Addon("service.sudosync").getAddonInfo("version")

    text = (
        "Tryb: SudoSync {version} — LIVE + powiadomienia zmian i ochrona nowszego odtwarzania\n\n"
        "Urządzenie: {name}\n"
        "Client ID: {cid}\n"
        "Kodi: {kodi} ({platform})\n"
        "JSON-RPC: {rpc}\n\n"
        "Urządzenie bazowe inicjalizacji: {base}\n"
        "Pierwsza synchronizacja: {init_state}\n\n"
        "Ostatni snapshot: {when}\n"
        "Filmy: {movies}\n"
        "Odcinki: {episodes}\n\n"
        "Snapshot: {path}\n\n"
        "{dry}\n\n"
        "{live}\n\n"
        "Aktualizacje: {update}"
    ).format(
        version=addon_version,
        name=status.get("client_name", "Kodi"),
        cid=status.get("client_id", "—"),
        kodi=kodi.get("version", "—"),
        platform=kodi.get("platform", "—"),
        rpc=rpcv or "—",
        base=base_name,
        init_state=init_state,
        when=status.get("last_snapshot_at", "—"),
        movies=counts.get("movies", 0),
        episodes=counts.get("episodes", 0),
        path=status.get("remote_path", "—"),
        dry=dry_text,
        live=live_text,
        update=update_text,
    )
    xbmcgui.Dialog().textviewer("SudoSync — status", text)


def apply_initial_dialog():
    guard = initial_write_guard_status()
    if not guard.get("armed"):
        xbmcgui.Dialog().ok(
            "SudoSync — bezpiecznik",
            "Najpierw kliknij:\n\n"
            "„Uzbrój jednorazową synchronizację początkową (10 min)”\n\n"
            "Przycisk uzbraja zapis natychmiast — nie trzeba zamykać okna ustawień.",
        )
        return

    xbmcgui.Dialog().notification(
        "SudoSync — przygotowanie",
        "Przygotowuję bezpieczny plan. Nie klikaj ponownie — za chwilę pojawi się okno potwierdzenia.",
        xbmcgui.NOTIFICATION_INFO,
        8000,
    )

    preview = run_dry_run(show_notification=False)
    init = preview.get("initialization") or {}
    if not init.get("base_client_id"):
        disarm_initial_write_guard()
        xbmcgui.Dialog().ok(
            "SudoSync — synchronizacja początkowa",
            "Najpierw wybierz urządzenie bazowe pierwszej synchronizacji.",
        )
        return

    cid = get_or_create_client_id()
    plan = (preview.get("planned_changes") or {}).get(cid) or []
    field_counts = {"playcount": 0, "lastplayed": 0, "userrating": 0, "resume": 0}
    for item in plan:
        changes = item.get("changes") or {}
        for field in field_counts:
            if field in changes:
                field_counts[field] += 1

    if not plan:
        disarm_initial_write_guard()
        xbmcgui.Dialog().ok(
            "SudoSync — synchronizacja początkowa",
            "Na tym Kodi nie ma obecnie żadnych bezpiecznych zmian do zastosowania. Bezpiecznik został rozbrojony.",
        )
        return

    base_label = init.get("base_client_name") or init.get("base_client_id")
    if not xbmcgui.Dialog().yesno(
        "SudoSync — UWAGA: zapis do biblioteki",
        "Urządzenie bazowe: {base}\n\n"
        "Ta operacja ZMIENI lokalną bibliotekę tego Kodi.\n\n"
        "Pozycji: {items}\nplaycount: {pc}\nlastplayed: {lp}\noceny: {ur}\nresume: {rs}\n\n"
        "Przed zapisem SudoSync utworzy pełny snapshot-backup na NAS. Niejednoznaczne pozycje są pomijane.\n\nKontynuować?".format(
            base=base_label, items=len(plan), pc=field_counts["playcount"], lp=field_counts["lastplayed"],
            ur=field_counts["userrating"], rs=field_counts["resume"]
        ),
        yeslabel="Dalej", nolabel="Anuluj",
    ):
        disarm_initial_write_guard()
        return

    if not xbmcgui.Dialog().yesno(
        "SudoSync — ostatnie potwierdzenie",
        "Synchronizacja bieżąca uruchomi się dopiero, gdy CAŁA inicjalizacja zostanie zakończona.\n\n"
        "Zastosować teraz tylko bezpieczny plan początkowy na TYM Kodi?",
        yeslabel="Zastosuj", nolabel="Anuluj",
    ):
        disarm_initial_write_guard()
        return

    xbmcgui.Dialog().notification(
        "SudoSync — synchronizacja trwa",
        "Zapis i weryfikacja mogą potrwać kilkadziesiąt sekund. Nie klikaj ponownie — poczekaj na komunikat końcowy.",
        xbmcgui.NOTIFICATION_INFO,
        10000,
    )

    result = apply_initial_sync(show_notification=True)
    if result.get("nothing_to_do"):
        xbmcgui.Dialog().ok("SudoSync", result.get("message"))
        return

    post = result.get("post_plan") or {}
    completed = result.get("initialization_completed", False)
    xbmcgui.Dialog().ok(
        "SudoSync — zakończono",
        "Zastosowano synchronizację początkową.\n\n"
        "Backup: {backup}\n\n"
        "Pozostały plan na tym Kodi: {left} pozycji.\n"
        "Stan całej inicjalizacji: {state}\n\n"
        "Po pełnym zakończeniu inicjalizacji synchronizacja bieżąca może uruchomić się automatycznie.".format(
            backup=result.get("backup_path", "—"), left=post.get("items", 0),
            state="ZAKOŃCZONA" if completed else "jeszcze niezakończona",
        ),
    )


def arm_initial_dialog():
    if not xbmcgui.Dialog().yesno(
        "SudoSync — uzbrojenie zapisu",
        "Uzbroić jednorazowy zapis synchronizacji początkowej na 10 minut?\n\n"
        "Samo uzbrojenie NICZEGO jeszcze nie zmienia w bibliotece. Zapis nastąpi dopiero po użyciu przycisku „Zastosuj synchronizację początkową na TYM Kodi” i dwóch kolejnych potwierdzeniach.",
        yeslabel="Uzbrój", nolabel="Anuluj",
    ):
        return
    arm_initial_write_guard()
    xbmcgui.Dialog().ok(
        "SudoSync — bezpiecznik uzbrojony",
        "Bezpiecznik jest uzbrojony na 10 minut.\n\n"
        "Możesz od razu, bez zamykania okna ustawień, kliknąć:\n\n"
        "„Zastosuj synchronizację początkową na TYM Kodi”.",
    )

def select_base_dialog():
    shared = read_shared_config()
    init = shared.get("initialization") or {}
    if init.get("completed"):
        xbmcgui.Dialog().ok(
            "SudoSync — urządzenie bazowe",
            "Pierwsza synchronizacja jest już zakończona. Zmiana urządzenia bazowego jest zablokowana.",
        )
        return
    current = init.get("base_client_name") or init.get("base_client_id")
    if current:
        # Core will distinguish THIS Kodi from another one. We intentionally do
        # not offer replacement here; replacement requires the dedicated clear.
        try:
            result = select_this_as_initial_base()
        except Exception as exc:
            xbmcgui.Dialog().ok("SudoSync — urządzenie bazowe", str(exc))
            return
        if result.get("already_selected"):
            xbmcgui.Dialog().ok(
                "SudoSync — urządzenie bazowe",
                "To Kodi jest już urządzeniem bazowym: {}.".format(result.get("client_name") or current),
            )
            return
    else:
        if not xbmcgui.Dialog().yesno(
            "SudoSync — urządzenie bazowe",
            "Ustawić TO Kodi jako urządzenie bazowe pierwszej synchronizacji?",
            yeslabel="Ustaw",
            nolabel="Anuluj",
        ):
            return
        result = select_this_as_initial_base()
        xbmcgui.Dialog().ok(
            "SudoSync",
            "Urządzenie bazowe ustawione: {}.".format(result.get("client_name") or result.get("client_id")),
        )


def reset_base_dialog():
    shared = read_shared_config()
    init = shared.get("initialization") or {}
    if init.get("completed"):
        xbmcgui.Dialog().ok(
            "SudoSync — urządzenie bazowe",
            "Pierwsza synchronizacja jest już zakończona. Zwykłe wyczyszczenie wyboru bazowego jest zablokowane.",
        )
        return
    if not init.get("base_client_id"):
        xbmcgui.Dialog().ok("SudoSync — urządzenie bazowe", "Nie ma obecnie wybranego urządzenia bazowego.")
        return
    if xbmcgui.Dialog().yesno(
        "SudoSync — urządzenie bazowe",
        "Obecne urządzenie bazowe: {}\n\nWyczyścić wybór, aby można było wskazać inne Kodi?".format(
            init.get("base_client_name") or init.get("base_client_id")
        ),
        yeslabel="Wyczyść",
        nolabel="Anuluj",
    ):
        clear_initial_base_selection()
        xbmcgui.Dialog().ok(
            "SudoSync",
            "Wybór urządzenia bazowego został wyczyszczony. Teraz użyj przycisku ustawienia bazy na właściwym Kodi.",
        )



def live_sync_dialog():
    settings = addon_settings()
    shared = read_shared_config(settings["base_path"])
    init = shared.get("initialization") or {}
    if not init.get("completed", False):
        xbmcgui.Dialog().ok(
            "SudoSync — synchronizacja bieżąca",
            "Najpierw trzeba zakończyć synchronizację początkową wszystkich Kodi.",
        )
        return
    if not settings.get("live_sync_enabled", True):
        xbmcgui.Dialog().ok(
            "SudoSync — synchronizacja bieżąca",
            "Synchronizacja bieżąca jest wyłączona w ustawieniach. Włącz ją i zatwierdź ustawienia przyciskiem OK.",
        )
        return
    xbmcgui.Dialog().notification(
        "SudoSync — synchronizacja LIVE",
        "Sprawdzam zmiany lokalne i pozostałe Kodi. Poczekaj na komunikat końcowy.",
        xbmcgui.NOTIFICATION_INFO,
        7000,
    )
    result = run_live_sync_cycle(show_notification=False, suppress_local_changes=False, notify_local_changes=False, receiver_notification_delay_ms=0)
    if result.get("busy"):
        xbmcgui.Dialog().ok("SudoSync", "Inny cykl synchronizacji już trwa na tym Kodi. Nie uruchamiaj go ponownie.")
        return
    if result.get("playing"):
        xbmcgui.Dialog().ok("SudoSync", "Trwa odtwarzanie wideo. Synchronizacja została odłożona i wykona się automatycznie po zatrzymaniu odtwarzania.")
        return
    if not result.get("active", True):
        xbmcgui.Dialog().ok("SudoSync", "Synchronizacja LIVE nie jest teraz aktywna: {}".format(result.get("reason") or "nieznany powód"))
        return
    if result.get("waiting"):
        xbmcgui.Dialog().ok(
            "SudoSync — oczekiwanie",
            "Synchronizacja LIVE ruszy automatycznie, gdy wszystkie znane Kodi będą miały wersję 0.4.\n\nOczekiwanie na: {}".format(
                ", ".join(result.get("waiting_for_clients") or [])
            ),
        )
        return
    xbmcgui.Dialog().ok(
        "SudoSync — synchronizacja LIVE",
        "Cykl zakończony.\n\nZastosowano z innych Kodi: {} pozycji.\nPozostały lokalny plan: {} pozycji.".format(
            result.get("applied_items", 0), (result.get("post_plan") or {}).get("items", 0)
        ),
    )

def update_dialog():
    result = check_for_update(show_notification=False, manual=True)
    if result.get("error"):
        xbmcgui.Dialog().ok(
            "SudoSync — aktualizacja",
            "Nie udało się sprawdzić aktualizacji:\n{}".format(result["error"]),
        )
        return
    if not result.get("available"):
        xbmcgui.Dialog().ok(
            "SudoSync — aktualizacja",
            "Masz najnowszą wersję dostępną w folderze .SudoSync.\n\nZainstalowana: {}".format(
                result.get("current_version", "—")
            ),
        )
        return
    if xbmcgui.Dialog().yesno(
        "SudoSync — aktualizacja",
        "Dostępna jest wersja {}.\n\nSudoSync może pobrać ją bezpośrednio z ukrytego folderu .SudoSync i zainstalować bez szukania ZIP-a. Zainstalować teraz?".format(
            result.get("version")
        ),
        yeslabel="Zainstaluj",
        nolabel="Anuluj",
    ):
        install_latest_update(show_dialogs=True)


def main():
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    try:
        if action == "collect":
            collect_and_write(show_notification=True)
        elif action in ("dryrun", "analyse", "analyze"):
            report = run_dry_run(show_notification=True)
            summary = report.get("summary") or {}
            init = report.get("initialization") or {}
            xbmcgui.Dialog().ok(
                "SudoSync — DRY RUN",
                "Analiza zakończona. Niczego nie zmieniono w bibliotece.\n\n"
                "Urządzenie bazowe: {base}\n"
                "Bezpieczne wspólne pozycje: {safe}\n"
                "Bezpieczne mirrory lokalny/NAS: {mir}\n"
                "Niejednoznaczne grupy (pominięte): {amb}\n"
                "Pozycje bez mocnego ID: {noid}\n"
                "Konflikty ocen: {ratings}".format(
                    base=init.get("base_client_name") or "NIE WYBRANO",
                    safe=summary.get("safe_shared_clusters", 0),
                    mir=summary.get("safe_mirror_clusters", 0),
                    amb=summary.get("ambiguous_clusters", 0),
                    noid=summary.get("records_without_strong_id", 0),
                    ratings=summary.get("rating_conflicts", 0),
                ),
            )
        elif action in ("arminitial", "armwrite"):
            arm_initial_dialog()
        elif action in ("applyinitial", "initialsync"):
            apply_initial_dialog()
        elif action in ("selectbase", "setbase"):
            select_base_dialog()
        elif action in ("resetbase", "clearbase"):
            reset_base_dialog()
        elif action in ("livesync", "synclive", "syncnow"):
            live_sync_dialog()
        elif action in ("checkupdate", "update"):
            update_dialog()
        elif action in ("installupdate", "selfupdate"):
            result = find_latest_update()
            if result.get("error"):
                raise IOError(result["error"])
            if not result.get("available"):
                xbmcgui.Dialog().ok("SudoSync — aktualizacja", "Brak nowszej wersji w .SudoSync.")
            elif xbmcgui.Dialog().yesno(
                "SudoSync — aktualizacja",
                "Zainstalować wersję {} bezpośrednio z .SudoSync?".format(result.get("version")),
                yeslabel="Zainstaluj",
                nolabel="Anuluj",
            ):
                install_latest_update(show_dialogs=True)
        elif action in ("assignids", "assign_ids"):
            from resources.lib.sudosync_core import assign_sudosync_ids
            if not addon_settings().get("sudosync_id_enabled"):
                xbmcgui.Dialog().ok(
                    "SudoSync",
                    "Funkcja jest wyłączona. Najpierw ją włącz i zatwierdź opcje przyciskiem OK."
                )
                return
            if not xbmcgui.Dialog().yesno(
                "SudoSync — własne identyfikatory",
                "Czy na pewno chcesz nadpisać lub utworzyć pliki .nfo dla WSZYSTKICH pozycji bez standardowego ID?\n\nOstrzeżenie: przy dużej bibliotece pierwsze wykonanie może zająć dłuższą chwilę. Kodi zostanie poinformowane o zmianach.",
                yeslabel="Tak, dopisz ID",
                nolabel="Anuluj"
            ):
                return
            xbmcgui.Dialog().notification(
                "SudoSync",
                "Przypisuję identyfikatory. Może to potrwać dłuższą chwilę...",
                xbmcgui.NOTIFICATION_INFO,
                10000
            )
            result = assign_sudosync_ids()
            xbmcgui.Dialog().ok(
                "SudoSync",
                "Zmodyfikowano lub utworzono:\nFilmy: {}\nOdcinki: {}".format(result.get("movies", 0), result.get("episodes", 0))
            )
        else:
            show_status()
    except Exception as exc:
        xbmcgui.Dialog().ok("SudoSync — błąd", str(exc))


if __name__ == "__main__":
    main()
