# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------

import os
import sys

import xbmcgui

from resources.lib.sudosync_core import (
    assign_sudosync_ids,
    find_github_update,
    install_github_update,
    install_update_from_zip_path,
    install_v2_log_bridge,
    log,
    mask_vfs_credentials,
    preview_sudosync_ids,
    read_local_status,
    addon_settings,
)


def _get_localized_string(string_id, fallback=""):
    try:
        import xbmcaddon
        val = xbmcaddon.Addon("service.sudosync").getLocalizedString(string_id)
        if isinstance(val, str) and val.strip():
            return val
    except Exception:
        pass
    return fallback


def show_status():
    status = read_local_status()
    settings = addon_settings()
    kodi = status.get("kodi") or {}
    counts = status.get("counts") or {}
    rpcv = kodi.get("jsonrpc")
    if isinstance(rpcv, dict):
        rpcv = "{}.{}.{}".format(rpcv.get("major", 0), rpcv.get("minor", 0), rpcv.get("patch", 0))

    import xbmcaddon
    addon_version = xbmcaddon.Addon("service.sudosync").getAddonInfo("version")
    gh_upd = status.get("last_github_update_result") or {}
    gh_when = status.get("last_github_update_check_at") or "nie sprawdzano"
    if gh_upd.get("available"):
        update_text = "Dostępna na GitHub: {} (sprawdzono: {})".format(gh_upd.get("version"), gh_when)
    elif gh_upd.get("error"):
        update_text = "Błąd sprawdzenia GitHub ({}): {}".format(gh_when, gh_upd.get("error"))
    else:
        update_text = "Brak nowszej wersji (zainstalowana {}, sprawdzono: {})".format(addon_version, gh_when)

    live_enabled = settings.get("live_sync_enabled", True)
    live_text = (
        "Synchronizacja bieżąca: {state}\n"
        "Odpytywanie innych Kodi: co {poll} s\n"
        "Ostatni cykl: {when}\n"
        "Ostatnio odebrano: {received}\n"
        "Ostatni błąd: {error}"
    ).format(
        state="WŁĄCZONA" if live_enabled else "WYŁĄCZONA",
        poll=settings.get("live_poll_seconds", 60),
        when=status.get("last_live_sync_at") or "—",
        received=(
            "{} pozycji ({})".format(status.get("last_live_received_items"), status.get("last_live_received_at") or "—")
            if status.get("last_live_received_items") else "—"
        ),
        error=mask_vfs_credentials(status.get("last_live_error") or "brak"),
    )

    import xbmcaddon
    addon_version = xbmcaddon.Addon("service.sudosync").getAddonInfo("version")
    try:
        from resources.lib.sudosync_core import get_network_sudosync_id_last_number
        last_sudosync_num = get_network_sudosync_id_last_number(settings.get("base_path"))
    except Exception:
        last_sudosync_num = int(status.get("sudosync_id_last_number") or settings.get("sudosync_id_last_number") or 0)
    sudosync_id_state = "WŁĄCZONE (prefiks: {}, ostatni użyty numer: {})".format(
        settings.get("sudosync_id_prefix") or "NAS",
        last_sudosync_num,
    ) if settings.get("sudosync_id_enabled") else "WYŁĄCZONE (ostatni użyty numer w sieci: {})".format(last_sudosync_num)

    coverage_text = _coverage_text(status.get("kodibridge_id_coverage"))
    text = (
        "KodiBridge {version}\n\n"
        "Urządzenie: {name}\n"
        "Client ID: {cid}\n"
        "Kodi: {kodi} ({platform})\n"
        "JSON-RPC: {rpc}\n"
        "KodiBridge ID: {sid_state}\n"
        "{coverage}\n\n"
        "Ostatni snapshot: {when}\n"
        "Filmy: {movies}\n"
        "Odcinki: {episodes}\n\n"
        "Snapshot: {path}\n\n"
        "{live}\n\n"
        "Aktualizacje: {update}"
    ).format(
        version=addon_version,
        name=status.get("client_name", "Kodi"),
        cid=status.get("client_id", "—"),
        kodi=kodi.get("version", "—"),
        platform=kodi.get("platform", "—"),
        rpc=rpcv or "—",
        sid_state=sudosync_id_state,
        coverage=coverage_text,
        when=status.get("last_snapshot_at", "—"),
        movies=counts.get("movies", 0),
        episodes=counts.get("episodes", 0),
        path=mask_vfs_credentials(status.get("remote_path", "—")),
        live=live_text,
        update=update_text,
    )
    xbmcgui.Dialog().textviewer("KodiBridge — status", text)


