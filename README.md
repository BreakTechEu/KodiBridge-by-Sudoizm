**English** | [Polski](README.pl.md)

---

> [!NOTE]
> **Production Release (KodiBridge by Sudoizm 2.2.4):**
> KodiBridge by Sudoizm 2.2.4 is the **production release** of the v2 architecture featuring journaled synchronization, KodiBridge ID for private videos and local networks, single-active-Kodi session guard, and cross-device version enforcement. The legacy **SudoSync 1.0.0** line remains available on the read-only branch [v1-stable](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/tree/v1-stable).
> Both releases are available in the **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)** section.

# KodiBridge by Sudoizm

A lightweight background service add-on for Kodi that provides automatic, two-way synchronization of watch states, resume points, user ratings, and last-played timestamps across multiple Kodi instances in the same local network — without requiring an external MySQL/MariaDB server.

Synchronization relies on a shared network directory (SMB, NFS, or local share) and safe JSON-RPC API calls.

---

## Key Features

- **No Dedicated Database Required:** Uses a shared network path accessible by your devices (NAS, local PC, or network share).
- **Safe & Non-Destructive:** Interacts exclusively through Kodi's official JSON-RPC API. It never touches or locks internal SQLite files (`MyVideos*.db`) directly.
- **Single Synchronization Engine:** All synchronization runs through one engine (v2: snapshots, transaction journal, recovery). The legacy 1.x engine is no longer used; old 1.x files on the NAS are left untouched.
- **Single Active Kodi Guard:** An active session lease (`active_kodi_lock.json`) ensures only one Kodi instance runs KodiBridge in the shared network at a time, preventing concurrent write conflicts.
- **Robust Conflict Model:** Each synchronized field maintains its own logical version (Lamport clock) and client history, preventing race conditions, feedback loops, and desynchronization due to drifting client clocks. Atomic file locking safeguards the shared NAS state.
- **Choice on Conflicting Edits:** When the same field was changed on two Kodi devices so that neither change is newer (e.g. play count 1 on one and 2 on the other), KodiBridge shows a table: rows are the fields that differ, columns are the devices. You pick the correct value in each row (values from different devices can be combined), and the choice is saved as the newest, so it reaches every Kodi. The table works with mouse, keyboard and remote. The dialog appears once the conflict is detected; during playback it waits until the video stops. "Later" postpones the question until the next Kodi start; it can be opened at any time with "Resolve conflicts" in the add-on settings. Until a choice is made, that field is not synchronized; all other fields keep working.
- **Optimized for Android TV & Streaming Devices:** Every sync cycle rebuilds the local snapshot and publishes it only when it changed, which also covers platforms where Kodi occasionally fails to trigger `OnUpdate` or `OnStop` notifications.
- **Scraper & NFO Quarantine:** Newly scanned library items and NFO imports receive older internal versions to prevent accidental overwrites of existing shared playback progress.
- **KodiBridge ID & Partial Library Support:** Automatically assigns and securely injects sequential unique identifiers (`{prefix}-{num:06d}`) into `.nfo` files for all video items in the library as an independent, parallel local-network identifier (regardless of external IMDb/TMDb/TVDb IDs), skipping only files that already possess a KodiBridge ID. In `.nfo` files the KodiBridge ID is stored as `<uniqueid type="sudosync">` (unchanged technical tag, compatible with existing libraries). Upon finishing a video library scan, the background service silently (headless mode) assigns missing IDs with full NAS concurrency locking and immediately publishes an updated v2 snapshot to the NAS. A shared counter (`sudosync_id_last_number` in `SudoSync.cfg` and `system/config.json`) ensures all Kodi devices in the network know the highest assigned number and allows Kodi instances with different library scopes (partial vs. full NAS access) to index and synchronize seamlessly without conflicts or duplicate IDs.
- **Media Matching:** When the network KodiBridge ID option is enabled, items are matched between Kodi devices only by KodiBridge ID. IMDb/TMDb/TVDb/Trakt identifiers are used only when the option is disabled (e.g. without write access to the NAS). Items without a usable identifier are skipped, never matched by title.
- **Network ID vs UI Alias:** Distinctly separates internal network identifiers from user-friendly UI aliases to keep network mapping robust while displaying readable names.
- **GitHub Releases & Version Guard:** Checks GitHub Releases once per day (with explicit user consent), supports manual ZIP installation, and enforces a minimum add-on version (`min_addon_version` in `SudoSync.cfg`) across all Kodi devices in the network; a newer version recorded by another Kodi triggers the update prompt immediately.
- **Journaled Sync:** Network sync gracefully handles partial Kodi API errors with an incremental journaling system, allowing for safe recovery. If an item changed in Kodi after an interrupted write, recovery keeps the current Kodi value, skips the item without writing and continues, so an interrupted write never blocks synchronization permanently.
- **Strong File Limits:** Automatic file size and record count limits prevent Kodi memory exhaustion.

