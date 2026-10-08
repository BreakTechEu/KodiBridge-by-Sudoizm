# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------

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
        update_text = "DostÄ™pna: {} ({})".format(upd.get("version"), upd.get("filename"))
    elif upd.get("error"):
        update_text = "Nie udaĹ‚o siÄ™ sprawdziÄ‡: {}".format(upd.get("error"))
    else:
        update_text = "Brak nowszej wersji (zainstalowana {})".format(upd.get("current_version", "â€”"))

    try:
        shared = sync_initial_base_selection(show_notification=False)
        init = shared.get("initialization") or {}
        base_name = init.get("base_client_name") or "â€”"
        init_state = "zakoĹ„czona" if init.get("completed") else "niezakoĹ„czona"
    except Exception as exc:
        base_name = "bĹ‚Ä…d: {}".format(exc)
        init_state = "â€”"

    dry = status.get("dry_run_summary") or {}
    plan = status.get("dry_run_local_plan") or {}
    dry_text = (
        "Ostatnia analiza: {when}\n"
        "Bezpieczne wspĂłlne pozycje: {safe}\n"
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
        when=status.get("last_dry_run_at", "â€”"),
        safe=dry.get("safe_shared_clusters", "â€”"),
        mir=dry.get("safe_mirror_clusters", 0),
        amb=dry.get("ambiguous_clusters", "â€”"),
        noid=dry.get("records_without_strong_id", "â€”"),
        ratings=dry.get("rating_conflicts", "â€”"),
        items=plan.get("items", 0),
        pc=plan.get("playcount", 0),
        lp=plan.get("lastplayed", 0),
        ur=plan.get("userrating", 0),
        rs=plan.get("resume", 0),
        report=status.get("dry_run_report_path", "â€”"),
    )

    live_enabled = settings.get("live_sync_enabled", True)
    waiting = status.get("live_waiting_clients") or []
    live_plan = status.get("live_local_plan") or {}
    if init_state == "zakoĹ„czona":
        live_state = "WĹÄ„CZONA" if live_enabled else "WYĹÄ„CZONA"
    else:
        live_state = "OCZEKUJE NA ZAKOĹCZENIE INICJALIZACJI"
    live_text = (
        "Synchronizacja bieĹĽÄ…ca: {state}\n"
        "Odpytywanie innych Kodi: co {poll} s\n"
        "Ostatni cykl LIVE: {when}\n"
        "Ostatnio odebrano: {applied} pozycji\n"
        "Ostatnio wysĹ‚ano lokalnie: {published} pozycji\n"
        "BieĹĽÄ…cy plan lokalny: {items} pozycji\n"
        "Ostatnia kontrola lokalna: {probe_when}\n"
        "Kontrola wykryĹ‚a zmianÄ™: {probe_changed} ({probe_items} pozycji)\n"
        "Oczekiwanie na aktualizacjÄ™ klientĂłw: {waiting}\n"
        "Raport LIVE: {report}"
    ).format(
        state=live_state,
        poll=settings.get("live_poll_seconds", 60),
        when=status.get("last_live_sync_at") or status.get("last_live_plan_at") or "â€”",
        applied=status.get("last_live_applied_items", 0),
        published=status.get("last_live_published_items", 0),
        items=live_plan.get("items", 0),
        probe_when=status.get("last_local_probe_at") or "â€”",
        probe_changed="tak" if status.get("last_local_probe_changed") else "nie",
        probe_items=status.get("last_local_probe_changed_items", 0),
        waiting=", ".join(waiting) if waiting else "nie",
        report=status.get("last_live_report_path", "â€”"),
    )

    import xbmcaddon
    addon_version = xbmcaddon.Addon("service.sudosync").getAddonInfo("version")

    text = (
        "Tryb: SudoSync {version} â€” LIVE + powiadomienia zmian i ochrona nowszego odtwarzania\n\n"
        "UrzÄ…dzenie: {name}\n"
        "Client ID: {cid}\n"
        "Kodi: {kodi} ({platform})\n"
        "JSON-RPC: {rpc}\n\n"
        "UrzÄ…dzenie bazowe inicjalizacji: {base}\n"
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
        cid=status.get("client_id", "â€”"),
        kodi=kodi.get("version", "â€”"),
        platform=kodi.get("platform", "â€”"),
        rpc=rpcv or "â€”",
        base=base_name,
        init_state=init_state,
        when=status.get("last_snapshot_at", "â€”"),
        movies=counts.get("movies", 0),
        episodes=counts.get("episodes", 0),
        path=status.get("remote_path", "â€”"),
        dry=dry_text,
        live=live_text,
        update=update_text,
    )
    xbmcgui.Dialog().textviewer("SudoSync â€” status", text)