def update_dialog():
    import os
    import time
    from resources.lib.sudosync_core import _addon_profile_path, _write_local_json, utc_now

    result = find_github_update()
    local_status = read_local_status()
    local_status["last_github_update_check_epoch"] = time.time()
    local_status["last_github_update_check_at"] = utc_now()
    local_status["last_github_update_result"] = {
        "available": bool(result.get("available")),
        "version": str(result.get("version") or ""),
        "current_version": str(result.get("current_version") or ""),
        "error": str(result.get("error") or ""),
    }
    _write_local_json(os.path.join(_addon_profile_path(), "status.json"), local_status)

    if result.get("error"):
        xbmcgui.Dialog().ok(
            "KodiBridge — aktualizacja (GitHub)",
            "Nie udało się sprawdzić aktualizacji na GitHub Releases:\n{}\n\n"
            "Możesz użyć opcji instalacji ze wskazanego pliku ZIP.".format(result["error"]),
        )
        return
    if not result.get("available"):
        xbmcgui.Dialog().ok(
            "KodiBridge — aktualizacja (GitHub)",
            "Masz najnowszą wersję dostępną w GitHub Releases.\n\nZainstalowana: {}".format(
                result.get("current_version", "—")
            ),
        )
        return
    if xbmcgui.Dialog().yesno(
        "KodiBridge — dostępna aktualizacja",
        "Na GitHub Releases dostępna jest nowa wersja KodiBridge {} (obecnie zainstalowana: {}).\n\n"
        "Pobrać i zainstalować aktualizację teraz?".format(
            result.get("version"), result.get("current_version", "—")
        ),
        yeslabel="Zainstaluj",
        nolabel="Anuluj",
    ):
        install_github_update(update_info=result, show_dialogs=True)


def install_local_zip_dialog():
    zip_path = xbmcgui.Dialog().browse(
        1, "Wybierz pakiet aktualizacji KodiBridge (.zip)", "files", ".zip"
    )
    if not zip_path:
        return
    install_update_from_zip_path(zip_path, show_dialogs=True)


def _coverage_text(coverage):
    """Describe the KodiBridge ID coverage of this Kodi's library for the status window."""
    if not isinstance(coverage, dict):
        return "Pokrycie KodiBridge ID: — (pojawi się po pierwszym cyklu synchronizacji)"
    movie = coverage.get("movie") or {}
    episode = coverage.get("episode") or {}
    lines = ["Pokrycie KodiBridge ID: filmy {}/{}, odcinki {}/{} (sprawdzono: {})".format(
        movie.get("with_id", 0), movie.get("total", 0),
        episode.get("with_id", 0), episode.get("total", 0),
        coverage.get("checked_at") or "—",
    )]
    problems = []
    for key, label in (("missing", "bez ID"), ("duplicates", "zdublowane ID"),
                       ("ambiguous", "wspólne ID zewnętrzne bez KodiBridge ID")):
        count = int(movie.get(key) or 0) + int(episode.get(key) or 0)
        if count:
            problems.append("{}: {}".format(label, count))
    if coverage.get("unreadable_nfo"):
        problems.append("nieczytelne .nfo: {}".format(coverage["unreadable_nfo"]))
    if problems:
        lines.append("UWAGA – " + ", ".join(problems) + " (przykłady w kodi.log: „KodiBridge ID coverage”)")
        if int(movie.get("duplicates") or 0) + int(episode.get("duplicates") or 0):
            lines.append("Zdublowane ID naprawia przycisk „Napraw zdublowane KodiBridge ID” w ustawieniach.")
    else:
        lines.append("Wszystkie pozycje mają KodiBridge ID.")
    return "\n".join(lines)


# Number of .nfo files changed by the current settings action (assign or roll
# back KodiBridge IDs); the service re-reads NFO identifiers only if it is > 0.
_NFO_CHANGES = {"count": 0}


def _note_nfo_changes(result, keys=("movies", "episodes")):
    if not isinstance(result, dict):
        return
    try:
        _NFO_CHANGES["count"] += sum(int(result.get(key) or 0) for key in keys)
    except (TypeError, ValueError):
        _NFO_CHANGES["count"] += 1  # unknown amount: assume something changed


