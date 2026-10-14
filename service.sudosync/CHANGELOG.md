SudoSync 1.1.0 (v2 Beta)
======================

Dodano eksperymentalną obsługę "SudoSync ID" dla prywatnych materiałów wideo, które nie posiadają oficjalnego identyfikatora (IMDb/TMDb). SudoSync pozwala teraz wstrzykiwać własne unikalne ID prosto do plików `.nfo` po stronie współdzielonej biblioteki.

SudoSync 1.0.0
==============

0.4.1 naprawia też mylący DRY RUN przy mieszance snapshotów 0.3/0.4: zamiast tysięcy pozornych zmian oceny null -> 0 raport pokazuje oczekiwanie na aktualizację klientów.

SudoSync 0.4.x — historia wersji testowej

Local Kodi watch-state synchronization via a shared NAS folder.

The LIVE synchronization engine activates only after the shared
initialization is marked completed. Before that, the 0.3 safe initial workflow
remains in force.

LIVE synchronized fields:
- playcount / watched-unwatched state;
- lastplayed;
- resume position/total (including clearing resume);
- userrating 1-10 after initialization. Historical pre-initialization ratings
  outside 6/7/8 remain ignored until the user actually changes that rating.

Safety / conflict model:
- JSON-RPC only; never reads/writes Kodi SQLite/MyVideos directly;
- strong IDs (IMDb/TMDb/TVDb/Trakt) remain the cross-device identity;
- ambiguous ID groups and unidentified records remain skipped;
- verified local/network mirrors are supported and all local copies receive the
  winning state;
- each field has its own version (timestamp + sequence + client ID), so a newer
  real user change can lower playcount, clear resume, clear/change a rating, etc.;
- remote writes keep the winning version and therefore do not bounce back as a
  new local event (feedback-loop guard);
- new library items and scan/NFO-import changes get a deliberately old version,
  so they cannot overwrite remembered shared state;
- no writes are performed while Kodi is actively playing video;
- continuous applies keep a bounded rolling pre-live snapshot backup instead of
  producing unlimited full backups;
- a local cycle lock prevents double-running the same sync on one Kodi.

Operation:
- live synchronization defaults ON after completed initialization;
- local user changes normally trigger a cycle about 2 seconds after Kodi notifications;
- other clients are polled every 60 seconds by default (configurable 30-600 s);
- Player.OnStop triggers a prompt cycle;
- during VideoLibrary scan, user-change detection is suppressed; scan/NFO changes
  cannot become authoritative merely because they were imported;
- while upgrading from 0.3, 0.4 waits until every known client has written a
  versioned LIVE snapshot before any background LIVE write is allowed.

Shared path defaults to empty (configured via first-run setup wizard).

Existing 0.3 protections retained:
- explicit base-device selection and clear-before-replace rule;
- one-shot initial write safety interlock;
- two confirmations and pre-initial-write full backup;
- visible progress notifications for manual writes;
- self-update from the hidden .SudoSync folder.


0.4.2-alpha: powiadomienia LIVE (wysłano/odebrano), domyślnie włączone; ochrona nowszego lokalnego lastplayed/resume przed regresją; poprawka wyścigu skan biblioteki vs Player.OnStop.
0.4.3-alpha: powiadomienia LIVE tylko po odebraniu/zastosowaniu danych z innego Kodi; brak komunikatu na źródle; przy synchronizacji podczas startu komunikat odbiorczy jest opóźniony o 12 s, by nie zginął wśród komunikatów uruchomieniowych.

0.4.4-alpha: naprawa propagacji zmiany już istniejącej oceny; zdarzenie VideoLibrary.OnUpdate po skanie nie jest już mylone z kwarantanną skanu. Dodatkowo komunikat odbiorczy LIVE jest wymuszany tylko po faktycznym zastosowaniu zmian z innego Kodi.


1.0.0: pierwsze wydanie produkcyjne; logika synchronizacji zgodna z przetestowanym 0.4.4-alpha, bez zmian formatu danych wymagających ponownej inicjalizacji.
