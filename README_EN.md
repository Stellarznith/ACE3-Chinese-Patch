# Another Century's Episode 3 THE FINAL — Simplified Chinese Patch

> **Status: v1.0.0 release preparation complete, not yet published.**
> The patch has been generated and verified byte-for-byte; the installer and companion tools are included in this repository. Items that are not yet done (PS2 hardware verification, installer testing on a clean machine) are stated explicitly instead of being left as "TBD".

An unofficial, non-commercial Simplified Chinese localization for the Japanese PS2 release **SLPS-25784**.

This project started with a childhood wish to play *Another Century's Episode 3* in Chinese. That wish is finally within reach, and the result is meant to be shared with everyone who loves this game. The localization is not perfect yet, but it should let more players experience the game without a language barrier. Testing reports and bug reports are very welcome.

| | |
|---|---|
| Localization by | 伐妹儿 (Fameier) |
| GitHub | [Stellarznith](https://github.com/Stellarznith) |
| Project URL | `https://github.com/Stellarznith/ACE3-Chinese-Patch` |
| Current version | `v1.0.0` (release preparation, not yet published) |
| Supported original | Japanese SLPS-25784 (MD5 `3f8bc41101635b152fed920182cd36e3`) |

## Release positioning

**First public release · still being improved**

This is a localization that has gone through long-term production and debugging and is shared publicly as a finished fan work, not a temporary test build. Releases do not use labels such as "Beta", "public test build" or "final perfect version": it is neither an internal half-finished package, nor a claim that the work is perfect.

A public release means the work has reached a shareable level of completion. It does not mean zero bugs, and it does not mean every part of the game has been verified. Known limitations at this time:

- Some in-game textures remain untranslated and keep the original Japanese or English artwork.
- On bright battle backgrounds, the edges of some Chinese glyphs may show a faint grey outline.
- Mistranslations, missing lines or display glitches may still exist.
- Real PS2 hardware has not been verified yet.

Player testing and feedback are genuinely welcome, and fixes and updates will continue. Versioning plan: the first public release uses `v1.0.0`, later fixes move to `v1.0.1` and so on.

## Completion status

- **Game text: fully localized**, including story dialogue, menus, battle text, and character and mecha profiles.
- **In-game textures: partially localized.** Some textures still show the original Japanese or English artwork.
- **Patch restoration: verified.** Rebuilding the image from the original ISO with the bundled xdelta3 3.2.2 produced output byte-identical to the release master (0 bytes of difference) via two independent decoders; the original ISO's SHA-256 and modification time were unchanged.
- **Installer: tested on the development machine** (direct engine call and full GUI path), covering manifest validation, original-image fingerprint check, `.part` staging, no-overwrite commit, cancel and error paths. **Not yet verified by double-clicking the EXE on a clean Windows machine.**
- **Emulator testing**: see [compatibility](docs/COMPATIBILITY.md) and [known issues](docs/KNOWN_ISSUES.md).
- **PS2 hardware testing: not performed yet.** Running the patch on a real PlayStation 2 console is not guaranteed.

Because of the original font atlas capacity, text storage limits and on-screen space, a few Chinese lines may read less naturally than ideal.

Despite extensive translation, review and debugging work, mistranslations, missing lines, rendering glitches, texture problems, freezes or other undiscovered bugs may still exist.

## Testing and feedback

This is a personal project with limited test environments and playthrough coverage, so player testing is genuinely helpful. If you find any problem — on PCSX2 or on real hardware — please open a GitHub Issue and include the problem type, where it happened, how to reproduce it, your environment (emulator version and key settings, or console model), and screenshots or logs. Please do not upload full game ISOs, BIOS files or other copyrighted material.

## Download

Releases provide the xdelta patch, its JSON manifest, the Windows installer, the xdelta3 command-line tools and `SHA256SUMS.txt`. Release notes follow [docs/RELEASE_TEMPLATE.md](docs/RELEASE_TEMPLATE.md).

## What this is

A patch, not a game. No ISO, BIOS or game data is distributed here.

You need your own legally obtained Japanese copy of the game. The installer verifies the expected original checksum and refuses mismatched images.

## Repository layout

| Directory | Contents |
|---|---|
| `docs/` | Installation, compatibility, known issues, verification log, FAQ, credits, release template |
| `installer/` | Patch installer source, patch manifest protocol (v1) and build script |
| `workshop/` | ACE3 localization workshop source (GUI editing tool, PySide6) |
| `tools/` | Low-level resource format modules (LZ archives, font atlas, code maps, TEX) and the text locator |

## Distribution policy

Free to redistribute and mirror, always free of charge. Selling the patch, paywalling it, or claiming authorship is not allowed. Full terms: [DISTRIBUTION.md](DISTRIBUTION.md).

## Disclaimer

Unofficial fan work, provided as-is without warranty; back up your disc image and memory card first. Full terms: [DISCLAIMER.md](DISCLAIMER.md).

## License

Source code under `installer/`, `workshop/` and `tools/` is MIT. Patches and release packages follow [DISTRIBUTION.md](DISTRIBUTION.md). See [LICENSE-SCOPE.md](LICENSE-SCOPE.md) for the boundaries.
