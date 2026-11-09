# Signing the installers

The workflow builds unsigned installers. They work, but each system warns the user:

- **Mac:** "Veuly cannot be opened because Apple cannot check it". The user has to right-click
  the app, choose Open, and confirm. Most students will stop there.
- **Windows:** SmartScreen shows "Windows protected your PC" until the file has built up reputation.

Removing the warnings costs money and needs your identity, so it is not automated here.

## Mac: Developer ID and notarization

Needs the Apple Developer Program (99 USD a year). With a "Developer ID Application" certificate in
your keychain, after `python3 packaging/build.py --no-test`:

```
codesign --force --deep --options runtime --timestamp --sign "Developer ID Application: YOUR NAME (TEAMID)" dist/Veuly.app
hdiutil create -volname Veuly -srcfolder dist/Veuly.app -ov -format UDZO dist/Veuly-mac-arm64.dmg
xcrun notarytool submit dist/Veuly-mac-arm64.dmg --apple-id YOU@EXAMPLE.COM --team-id TEAMID --password APP_SPECIFIC_PASSWORD --wait
xcrun stapler staple dist/Veuly-mac-arm64.dmg
```

The bundled `whisper-cli` is signed by `--deep`. If notarization rejects it, sign that file first
with the same identity and `--options runtime`.

The native Mac app is what gets sold. `build_mac.py` does all of this with a real identity:

```
# once: let notarytool use your Apple ID (keeps it in your Keychain under a name you pick)
xcrun notarytool store-credentials veuly-notary --apple-id YOU@EXAMPLE.COM --team-id TEAMID --password APP_SPECIFIC_PASSWORD

python3 packaging/build_mac.py dist --standalone --whisper FILE --dmg \
  --sign "Developer ID Application: YOUR NAME (TEAMID)" --notarize veuly-notary \
  --update-url https://YOUR.SITE/veuly/latest.json
```

`--standalone` puts its own Python and whisper-cli inside, so a Mac needs nothing else. It signs each program and library inside
first, then the app, with the hardened runtime and `packaging/mac/veuly.entitlements` (the microphone only), signs the DMG, sends it to
Apple and staples the ticket. This has not been run with a real identity yet: until it has, a stranger's Mac may show problems that
ad hoc signing hides. FILE comes from whisper.cpp with `-DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_METAL_EMBED_LIBRARY=ON`,
with `whisper-LICENSE.txt` beside it; the build refuses one that needs libraries other Macs do not have.

## Windows: code signing

A certificate from a certificate authority (roughly 100 to 400 EUR a year; since 2023 the key lives
on a hardware token or a cloud service such as Azure Trusted Signing). Then:

```
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\Veuly-Setup.exe
```

These commands were not run for this project. Check them against Apple's and Microsoft's current
documentation before relying on them.