def apply_initial_dialog():
    guard = initial_write_guard_status()
    if not guard.get("armed"):
        xbmcgui.Dialog().ok(
            "SudoSync â€” bezpiecznik",
            "Najpierw kliknij:\n\n"
            "â€žUzbrĂłj jednorazowÄ… synchronizacjÄ™ poczÄ…tkowÄ… (10 min)â€ť\n\n"
            "Przycisk uzbraja zapis natychmiast â€” nie trzeba zamykaÄ‡ okna ustawieĹ„.",
        )
        return

    xbmcgui.Dialog().notification(
        "SudoSync â€” przygotowanie",
        "PrzygotowujÄ™ bezpieczny plan. Nie klikaj ponownie â€” za chwilÄ™ pojawi siÄ™ okno potwierdzenia.",
        xbmcgui.NOTIFICATION_INFO,
        8000,
    )

    preview = run_dry_run(show_notification=False)
    init = preview.get("initialization") or {}
    if not init.get("base_client_id"):
        disarm_initial_write_guard()
        xbmcgui.Dialog().ok(
            "SudoSync â€” synchronizacja poczÄ…tkowa",
            "Najpierw wybierz urzÄ…dzenie bazowe pierwszej synchronizacji.",
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
            "SudoSync â€” synchronizacja poczÄ…tkowa",
            "Na tym Kodi nie ma obecnie ĹĽadnych bezpiecznych zmian do zastosowania. Bezpiecznik zostaĹ‚ rozbrojony.",
        )
        return

    base_label = init.get("base_client_name") or init.get("base_client_id")
    if not xbmcgui.Dialog().yesno(
        "SudoSync â€” UWAGA: zapis do biblioteki",
        "UrzÄ…dzenie bazowe: {base}\n\n"
        "Ta operacja ZMIENI lokalnÄ… bibliotekÄ™ tego Kodi.\n\n"
        "Pozycji: {items}\nplaycount: {pc}\nlastplayed: {lp}\noceny: {ur}\nresume: {rs}\n\n"
        "Przed zapisem SudoSync utworzy peĹ‚ny snapshot-backup na NAS. Niejednoznaczne pozycje sÄ… pomijane.\n\nKontynuowaÄ‡?".format(
            base=base_label, items=len(plan), pc=field_counts["playcount"], lp=field_counts["lastplayed"],
            ur=field_counts["userrating"], rs=field_counts["resume"]
        ),
        yeslabel="Dalej", nolabel="Anuluj",
    ):
        disarm_initial_write_guard()
        return

    if not xbmcgui.Dialog().yesno(
        "SudoSync â€” ostatnie potwierdzenie",
        "Synchronizacja bieĹĽÄ…ca uruchomi siÄ™ dopiero, gdy CAĹA inicjalizacja zostanie zakoĹ„czona.\n\n"
        "ZastosowaÄ‡ teraz tylko bezpieczny plan poczÄ…tkowy na TYM Kodi?",
        yeslabel="Zastosuj", nolabel="Anuluj",
    ):
        disarm_initial_write_guard()
        return

    xbmcgui.Dialog().notification(
        "SudoSync â€” synchronizacja trwa",
        "Zapis i weryfikacja mogÄ… potrwaÄ‡ kilkadziesiÄ…t sekund. Nie klikaj ponownie â€” poczekaj na komunikat koĹ„cowy.",
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
        "SudoSync â€” zakoĹ„czono",
        "Zastosowano synchronizacjÄ™ poczÄ…tkowÄ….\n\n"
        "Backup: {backup}\n\n"
        "PozostaĹ‚y plan na tym Kodi: {left} pozycji.\n"
        "Stan caĹ‚ej inicjalizacji: {state}\n\n"
        "Po peĹ‚nym zakoĹ„czeniu inicjalizacji synchronizacja bieĹĽÄ…ca moĹĽe uruchomiÄ‡ siÄ™ automatycznie.".format(
            backup=result.get("backup_path", "â€”"), left=post.get("items", 0),
            state="ZAKOĹCZONA" if completed else "jeszcze niezakoĹ„czona",
        ),
    )


