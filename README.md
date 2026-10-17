**English** | [Polski](README.pl.md)

---

> [!WARNING]
> **Beta Release Notice (SudoSync 2.0.3):**
> SudoSync 2.0.3 is a **public beta / testing release** featuring the rebuilt v2 architecture and experimental SudoSync ID system for private videos. While tested thoroughly, it is intended for testing and early adopters. If you require a battle-tested, fully stable environment, use **SudoSync 1.0.0** from branch [v1-stable](https://github.com/BreakTechEu/SudoSync-Kodi-addon/tree/v1-stable).
> Both releases are available in the **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** section.

# SudoSync for Kodi

A lightweight background service add-on for Kodi that provides automatic, two-way synchronization of watch states, resume points, user ratings, and last-played timestamps across multiple Kodi instances in the same local network — without requiring an external MySQL/MariaDB server.

Synchronization relies on a shared network directory (SMB, NFS, or local share) and safe JSON-RPC API calls.

---

## Key Features

- **No Dedicated Database Required:** Uses a shared network path accessible by your devices (NAS, local PC, or network share).
- **Safe & Non-Destructive:** Interacts exclusively through Kodi's official JSON-RPC API. It never touches or locks internal SQLite files (`MyVideos*.db`) directly.
- **Single Active Kodi Guard:** An active session lease (`active_kodi_lock.json`) ensures only one Kodi instance runs SudoSync in the shared network at a time, preventing concurrent write conflicts.
- **Robust Conflict Model:** Each synchronized field maintains its own logical version (Lamport clock) and client history, preventing race conditions, feedback loops, and desynchronization due to drifting client clocks. Atomic file locking safeguards the shared NAS state.
- **Optimized for Android TV & Streaming Devices:** A lightweight state diff engine handles platforms where Kodi occasionally fails to trigger `OnUpdate` or `OnStop` notifications.
- **Scraper & NFO Quarantine:** Newly scanned library items and NFO imports receive older internal versions to prevent accidental overwrites of existing shared playback progress.
- **Experimental SudoSync ID:** Automatically assigns and securely injects unique identifiers into `.nfo` files for private home videos that lack standard online scraper IDs.
- **Network ID vs UI Alias:** Distinctly separates internal network identifiers from user-friendly UI aliases to keep network mapping robust while displaying readable names.
- **GitHub Releases & Version Guard:** Checks GitHub Releases once per day (with explicit user consent), supports manual ZIP installation, and enforces a minimum add-on version (`min_addon_version` in `SudoSync.cfg`) across all Kodi devices in the network.
- **Journaled Sync:** Network sync gracefully handles partial Kodi API errors with an incremental journaling system, allowing for safe recovery.
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
   - **SudoSync 2.0.3 (Beta):** Download `service.sudosync-2.0.3.zip` from the **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** section (recommended for testing new v2 features and SudoSync ID).
   - **SudoSync 1.0.0 (Stable):** Download `service.sudosync-1.0.0.zip` from the **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** section (recommended for production environments requiring maximum stability).

2. **Install in Kodi:**
   - In Kodi, navigate to: *Settings -> Add-ons -> Install from zip file*.
   - Select the package archive.

3. **Configure Network Path & Identifiers:**
   - On first start, the SudoSync wizard prompts you to choose a shared network folder (e.g. `smb://192.168.1.100/SharedFolder/`), creates `.SudoSync/` automatically, and enrolls the device.
   - In add-on settings, you can set a friendly device name and configure the **Network Name (SudoSync ID prefix)** for private videos.

For a step-by-step safe manual testing guide for version 2.x (Beta) on Kodi (in Polish), see [`docs/TEST_NA_KODI.md`](docs/TEST_NA_KODI.md).

---

## Updating the Add-on

> [!IMPORTANT]
> **Safe Update & Network Version Guard:**
> SudoSync includes a built-in update mechanism that protects network consistency:
> 1. **Automatic GitHub Releases Check (Once Every 24 Hours):** SudoSync checks the official GitHub Releases (`BreakTechEu/SudoSync-Kodi-addon`) at most once per day. Silent background updates are disabled — when a newer release is found, SudoSync prompts you for explicit consent before downloading and installing (you can also trigger the check manually in add-on settings).
> 2. **Update from Local ZIP File:** Add-on settings provide an option to browse and install a `service.sudosync-X.X.X.zip` package directly from local storage or a network share.
> 3. **Network Minimum Version Guard (`min_addon_version` in `SudoSync.cfg`):** Whenever a Kodi device on the shared network runs a newer SudoSync version, it records that version as `min_addon_version` in `SudoSync.cfg`. Any other Kodi instance starting with an older version will detect the mismatch and either force an immediate update (via GitHub Releases or local ZIP) or stay paused until updated.

---

## Compatibility

> **Notice:** This plugin exclusively supports the two most recent major versions of Kodi (currently v20 Nexus and v21 Omega). Older versions are no longer supported.

- Kodi v20 Nexus and v21 Omega.
- Multi-platform: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## License

This project is open-source software licensed under the [GPL-3.0-or-later](LICENSE) license.