def _flush_nfo_refresh():
    """Ask the service to re-read .nfo files now, before a dialog waits for the user."""
    if _NFO_CHANGES["count"] > 0:
        from resources.lib.sudosync_core import request_nfo_cache_refresh
        request_nfo_cache_refresh()
        _NFO_CHANGES["count"] = 0


def repair_ids_dialog():
    """Settings action: give new KodiBridge IDs to items that share one ID."""
    from resources.lib.sudosync_core import repair_duplicate_sudosync_ids

    if not addon_settings().get("sudosync_id_enabled"):
        xbmcgui.Dialog().ok("KodiBridge", "Funkcja jest wyłączona. Najpierw ją włącz i zatwierdź opcje przyciskiem OK.")
        return
    if not xbmcgui.Dialog().yesno(
        "KodiBridge — zdublowane ID",
        "Pozycje biblioteki, które mają to samo KodiBridge ID, dostaną nowe numery. "
        "Jedna pozycja z każdej grupy zachowuje numer (ta, którą zna synchronizacja). "
        "Zmienione pliki .nfo dostają kopię .repair.bak, a zmianę można wycofać.\n"
        "Odczyt biblioteki może potrwać kilka minut. Kontynuować?",
        yeslabel="Napraw",
        nolabel="Anuluj",
    ):
        return

    import xbmc
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    heading = "KodiBridge — naprawa zdublowanych ID"

    def is_canceled():
        try:
            if dialog.iscanceled():
                return True
        except Exception:
            pass
        try:
            return monitor.abortRequested() is True
        except Exception:
            return False

    def progress(current, total, label=""):
        if is_canceled():
            return
        pct = int(min(100, max(0, current * 100 // max(1, total))))
        dialog.update(pct, "Nadaję nowe numery ({}/{})\n{}".format(current, total, label))

    dialog.create(heading, "Odczyt biblioteki i plików .nfo…")
    try:
        result = repair_duplicate_sudosync_ids(progress_callback=progress, is_canceled=is_canceled)
    finally:
        try:
            dialog.close()
        except Exception:
            pass
    _note_nfo_changes(result, keys=("renumbered",))
    _flush_nfo_refresh()

    if result.get("busy"):
        xbmcgui.Dialog().ok("KodiBridge", "Przypisywanie KodiBridge ID jest już wykonywane w tle. Poczekaj na zakończenie i spróbuj ponownie.")
        return
    if not result.get("groups"):
        xbmcgui.Dialog().ok("KodiBridge", "W bibliotece tego Kodi nie ma zdublowanych KodiBridge ID.")
        return

    lines = [
        "Grupy z tym samym ID: {}. Nadano nowe numery: {}. Pominięte: {}.".format(
            result.get("groups", 0), result.get("renumbered", 0), len(result.get("errors") or [])),
    ]
    if result.get("cancelled"):
        lines.append("Przerwano na żądanie; pozostałe pozycje bez zmian.")
    if result.get("fatal_error"):
        lines.append("Przerwano: {}".format(mask_vfs_credentials(result["fatal_error"])))
    if result.get("changes"):
        lines += ["", "Nowe numery:"] + ["  " + line for line in result["changes"]]
    if result.get("kept"):
        lines += ["", "Zachowały numer:"] + ["  " + line for line in result["kept"]]
    if result.get("errors"):
        lines += ["", "Pominięte:"] + ["  " + mask_vfs_credentials(line) for line in result["errors"]]
    lines += ["", "Raport: {}. Zmianę wycofuje przycisk „Wycofaj ostatnio przypisane ID”.".format(
        os.path.basename(result.get("manifest_path") or "") or "repair_ids_manifest.json")]
    xbmcgui.Dialog().textviewer(heading, "\n".join(lines))


def rollback_ids_dialog():
    """Settings action: undo the last KodiBridge ID assignment or duplicate repair."""
    from resources.lib.sudosync_core import (
        latest_rollback_manifests,
        publish_kodibridge_id_change,
        renumbered_pairs_from_manifest,
        rollback_sudosync_ids,
    )

    manifests = latest_rollback_manifests()
    if not manifests:
        xbmcgui.Dialog().ok("KodiBridge", "Brak zapisanej operacji do wycofania.")
        return
    names = {"assign": "przypisanie KodiBridge ID", "repair": "naprawę zdublowanych ID"}
    kind, path, stamp = manifests[0]
    if len(manifests) > 1:
        choice = xbmcgui.Dialog().select(
            "KodiBridge — co wycofać?",
            ["Ostatnie {} ({})".format(names[k], s[:16].replace("T", " ") or "?") for k, _p, s in manifests],
        )
        if choice < 0:
            return
        kind, path, stamp = manifests[choice]
    if not xbmcgui.Dialog().yesno(
        "KodiBridge - cofnięcie zmian",
        "Czy wycofać ostatnie {}, przywracając pliki .nfo z kopii zapasowych?".format(names[kind]),
        yeslabel="Tak, wycofaj",
        nolabel="Anuluj",
    ):
        return
    res = rollback_sudosync_ids(manifest_path=path)
    _note_nfo_changes(res, keys=("rolled_back", "restored", "deleted"))
    if res.get("rolled_back"):
        # Other Kodi devices keep KodiBridge IDs read from .nfo files in a cache;
        # a restored file may carry an older ID, so they must read it again.
        reverse = [(new, old) for old, new in renumbered_pairs_from_manifest(path)] if kind == "repair" else []
        try:
            publish_kodibridge_id_change(renumbered=reverse)
        except Exception as exc:
            log("Cannot announce the KodiBridge ID rollback: {}".format(exc))
    _flush_nfo_refresh()
    message = "Przywrócono: {} plików. Błędy: {}".format(res.get("rolled_back", 0), res.get("errors", 0))
    if res.get("already_rolled_back"):
        message += "\nWycofane już wcześniej: {}".format(res["already_rolled_back"])
    xbmcgui.Dialog().ok("KodiBridge", message)


def main():
    install_v2_log_bridge()
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    try:
        if action == "bootstrap":
            from resources.lib.sudosync_bootstrap import run_bootstrap
            if run_bootstrap(force_select_path=True):
                xbmcgui.Dialog().ok("KodiBridge", "Konfigurator zakończony pomyślnie. Środowisko gotowe.")
        elif action in ("checkupdate", "update"):
            update_dialog()
        elif action in ("installlocalzip", "localupdate"):
            install_local_zip_dialog()
        elif action in ("installupdate", "selfupdate"):
            result = find_github_update()
            if result.get("error"):
                raise IOError(result["error"])
            if not result.get("available"):
                xbmcgui.Dialog().ok("KodiBridge — aktualizacja", "Brak nowszej wersji na GitHub Releases.")
            elif xbmcgui.Dialog().yesno(
                "KodiBridge — aktualizacja",
                "Zainstalować wersję {} z GitHub Releases?".format(result.get("version")),
                yeslabel="Zainstaluj",
                nolabel="Anuluj",
            ):
                install_github_update(update_info=result, show_dialogs=True)
        elif action in ("resolveconflicts", "conflicts"):
            from resources.lib.sudosync_conflicts import request_resolution
            request_resolution()
            xbmcgui.Dialog().notification(
                "KodiBridge",
                "Zlecono sprawdzenie konfliktów. Postęp w rogu ekranu; przy dużej bibliotece kilka minut.",
                xbmcgui.NOTIFICATION_INFO, 6000,
            )
        elif action in ("assignids", "assign_ids"):
            if not addon_settings().get("sudosync_id_enabled"):
                xbmcgui.Dialog().ok(
                    "KodiBridge",
                    "Funkcja jest wyłączona. Najpierw ją włącz i zatwierdź opcje przyciskiem OK."
                )
                return

            import xbmc
            monitor = xbmc.Monitor()
            dialog = xbmcgui.DialogProgress()
            dialog_title = _get_localized_string(32076, "KodiBridge — KodiBridge ID")
            stage1_desc = _get_localized_string(32080, "Skanowanie biblioteki i sprawdzanie KodiBridge ID...")
            stage2_desc = _get_localized_string(32081, "Przypisywanie KodiBridge ID...")
            auto_close_notice = _get_localized_string(32082, "To okno zamknie się automatycznie po zakończeniu operacji.")

            def is_canceled():
                try:
                    if dialog.iscanceled():
                        return True
                except Exception:
                    pass
                try:
                    if monitor.abortRequested() is True:
                        return True
                except Exception:
                    pass
                return False

            init_msg = "{}\n\n{}".format(stage1_desc, auto_close_notice)
            dialog.create(dialog_title, init_msg)

            try:
                def phase1_progress(current, total, item_name=""):
                    if is_canceled():
                        return
                    pct = int(min(100, max(0, (current / max(1, total)) * 100)))
                    name_str = " ({})".format(item_name) if item_name else ""
                    msg = "{stage} ({current}/{total}){name}\n\n{notice}".format(
                        stage=stage1_desc, current=current, total=total,
                        name=name_str, notice=auto_close_notice
                    )
                    dialog.update(pct, msg)

                preview = preview_sudosync_ids(progress_callback=phase1_progress, is_canceled=is_canceled)

                if is_canceled():
                    log("SudoSync ID assignment cancelled by user during Phase 1.", xbmc.LOGINFO)
                    return

                if not preview:
                    sync_res = assign_sudosync_ids(show_notification=False, auto_mode=True, is_canceled=is_canceled)
                    _note_nfo_changes(sync_res)
                    last_num = sync_res.get("last_number", 0) if isinstance(sync_res, dict) else 0
                    dialog.close()
                    xbmcgui.Dialog().ok(
                        "KodiBridge",
                        "Nie znaleziono żadnych nowych plików wymagających przypisania ID.\n"
                        "Ostatni użyty numer KodiBridge ID w sieci: {}".format(last_num),
                    )
                    return

                total_preview = len(preview)
                dialog.update(
                    0,
                    "{stage} (0/{total})\n\n{notice}".format(
                        stage=stage2_desc, total=total_preview, notice=auto_close_notice
                    )
                )

                def phase2_progress(current, total, item_name=""):
                    if is_canceled():
                        return
                    pct = int(min(100, max(0, (current / max(1, total)) * 100)))
                    name_str = " ({})".format(item_name) if item_name else ""
                    msg = "{stage} ({current}/{total}){name}\n\n{notice}".format(
                        stage=stage2_desc, current=current, total=total,
                        name=name_str, notice=auto_close_notice
                    )
                    dialog.update(pct, msg)

                collected_items = getattr(preview, "collected_items", None)
                result = assign_sudosync_ids(
                    show_notification=False,
                    auto_mode=False,
                    collected_items=collected_items,
                    progress_callback=phase2_progress,
                    is_canceled=is_canceled,
                )
                _note_nfo_changes(result)

                if isinstance(result, dict) and result.get("fatal_error") and not is_canceled():
                    log("SudoSync ID assignment aborted by error: {}".format(mask_vfs_credentials(result.get("fatal_error"))), xbmc.LOGERROR)
                    dialog.close()
                    manifest_name = os.path.basename(result.get("manifest_path", "")) or "assign_ids_manifest.json"
                    xbmcgui.Dialog().ok(
                        "KodiBridge — błąd",
                        "Błąd zapisu na udziale sieciowym: {}\n"
                        "Zmodyfikowano do błędu: filmy {}, odcinki {}.\n"
                        "Szczegóły w kodi.log oraz w raporcie ({})".format(
                            mask_vfs_credentials(result.get("fatal_error")),
                            result.get("movies", 0),
                            result.get("episodes", 0),
                            manifest_name,
                        ),
                    )
                    return

                if is_canceled() or (isinstance(result, dict) and result.get("cancelled")):
                    log("SudoSync ID assignment cancelled by user during Phase 2.", xbmc.LOGINFO)
                    return

                dialog.close()
                if not isinstance(result, dict):
                    return

                manifest_name = os.path.basename(result.get("manifest_path", "")) or "assign_ids_manifest.json"
                xbmcgui.Dialog().ok(
                    "KodiBridge",
                    "Zmodyfikowano lub utworzono: filmy: {}, odcinki: {}.\n"
                    "Ostatni numer KodiBridge ID w sieci: {}.\n"
                    "Raport z operacji zapisano w profilu ({})".format(
                        result.get("movies", 0),
                        result.get("episodes", 0),
                        result.get("last_number", 0),
                        manifest_name,
                    ),
                )
            finally:
                try:
                    dialog.close()
                except Exception:
                    pass
        elif action in ("repairids", "repair_ids"):
            repair_ids_dialog()
        elif action == "rollbackids":
            rollback_ids_dialog()
        else:
            show_status()
    except Exception as exc:
        xbmcgui.Dialog().ok("KodiBridge — błąd", mask_vfs_credentials(str(exc)))
    finally:
        if action in ("assignids", "assign_ids", "repairids", "repair_ids", "rollbackids") and _NFO_CHANGES["count"] > 0:
            # These actions change KodiBridge IDs in .nfo files from this script
            # process; the running service must drop its cached NFO identifiers.
            # Only when something changed: re-reading every .nfo of a large
            # library over SMB can take minutes.
            from resources.lib.sudosync_core import request_nfo_cache_refresh
            request_nfo_cache_refresh()


if __name__ == "__main__":
    main()
