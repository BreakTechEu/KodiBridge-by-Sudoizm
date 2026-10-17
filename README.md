**English** | [Polski](README.pl.md)

---

> [!WARNING]
> **Beta Release Notice (SudoSync 2.0.0):**
> SudoSync 2.0.0 is a **public beta / testing release** featuring the rebuilt v2 architecture and experimental SudoSync ID system for private videos. While tested thoroughly, it is intended for testing and early adopters. If you require a battle-tested, fully stable environment, use **SudoSync 1.0.0** from branch [v1-stable](https://github.com/BreakTechEu/SudoSync-Kodi-addon/tree/v1-stable).
> Both releases are available in the **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** section.

# SudoSync for Kodi

A lightweight background service add-on for Kodi that provides automatic, two-way synchronization of watch states, resume points, user ratings, and last-played timestamps across multiple Kodi instances in the same local network — without requiring an external MySQL/MariaDB server.

Synchronization relies on a shared network directory (SMB, NFS, or local share) and safe JSON-RPC API calls.

---

## Key Features

- **No Dedicated Database Required:** Uses a shared network path accessible by your devices (NAS, local PC, or network share).
- **Safe & Non-Destructive:** Interacts exclusively through Kodi's official JSON-RPC API. It never touches or locks internal SQLite files (`MyVideos*.db`) directly.
- **Robust Conflict Model:** Each synchronized field maintains its own logical version (Lamport clock) and client history, preventing race conditions, feedback loops, and desynchronization due to drifting client clocks. Atomic file locking safeguards the shared NAS state.
- **Optimized for Android TV & Streaming Devices:** A lightweight state diff engine handles platforms where Kodi occasionally fails to trigger `OnUpdate` or `OnStop` notifications.
- **Scraper & NFO Quarantine:** Newly scanned library items and NFO imports receive older internal versions to prevent accidental overwrites of existing shared playback progress.
- **Experimental SudoSync ID:** Automatically assigns and securely injects unique identifiers into `.nfo` files for private home videos that lack standard online scraper IDs.
- **Network ID vs UI Alias:** Distinctly separates internal network identifiers from user-friendly UI aliases to keep network mapping robust while displaying readable names.
- **Cryptographic Security:** Self-updates via NAS are authenticated with a 2048-bit RSA signature. Untrusted or modified zip files are securely rejected.
- **Journaled Initial Sync:** Network sync gracefully handles partial Kodi API errors with an incremental journaling system, allowing for safe recovery.
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
   - **SudoSync 2.0.0 (Beta):** Download `service.sudosync-2.0.0.zip` from the **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** section (recommended for testing new v2 features and SudoSync ID).
   - **SudoSync 1.0.0 (Stable):** Download `service.sudosync-1.0.0.zip` from the **[Releases](https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases)** section (recommended for production environments requiring maximum stability).

2. **Install in Kodi:**
   - In Kodi, navigate to: *Settings -> Add-ons -> Install from zip file*.
   - Select the package archive.

3. **Configure Network Path & Identifiers:**
   - Open SudoSync add-on settings in Kodi.
   - Specify the shared network folder where all Kodi instances have read and write permissions, e.g.:
     ```text
     smb://192.168.1.100/SharedFolder/.SudoSync/
     ```
   - In the settings, specify your **Network Identifier** (used internally for file sync) and your **UI Alias** (a friendly name displayed on screen).

For a step-by-step safe manual testing guide for version 2.0.0 (Beta) on Kodi (in Polish), see [`docs/TEST_NA_KODI.md`](docs/TEST_NA_KODI.md).

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