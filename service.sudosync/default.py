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
from resources.lib.sudosync_i18n import tr


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
    gh_when = status.get("last_github_update_check_at") or tr(32100)
    if gh_upd.get("available"):
        update_text = tr(32101, gh_upd.get("version"), gh_when)
    elif gh_upd.get("error"):
        update_text = tr(32102, gh_when, gh_upd.get("error"))
    else:
        update_text = tr(32103, addon_version, gh_when)

    live_enabled = settings.get("live_sync_enabled", True)
    live_text = tr(
        32104,
        state=tr(32105) if live_enabled else tr(32106),
        poll=settings.get("live_poll_seconds", 60),
        when=status.get("last_live_sync_at") or "—",
        received=(
            tr(32107, status.get("last_live_received_items"), status.get("last_live_received_at") or "—")
            if status.get("last_live_received_items") else "—"
        ),
        error=mask_vfs_credentials(status.get("last_live_error") or tr(32108)),
    )

    import xbmcaddon
    addon_version = xbmcaddon.Addon("service.sudosync").getAddonInfo("version")
    sudosync_id_state = tr(32109) if settings.get("sudosync_id_enabled") else tr(32110)

    coverage_text = _coverage_text(status.get("kodibridge_id_coverage"), bool(settings.get("sudosync_id_enabled")))
    text = tr(
        32111,
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
    xbmcgui.Dialog().textviewer(tr(32112), text)


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
        xbmcgui.Dialog().ok(tr(32113), tr(32114, result["error"]))
        return
    if not result.get("available"):
        xbmcgui.Dialog().ok(tr(32113), tr(32115, result.get("current_version", "—")))
        return
    if xbmcgui.Dialog().yesno(
        tr(32116),
        tr(32117, result.get("version"), result.get("current_version", "—")),
        yeslabel=tr(32118),
        nolabel=tr(32119),
    ):
        install_github_update(update_info=result, show_dialogs=True)


def install_local_zip_dialog():
    zip_path = xbmcgui.Dialog().browse(
        1, tr(32120), "files", ".zip"
    )
    if not zip_path:
        return
    install_update_from_zip_path(zip_path, show_dialogs=True)


def _problem_lines(movie, episode, kinds):
    problems = []
    for key, label in kinds:
        count = int(movie.get(key) or 0) + int(episode.get(key) or 0)
        if not count:
            continue
        places = []
        for media in (movie, episode):
            for place in ((media.get("places") or {}).get(key) or []):
                if place not in places:
                    places.append(place)
        problems.append("{}: {}".format(label, count))
        if places:
            problems.extend("  – " + place for place in places)
    return problems


def _coverage_text(coverage, id_enabled=True):
    """Describe the coverage of this Kodi's library for the status window.

    For each problem only where to look: the movie, or the series and season.
    ``id_enabled`` is the current KodiBridge ID option; a coverage recorded in
    the other mode is not shown (the next sync cycle replaces it).
    """
    recorded_external = isinstance(coverage, dict) and coverage.get("mode") == "external"
    if not isinstance(coverage, dict) or recorded_external == bool(id_enabled):
        if id_enabled:
            return tr(32121)
        return tr(32122)
    movie = coverage.get("movie") or {}
    episode = coverage.get("episode") or {}
    if not id_enabled:
        lines = [tr(32123,
                    movie.get("with_id", 0), movie.get("total", 0),
                    episode.get("with_id", 0), episode.get("total", 0),
                    coverage.get("checked_at") or "—")]
        ignored = int(movie.get("no_external") or 0) + int(episode.get("no_external") or 0)
        if ignored:
            lines.append(tr(32124, ignored))
        problems = _problem_lines(movie, episode, (
            ("ambiguous", tr(32125)),
            ("repeated", tr(32126))))
        if coverage.get("unreadable_nfo"):
            problems.append(tr(32127, coverage["unreadable_nfo"]))
        if problems:
            lines.append(tr(32128))
            lines.extend(problems)
        return "\n".join(lines)
    lines = [tr(
        32129,
        movie.get("with_id", 0), movie.get("total", 0),
        episode.get("with_id", 0), episode.get("total", 0),
        coverage.get("checked_at") or "—",
    )]
    problems = _problem_lines(movie, episode, (
        ("missing", tr(32130)), ("duplicates", tr(32131)),
        ("ambiguous", tr(32132)),
        ("repeated", tr(32126))))
    if coverage.get("unreadable_nfo"):
        problems.append(tr(32127, coverage["unreadable_nfo"]))
    if problems:
        lines.append(tr(32128))
        lines.extend(problems)
    else:
        lines.append(tr(32133))
    return "\n".join(lines)


ASSIGN_BUSY_MESSAGE_ID = 32134


def assign_busy_message():
    return tr(ASSIGN_BUSY_MESSAGE_ID)


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
        xbmcgui.Dialog().ok("KodiBridge", tr(32135))
        return
    if not xbmcgui.Dialog().yesno(tr(32136), tr(32137), yeslabel=tr(32138), nolabel=tr(32119)):
        return

    import xbmc
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    heading = tr(32139)

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
        dialog.update(pct, tr(32140, current, total, label))

    dialog.create(heading, tr(32141))
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
        xbmcgui.Dialog().ok("KodiBridge", tr(32142))
        return
    repeated = result.get("repeated") or []
    repeated_lines = []
    if repeated:
        repeated_lines = ["", tr(32143)]
        repeated_lines += ["  – " + mask_vfs_credentials(line) for line in repeated]
    if not result.get("groups"):
        if repeated_lines:
            xbmcgui.Dialog().textviewer(heading, "\n".join([tr(32144)] + repeated_lines))
        else:
            xbmcgui.Dialog().ok("KodiBridge", tr(32144))
        return

    lines = [
        tr(32145,
           result.get("groups", 0), result.get("renumbered", 0), len(result.get("errors") or [])),
    ]
    if result.get("cancelled"):
        lines.append(tr(32146))
    if result.get("fatal_error"):
        lines.append(tr(32147, mask_vfs_credentials(result["fatal_error"])))
    if result.get("changes"):
        lines += ["", tr(32148)] + ["  " + line for line in result["changes"]]
    if result.get("kept"):
        lines += ["", tr(32149)] + ["  " + line for line in result["kept"]]
    lines += repeated_lines
    if result.get("errors"):
        lines += ["", tr(32150)] + ["  " + mask_vfs_credentials(line) for line in result["errors"]]
    lines += ["", tr(32151, os.path.basename(result.get("manifest_path") or "") or "repair_ids_manifest.json",
                     tr(32072))]
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
        xbmcgui.Dialog().ok("KodiBridge", tr(32152))
        return
    choice_labels = {"assign": 32153, "repair": 32154}
    confirm_texts = {"assign": 32155, "repair": 32156}
    kind, path, stamp = manifests[0]
    if len(manifests) > 1:
        choice = xbmcgui.Dialog().select(
            tr(32157),
            [tr(choice_labels[k], s[:16].replace("T", " ") or "?") for k, _p, s in manifests],
        )
        if choice < 0:
            return
        kind, path, stamp = manifests[choice]
    if not xbmcgui.Dialog().yesno(
        tr(32158),
        tr(confirm_texts[kind]),
        yeslabel=tr(32159),
        nolabel=tr(32119),
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
    message = tr(32160, res.get("rolled_back", 0), res.get("errors", 0))
    if res.get("already_rolled_back"):
        message += "\n" + tr(32161, res["already_rolled_back"])
    xbmcgui.Dialog().ok("KodiBridge", message)


def main():
    install_v2_log_bridge()
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    try:
        if action == "bootstrap":
            from resources.lib.sudosync_bootstrap import run_bootstrap
            if run_bootstrap(force_select_path=True):
                xbmcgui.Dialog().ok("KodiBridge", tr(32162))
        elif action in ("checkupdate", "update"):
            update_dialog()
        elif action in ("installlocalzip", "localupdate"):
            install_local_zip_dialog()
        elif action in ("installupdate", "selfupdate"):
            result = find_github_update()
            if result.get("error"):
                raise IOError(result["error"])
            if not result.get("available"):
                xbmcgui.Dialog().ok(tr(32163), tr(32164))
            elif xbmcgui.Dialog().yesno(
                tr(32163),
                tr(32165, result.get("version")),
                yeslabel=tr(32118),
                nolabel=tr(32119),
            ):
                install_github_update(update_info=result, show_dialogs=True)
        elif action in ("resolveconflicts", "conflicts"):
            from resources.lib.sudosync_conflicts import request_resolution
            request_resolution()
            xbmcgui.Dialog().notification(
                "KodiBridge",
                tr(32166),
                xbmcgui.NOTIFICATION_INFO, 6000,
            )
        elif action in ("assignids", "assign_ids"):
            if not addon_settings().get("sudosync_id_enabled"):
                xbmcgui.Dialog().ok("KodiBridge", tr(32135))
                return

            import xbmc
            monitor = xbmc.Monitor()
            dialog = xbmcgui.DialogProgress()
            dialog_title = tr(32076)
            stage1_desc = tr(32080)
            stage2_desc = tr(32081)
            auto_close_notice = tr(32082)

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
                    dialog.close()
                    if not isinstance(sync_res, dict):
                        xbmcgui.Dialog().ok("KodiBridge", assign_busy_message())
                        return
                    xbmcgui.Dialog().ok("KodiBridge", tr(32167))
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
                        tr(32168),
                        tr(32169,
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
                    # Another assignment (e.g. the one after a library scan) holds the lock.
                    xbmcgui.Dialog().ok("KodiBridge", assign_busy_message())
                    return

                manifest_name = os.path.basename(result.get("manifest_path", "")) or "assign_ids_manifest.json"
                xbmcgui.Dialog().ok(
                    "KodiBridge",
                    tr(32170,
                       result.get("movies", 0),
                        result.get("episodes", 0),
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
        xbmcgui.Dialog().ok(tr(32168), mask_vfs_credentials(str(exc)))
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