def arm_initial_dialog():
    if not xbmcgui.Dialog().yesno(
        "SudoSync â€” uzbrojenie zapisu",
        "UzbroiÄ‡ jednorazowy zapis synchronizacji poczÄ…tkowej na 10 minut?\n\n"
        "Samo uzbrojenie NICZEGO jeszcze nie zmienia w bibliotece. Zapis nastÄ…pi dopiero po uĹĽyciu przycisku â€žZastosuj synchronizacjÄ™ poczÄ…tkowÄ… na TYM Kodiâ€ť i dwĂłch kolejnych potwierdzeniach.",
        yeslabel="UzbrĂłj", nolabel="Anuluj",
    ):
        return
    arm_initial_write_guard()
    xbmcgui.Dialog().ok(
        "SudoSync â€” bezpiecznik uzbrojony",
        "Bezpiecznik jest uzbrojony na 10 minut.\n\n"
        "MoĹĽesz od razu, bez zamykania okna ustawieĹ„, kliknÄ…Ä‡:\n\n"
        "â€žZastosuj synchronizacjÄ™ poczÄ…tkowÄ… na TYM Kodiâ€ť.",
    )

def select_base_dialog():
    shared = read_shared_config()
    init = shared.get("initialization") or {}
    if init.get("completed"):
        xbmcgui.Dialog().ok(
            "SudoSync â€” urzÄ…dzenie bazowe",
            "Pierwsza synchronizacja jest juĹĽ zakoĹ„czona. Zmiana urzÄ…dzenia bazowego jest zablokowana.",
        )
        return
    current = init.get("base_client_name") or init.get("base_client_id")
    if current:
        # Core will distinguish THIS Kodi from another one. We intentionally do
        # not offer replacement here; replacement requires the dedicated clear.
        try:
            result = select_this_as_initial_base()
        except Exception as exc:
            xbmcgui.Dialog().ok("SudoSync â€” urzÄ…dzenie bazowe", str(exc))
            return
        if result.get("already_selected"):
            xbmcgui.Dialog().ok(
                "SudoSync â€” urzÄ…dzenie bazowe",
                "To Kodi jest juĹĽ urzÄ…dzeniem bazowym: {}.".format(result.get("client_name") or current),
            )
            return
    else:
        if not xbmcgui.Dialog().yesno(
            "SudoSync â€” urzÄ…dzenie bazowe",
            "UstawiÄ‡ TO Kodi jako urzÄ…dzenie bazowe pierwszej synchronizacji?",
            yeslabel="Ustaw",
            nolabel="Anuluj",
        ):
            return
        result = select_this_as_initial_base()
        xbmcgui.Dialog().ok(
            "SudoSync",
            "UrzÄ…dzenie bazowe ustawione: {}.".format(result.get("client_name") or result.get("client_id")),
        )


def reset_base_dialog():
    shared = read_shared_config()
    init = shared.get("initialization") or {}
    if init.get("completed"):
        xbmcgui.Dialog().ok(
            "SudoSync â€” urzÄ…dzenie bazowe",
            "Pierwsza synchronizacja jest juĹĽ zakoĹ„czona. ZwykĹ‚e wyczyszczenie wyboru bazowego jest zablokowane.",
        )
        return
    if not init.get("base_client_id"):
        xbmcgui.Dialog().ok("SudoSync â€” urzÄ…dzenie bazowe", "Nie ma obecnie wybranego urzÄ…dzenia bazowego.")
        return
    if xbmcgui.Dialog().yesno(
        "SudoSync â€” urzÄ…dzenie bazowe",
        "Obecne urzÄ…dzenie bazowe: {}\n\nWyczyĹ›ciÄ‡ wybĂłr, aby moĹĽna byĹ‚o wskazaÄ‡ inne Kodi?".format(
            init.get("base_client_name") or init.get("base_client_id")
        ),
        yeslabel="WyczyĹ›Ä‡",
        nolabel="Anuluj",
    ):
        clear_initial_base_selection()
        xbmcgui.Dialog().ok(
            "SudoSync",
            "WybĂłr urzÄ…dzenia bazowego zostaĹ‚ wyczyszczony. Teraz uĹĽyj przycisku ustawienia bazy na wĹ‚aĹ›ciwym Kodi.",
        )



