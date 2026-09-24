# OATH

**OATH Allows Total History-transfer.** One tool to rule them all.

An open-source terminal app for moving your own WhatsApp chat history between an Android phone and an iPhone, without paid software.

> **Status: alpha, validated on a real device end to end.** Android -> iPhone conversion has completed a full real-world run: a real crypt15 WhatsApp export (1622 chats, 64k+ messages, 11.5k+ media files) was converted, injected into a real iPhone's backup, and **successfully restored via Apple's own MobileBackup2 restore protocol** (`Restoring finished`, no errors). See [docs/RESEARCH.md](docs/RESEARCH.md) for the real-device findings, including two Manifest.db record-encoding bugs that silently fail Apple's restore validator and how they were diagnosed and fixed.

## What works today

- Decrypt a real-format crypt15 Android backup with your 64-digit key (via [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools)) and read chats, groups, media, quotes, stars and `@lid` senders.
- Inject those chats into a **copy** of an unencrypted iPhone backup: Core Data keys and counters maintained, media added, `Manifest.db` sizes and digests kept consistent, safe to re-run.
- Produce a backup that Apple's own restore mechanism (Finder / `pymobiledevice3 backup2 restore`) accepts and restores without corruption errors — confirmed on a real iPhone, real iOS 27.
- Not carried over yet: quoted-reply links and starred flags on iOS. Reported as warnings.
- iPhone -> Android is not implemented yet.

## Emulator

`oath emulate` builds a fake Android WhatsApp folder (real crypt15 format) and a fake iPhone backup, so the whole pipeline can be exercised without touching an account. The emulated iPhone schema is an approximation. The first real-device run is expected to need adjustments.

## Planned workflow

1. Scan: detect `adb`, libimobiledevice and connected phones.
2. Read: decrypt an Android backup or parse an iPhone backup, then show a dry-run summary (chats, messages, media).
3. Convert: map messages into the target platform's format.
4. Write: build a backup the target phone restores, always working on copies of your originals.

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- `adb` (Android platform-tools)
- libimobiledevice (`idevicebackup2`, `idevice_id`)

## Usage

```bash
uv sync
uv run oath doctor                       # tool and device check
uv run oath emulate /tmp/oath-demo       # synthetic Android data + iPhone backup
uv run oath inspect android /tmp/oath-demo/android/WhatsApp --key <64-hex>
uv run oath convert --android /tmp/oath-demo/android/WhatsApp --key <64-hex> \
    --ios-backup /tmp/oath-demo/ios-backup --out /tmp/oath-demo/ios-converted
uv run oath          # launch the TUI (device check for now)
uv run pytest        # run tests
```

## Safety

- Everything runs locally. OATH sends no data anywhere.
- OATH is meant for your own account and your own data only.
- Restoring a backup to an iPhone erases and replaces its contents. Always make a verified backup of both phones first.

## Legal notice

WhatsApp is a registered trademark of Meta Platforms, Inc. OATH is an independent open-source project. It is not affiliated with, authorized, sponsored, or endorsed by Meta Platforms, Inc. or WhatsApp LLC. All product names and trademarks belong to their respective owners and are used here for identification only.

Use of OATH may be restricted by WhatsApp's Terms of Service. You are responsible for how you use it.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