---

## Synchronized Attributes

- Watch state & play count (watched / unwatched),
- Exact playback resume position,
- Last played timestamp,
- User ratings (1–10).

---

## Installation & Setup

1. **Installation Package:**
   - **KodiBridge by Sudoizm 2.2.4 (Production):** Download `kodibridge-by-sudoizm-2.2.4.zip` from the **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)** section (recommended production release with v2 architecture and KodiBridge ID).
   - **SudoSync 1.0.0 (Legacy Stable):** Download `service.sudosync-1.0.0.zip` from the **[Releases](https://github.com/BreakTechEu/KodiBridge-by-Sudoizm/releases)** section.

2. **Install in Kodi:**
   - In Kodi, navigate to: *Settings -> Add-ons -> Install from zip file*.
   - Select the package archive.

3. **Configure Network Path & Identifiers:**
   - On first start, the KodiBridge wizard prompts you to choose a shared network folder (e.g. `smb://192.168.1.100/SharedFolder/`), creates `.SudoSync/` automatically, and enrolls the device.
   - In add-on settings, you can set a friendly device name and configure the **Network Name (KodiBridge ID prefix)** for private videos.

---

## Updating the Add-on

> [!IMPORTANT]
> **Safe Update & Network Version Guard:**
> KodiBridge includes a built-in update mechanism that protects network consistency:
> 1. **Automatic GitHub Releases Check (Once a Day):** KodiBridge checks the official GitHub Releases (`BreakTechEu/KodiBridge-by-Sudoizm`) once per calendar day, at the first Kodi start of the day (or just after midnight if Kodi keeps running). Silent background updates are disabled — when a newer release is found, KodiBridge prompts you for explicit consent before downloading and installing (you can also trigger the check manually in add-on settings).
> 2. **Update from Local ZIP File:** Add-on settings provide an option to browse and install a `kodibridge-by-sudoizm-X.Y.Z.zip` package directly from local storage or a network share. Existing historical releases keep their original filenames; the built-in updater accepts both the new KodiBridge filename and legacy `service.sudosync-X.Y.Z.zip` names.
> 3. **Network Minimum Version Guard (`min_addon_version` in `SudoSync.cfg`):** Whenever a Kodi device on the shared network runs a newer KodiBridge version, it records that version as `min_addon_version` in `SudoSync.cfg`. Another Kodi with an older version detects this at start-up or while running (within about a minute; during playback the prompt waits until the video stops) and immediately offers an update from GitHub Releases or a local ZIP, without waiting for the daily check. If the update is declined, KodiBridge does not synchronize that Kodi and asks again after the next Kodi start.

---

## Compatibility

> **Notice:** This plugin officially supports the two most recent **stable** major versions of Kodi (currently v20 Nexus and v21 Omega). Pre-release / RC builds of upcoming major versions (such as Kodi v22 Piers RC) are not yet officially verified. Older versions are no longer supported.

- Kodi v20 Nexus and v21 Omega.
- Multi-platform: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## License

This project is open-source software licensed under the [GPL-3.0-or-later](LICENSE) license.