def live_sync_dialog():
    settings = addon_settings()
    shared = read_shared_config(settings["base_path"])
    init = shared.get("initialization") or {}
    if not init.get("completed", False):
        xbmcgui.Dialog().ok(
            "SudoSync â€” synchronizacja bieĹĽÄ…ca",
            "Najpierw trzeba zakoĹ„czyÄ‡ synchronizacjÄ™ poczÄ…tkowÄ… wszystkich Kodi.",
        )
        return
    if not settings.get("live_sync_enabled", True):
        xbmcgui.Dialog().ok(
            "SudoSync â€” synchronizacja bieĹĽÄ…ca",
            "Synchronizacja bieĹĽÄ…ca jest wyĹ‚Ä…czona w ustawieniach. WĹ‚Ä…cz jÄ… i zatwierdĹş ustawienia przyciskiem OK.",
        )
        return
    xbmcgui.Dialog().notification(
        "SudoSync â€” synchronizacja LIVE",
        "Sprawdzam zmiany lokalne i pozostaĹ‚e Kodi. Poczekaj na komunikat koĹ„cowy.",
        xbmcgui.NOTIFICATION_INFO,
        7000,
    )
    result = run_live_sync_cycle(show_notification=False, suppress_local_changes=False, notify_local_changes=False, receiver_notification_delay_ms=0)
    if result.get("busy"):
        xbmcgui.Dialog().ok("SudoSync", "Inny cykl synchronizacji juĹĽ trwa na tym Kodi. Nie uruchamiaj go ponownie.")
        return
    if result.get("playing"):
        xbmcgui.Dialog().ok("SudoSync", "Trwa odtwarzanie wideo. Synchronizacja zostaĹ‚a odĹ‚oĹĽona i wykona siÄ™ automatycznie po zatrzymaniu odtwarzania.")
        return
    if not result.get("active", True):
        xbmcgui.Dialog().ok("SudoSync", "Synchronizacja LIVE nie jest teraz aktywna: {}".format(result.get("reason") or "nieznany powĂłd"))
        return
    if result.get("waiting"):
        xbmcgui.Dialog().ok(
            "SudoSync â€” oczekiwanie",
            "Synchronizacja LIVE ruszy automatycznie, gdy wszystkie znane Kodi bÄ™dÄ… miaĹ‚y wersjÄ™ 0.4.\n\nOczekiwanie na: {}".format(
                ", ".join(result.get("waiting_for_clients") or [])
            ),
        )
        return
    xbmcgui.Dialog().ok(
        "SudoSync â€” synchronizacja LIVE",
        "Cykl zakoĹ„czony.\n\nZastosowano z innych Kodi: {} pozycji.\nPozostaĹ‚y lokalny plan: {} pozycji.".format(
            result.get("applied_items", 0), (result.get("post_plan") or {}).get("items", 0)
        ),
    )

def update_dialog():
    result = check_for_update(show_notification=False, manual=True)
    if result.get("error"):
        xbmcgui.Dialog().ok(
            "SudoSync â€” aktualizacja",
            "Nie udaĹ‚o siÄ™ sprawdziÄ‡ aktualizacji:\n{}".format(result["error"]),
        )
        return
    if not result.get("available"):
        xbmcgui.Dialog().ok(
            "SudoSync â€” aktualizacja",
            "Masz najnowszÄ… wersjÄ™ dostÄ™pnÄ… w folderze .SudoSync.\n\nZainstalowana: {}".format(
                result.get("current_version", "â€”")
            ),
        )
        return
    if xbmcgui.Dialog().yesno(
        "SudoSync â€” aktualizacja",
        "DostÄ™pna jest wersja {}.\n\nSudoSync moĹĽe pobraÄ‡ jÄ… bezpoĹ›rednio z ukrytego folderu .SudoSync i zainstalowaÄ‡ bez szukania ZIP-a. ZainstalowaÄ‡ teraz?".format(
            result.get("version")
        ),
        yeslabel="Zainstaluj",
        nolabel="Anuluj",
    ):
        install_latest_update(show_dialogs=True)


