**English** | [Polski](README.pl.md)

---

> **Note:** This repository (master branch) tracks **SudoSync 2.0.0** (in preparation, not yet published), featuring the experimental SudoSync ID system for private videos without standard online IDs. Stable version **SudoSync 1.0.0** is on branch [v1-stable](https://github.com/BreakTechEu/SudoSync/tree/v1-stable) and is no longer developed.

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

> [!NOTE]
> The release package for SudoSync 2.0.0 (`service.sudosync-2.0.0.zip`) has not been published yet. The **Releases** section currently provides stable version SudoSync 1.0.0 (`service.sudosync-1.0.0.zip`).

1. **Installation Package:**
   - For stable version SudoSync 1.0.0, download `service.sudosync-1.0.0.zip` from the **Releases** section.
   - To test development version 2.0.0 from this repository, package the `service.sudosync` directory into a zip archive.

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

---

## Updating the Add-on

> [!IMPORTANT]
> **Important Update Notice:**
> Currently (temporarily), the only valid and safe way to update the add-on without breaking the existing installation is to place the new release package (`.zip`) directly into the shared `.SudoSync` network folder (e.g. `smb://.../.SudoSync/service.sudosync-X.X.X.zip`).
>
> SudoSync will detect the new package in the shared directory and handle the update (either prompting you or installing automatically if enabled in add-on settings). Do not reinstall over an existing setup using Kodi's standard "Install from zip file" dialog.

---

## Compatibility

> **Notice:** This plugin exclusively supports the two most recent major versions of Kodi (currently v20 Nexus and v21 Omega). Older versions are no longer supported.

- Kodi v20 Nexus and v21 Omega.
- Multi-platform: Windows, Android / Google TV, Linux, CoreELEC / LibreELEC.

---

## License

This project is open-source software licensed under the [GPL-3.0-or-later](LICENSE) license.