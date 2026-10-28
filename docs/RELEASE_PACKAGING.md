# KodiBridge by Sudoizm — release packaging

This document defines the packaging rules for future 2.x releases. It does not
rename, replace, or otherwise modify existing GitHub Release assets.

## Release ZIP filename

For every new 2.x release, publish the add-on archive with this exact pattern:

`kodibridge-by-sudoizm-X.Y.Z.zip`

Replace `X.Y.Z` with the version declared in
`service.sudosync/addon.xml`. For example, version 2.1.4 must be published as
`kodibridge-by-sudoizm-2.1.4.zip`.

If a SHA-256 sidecar file is published, name it by appending `.sha256` to the
archive filename: `kodibridge-by-sudoizm-2.1.4.zip.sha256`. The checksum file
must contain the SHA-256 digest of that exact ZIP and identify the same filename.

## Required archive layout

The archive root must contain the add-on directory:

```text
service.sudosync/
  addon.xml
  service.py
  default.py
  resources/
  ...
```

Do not rename the internal `service.sudosync/` directory or the Kodi add-on ID
`service.sudosync`. The product's display name is KodiBridge by Sudoizm, but
the technical add-on ID remains unchanged to preserve installed configurations
and update-in-place behavior.

## Compatibility and historical releases

- Do not rename, overwrite, or replace any existing release ZIP.
- Do not modify the frozen SudoSync 1.0.0 release or the `v1-stable` branch.
- The built-in updater accepts both the new
  `kodibridge-by-sudoizm-X.Y.Z.zip` pattern and historical
  `service.sudosync-X.Y.Z.zip` names. Keep that compatibility unless a
  separately reviewed migration explicitly removes it.
- Local ZIP installation validates the archive's internal add-on manifest and
  version; the external filename does not replace those checks.
- The repository currently has test workflows but no automated release-ZIP
  packaging workflow. Build and inspect the archive before attaching it to a
  GitHub Release. Do not claim a release ZIP has been built or tested unless
  that work was actually performed.

## Release checklist

1. Confirm the version in `service.sudosync/addon.xml`.
2. Build the ZIP with `service.sudosync/` at the archive root.
3. Name the archive `kodibridge-by-sudoizm-X.Y.Z.zip`.
4. Inspect the archive contents and confirm `addon.xml` declares the same
   version and ID `service.sudosync`.
5. Calculate the SHA-256 digest from the final ZIP; if publishing a sidecar,
   ensure it names the same ZIP and contains the correct digest.
6. Attach the new assets to the matching GitHub Release without altering
   historical release assets.
7. Run the updater unit tests, including coverage for both branded and legacy
   ZIP filenames.