def main():
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    try:
        if action == "bootstrap":
            from resources.lib.sudosync_bootstrap import run_bootstrap
            if run_bootstrap():
                xbmcgui.Dialog().ok("SudoSync", "Konfigurator zakoĹ„czony pomyĹ›lnie. Ĺšrodowisko gotowe.")
        elif action == "collect":
            collect_and_write(show_notification=True)
        elif action in ("dryrun", "analyse", "analyze"):
            report = run_dry_run(show_notification=True)
            summary = report.get("summary") or {}
            init = report.get("initialization") or {}
            xbmcgui.Dialog().ok(
                "SudoSync â€” DRY RUN",
                "Analiza zakoĹ„czona. Niczego nie zmieniono w bibliotece.\n\n"
                "UrzÄ…dzenie bazowe: {base}\n"
                "Bezpieczne wspĂłlne pozycje: {safe}\n"
                "Bezpieczne mirrory lokalny/NAS: {mir}\n"
                "Niejednoznaczne grupy (pominiÄ™te): {amb}\n"
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
                xbmcgui.Dialog().ok("SudoSync â€” aktualizacja", "Brak nowszej wersji w .SudoSync.")
            elif xbmcgui.Dialog().yesno(
                "SudoSync â€” aktualizacja",
                "ZainstalowaÄ‡ wersjÄ™ {} bezpoĹ›rednio z .SudoSync?".format(result.get("version")),
                yeslabel="Zainstaluj",
                nolabel="Anuluj",
            ):
                install_latest_update(show_dialogs=True)
        elif action in ("assignids", "assign_ids"):
            from resources.lib.sudosync_core import assign_sudosync_ids, preview_sudosync_ids
            if not addon_settings().get("sudosync_id_enabled"):
                xbmcgui.Dialog().ok(
                    "SudoSync",
                    "Funkcja jest wyĹ‚Ä…czona. Najpierw jÄ… wĹ‚Ä…cz i zatwierdĹş opcje przyciskiem OK."
                )
                return
                
            preview = preview_sudosync_ids()
            if not preview:
                xbmcgui.Dialog().ok("SudoSync", "Nie znaleziono ĹĽadnych plikĂłw wymagajÄ…cych przypisania ID.")
                return
                
            if not xbmcgui.Dialog().yesno(
                "SudoSync - wĹ‚asne identyfikatory",
                "Znaleziono {} plikĂłw do zmiany (w tym np. {}).\n\nCzy na pewno chcesz nadpisaÄ‡ pliki .nfo?".format(
                    len(preview), preview[0].get("title")
                ),
                yeslabel="Tak, dopisz ID",
                nolabel="Anuluj"
            ):
                return
            xbmcgui.Dialog().notification(
                "SudoSync",
                "PrzypisujÄ™ identyfikatory. MoĹĽe to potrwaÄ‡ dĹ‚uĹĽszÄ… chwilÄ™...",
                xbmcgui.NOTIFICATION_INFO,
                10000
            )
            result = assign_sudosync_ids()
            xbmcgui.Dialog().ok(
                "SudoSync",
                "Raport z operacji zostaĹ‚ zapisany:\n{}\n\nZmodyfikowano lub utworzono:\nFilmy: {}\nOdcinki: {}".format(result.get("manifest_path"), result.get("movies", 0), result.get("episodes", 0))
            )
        elif action == "rollbackids":
            from resources.lib.sudosync_core import rollback_sudosync_ids
            if not xbmcgui.Dialog().yesno(
                "SudoSync - cofniÄ™cie zmian",
                "Czy wycofaÄ‡ ostatnie zmiany ID przywracajÄ…c kopie z backupu .bak?",
                yeslabel="Tak, wycofaj",
                nolabel="Anuluj"
            ):
                return
            res = rollback_sudosync_ids()
            xbmcgui.Dialog().ok("SudoSync", "PrzywrĂłcono: {} plikĂłw. BĹ‚Ä™dy: {}".format(res.get("rolled_back", 0), res.get("errors", 0)))
        else:
            show_status()
    except Exception as exc:
        xbmcgui.Dialog().ok("SudoSync â€” bĹ‚Ä…d", str(exc))


if __name__ == "__main__":
    main()
