# Another Century's Episode 3 THE FINAL — Simplified Chinese Patch

> **Status: work in progress, not released.**
> This repository currently contains only the release skeleton. Items marked "TBD" must be replaced with verified results before any public release.

An unofficial, non-commercial Simplified Chinese localization for the Japanese PS2 release **SLPS-25784**.

This project started with a childhood wish to play *Another Century's Episode 3* in Chinese. That wish is finally within reach, and the result is meant to be shared with everyone who loves this game. The localization is not perfect yet, but it should let more players experience the game without a language barrier. Testing reports and bug reports are very welcome.

| | |
|---|---|
| Localization by | 伐妹儿 (Fameier) |
| GitHub | [Stellarznith](https://github.com/Stellarznith) |
| Project URL (planned) | `https://github.com/Stellarznith/ACE3-Chinese-Patch` (not reachable until the repository exists) |

## Completion status

- **Game text: fully localized**, including story dialogue, menus, battle text, and character and mecha profiles.
- **In-game textures: partially localized.** Some textures still show the original Japanese or English artwork.
- **Emulator testing**: reported as-is from actual test runs; compatibility and known issues are updated as testing continues.
- **PS2 hardware testing: not performed yet.** Running the patch on a real PlayStation 2 console is not guaranteed.

Because of the original font atlas capacity, text storage limits and on-screen space, a few Chinese lines may read less naturally than ideal.

Despite extensive translation, review and debugging work, mistranslations, missing lines, rendering glitches, texture problems, freezes or other undiscovered bugs may still exist.

## Testing and feedback

This is a personal project with limited test environments and playthrough coverage, so player testing is genuinely helpful. If you find any problem — on PCSX2 or on real hardware — please open a GitHub Issue and include the problem type, where it happened, how to reproduce it, your environment (emulator version and key settings, or console model), and screenshots or logs. Please do not upload full game ISOs, BIOS files or other copyrighted material.

## Download

Not released yet. Releases will provide the xdelta patch, a Windows installer and `SHA256SUMS.txt`. Release notes follow [docs/RELEASE_TEMPLATE.md](docs/RELEASE_TEMPLATE.md).

## What this is

A patch, not a game. No ISO, BIOS or game data is distributed here.

You need your own legally obtained Japanese copy of the game. The installer verifies the expected original checksum and refuses mismatched images.

## Distribution policy

Free to redistribute and mirror, always free of charge. Selling the patch, paywalling it, or claiming authorship is not allowed. Full terms: [DISTRIBUTION.md](DISTRIBUTION.md).

## Disclaimer

Unofficial fan work, provided as-is without warranty; back up your disc image and memory card first. Full terms: [DISCLAIMER.md](DISCLAIMER.md).

## License

Source code under `installer/` and `tools/` is MIT. Patches and release packages follow [DISTRIBUTION.md](DISTRIBUTION.md). See [LICENSE-SCOPE.md](LICENSE-SCOPE.md) for the boundaries.
