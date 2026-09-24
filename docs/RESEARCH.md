# OATH: Research Findings

Date: 2026-09-19, updated 2026-09-24 after the first successful real-device restore.
Legend: [V] verified from a fetched source, [K] background knowledge, [U] uncertain / needs device test, [D] verified against a real device.

## 0. Verdict

Both directions are feasible in principle, but both rest on undocumented formats that drift with every WhatsApp release.
No maintained open-source tool exists (the two converters are archived since 2021-2023), which is the gap for this project.

| Direction | Approach | Root needed | Main risk |
|---|---|---|---|
| Android -> iOS | Convert msgstore -> ChatStorage.sqlite, inject into an unencrypted iPhone backup, restore | No | Core Data model match, Manifest.db integrity, restore regressions on iOS 17.4+ [V] |
| iOS -> Android | Build a WhatsApp-native local backup (crypt15 with our own 64-digit key) and let WhatsApp restore it | No | Whether current WhatsApp accepts a self-built crypt15 [U] |

First thing to check: whether WhatsApp's 2026 official changes remove the "before activation / fresh phone" limit [U]. If they do, the project's reason to exist shrinks.

## 1. Android internals

- Private data (root only): `/data/data/com.whatsapp/databases/{msgstore.db,wa.db}`, `files/key` (crypt12/14), `files/encrypted_backup.key` (crypt15) [V].
- Shared storage (adb-readable, no root): `/sdcard/Android/media/com.whatsapp/WhatsApp/{Databases,Backups,Media}` [V]. About 7 dated local backups are kept.
- Key files are different sizes: crypt12 key = 158 bytes [K]; crypt14 key = 131-byte Java-serialized blob [V]; crypt15 uses `encrypted_backup.key`. Do not treat them as one thing.
- File layout (crypt14/15) [V, wa-crypt-tools source]:
  1. protobuf varint header length
  2. protobuf header (crypt15: IV in `C15_IV` inside `e2ee_key_data`)
  3. AES-256-GCM ciphertext
  4. 16-byte GCM tag
  5. 16-byte MD5 checksum (absent on multifile backups)
- Plaintext is zlib-compressed SQLite, or a ZIP for multifile backups. Decrypt, then decompress.
- Crypt15 key derivation [V, key15.py]: 32-byte root key (= the 64 hex digits) -> HMAC-SHA256 "encryption loop" with fixed info strings `backup encryption`, `metadata encryption`, `metadata authentication`. Not standard HKDF. The header supplies only the IV.
- Password-mode E2EE backups: root key is server-wrapped (HSM/OPRF) [K], so probably not decryptable offline. Users must choose the 64-digit key.
- Non-root extraction, ranked:
  1. Local backup with E2EE and a user-saved 64-digit key, then `adb pull` and `wa-crypt-tools`. Recommended.
  2. Plain crypt14 local backup: dead end without the key file (needs root).
  3. Google Drive: no verified downloader, likely ToS-violating. Out of scope.
  4. Legacy APK + `adb backup`: works only up to Android 13 [V], blocked by downgrade checks after. Out of scope.
  5. Rooted device: copy key and DB directly. Advanced option only.
- Schema (modern) [V]: `message`, `chat`, `jid`, `jid_map` (LID <-> phone JID), `message_media`, `message_quoted`, `message_thumbnail`, `message_add_on` (+ `_reaction`), `group_participant_user`, `message_system`. Timestamps are Unix ms. Chats may be keyed by `@lid`, so always resolve through `jid_map`. Derive the schema from a real DB (`sqlite_master`, `PRAGMA user_version`), never hard-code it.
- Enums: `message_type` 0 text, 1 image, 2 audio, 3 video, 4 contact, 5 location are reasonably confirmed; 9 document, 13 gif, 20 sticker, 7 system are [K]. `status` values are unverified [U].
- Restore into Android WhatsApp: file goes in `Databases/` with the correct extension, same phone number, WhatsApp asks for the 64-digit key, copy `Media/` first. Remove Drive backups so WhatsApp offers the local one. Version rule is probably target >= source [U].

## 2. iOS internals

- Container: app group `group.net.whatsapp.WhatsApp.shared`; backup domain `AppDomainGroup-group.net.whatsapp.WhatsApp.shared` [V].
- Files: `ChatStorage.sqlite` (+ `-wal`, `-shm`), `ContactsV2.sqlite`, `Message/Media/<jid>/...`, `Media/Profile/`. Media prefix (`Media/` vs `Message/Media/`) changed across versions [U].
- Core Data tables: `ZWAMESSAGE`, `ZWACHATSESSION`, `ZWAMEDIAITEM`, `ZWAGROUPINFO`, `ZWAGROUPMEMBER`, `ZWAMESSAGEINFO`, `ZWAPROFILEPUSHNAME`, `ZWAMESSAGEDATAITEM`, plus `Z_PRIMARYKEY`, `Z_METADATA`, `Z_MODELCACHE` [V/K].
- Hard rules when writing rows [V/K]:
  - Update `Z_PRIMARYKEY.Z_MAX` for every entity touched, or the next insert collides.
  - `Z_ENT` must match the entity id, and `Z_OPT` should be sensible (1 or 2).
  - `Z_METADATA.Z_PLIST` holds model version hashes. A mismatch with the app's `WhatsAppChat.momd` triggers migration or failure. watoi issue #60 shows this happening [V].
  - Safest strategy: seed from the target iPhone's own `ChatStorage.sqlite` and change only data rows.
- Dates: Apple epoch seconds (Unix - 978307200) [V].
- `ZMESSAGETYPE`: 0 text, 1 image, 2 video, 3 audio, 4 contact, 5 location, 6 group/system, 7 URL, 8 document [V]; others [U]. Unknown types should fall back to a text placeholder.
- JIDs: `<number>@s.whatsapp.net`, groups `@g.us`, status `status@broadcast`; `@lid` interaction with iOS is unknown [U].
- Likely required consistency: valid `ZCHATSESSION` FK, unique non-null `ZSTANZAID`, monotonic `ZSORT`, consistent `ZMESSAGECOUNTER`/`ZUNREADCOUNT`, group rows in `ZWAGROUPINFO`/`ZWAGROUPMEMBER` [K, U]. Confirm by diffing a real database and reading Console.app crash logs.
- Media: without local files WhatsApp shows placeholders. Copy files into the container and set `ZMEDIALOCALPATH` [V, watoi].
- Backup format [V/K]:
  - `Manifest.db` `Files` table: `fileID = sha1(domain + "-" + relativePath)`, blob at `<fileID[:2]>/<fileID>`, `flags` 1 file / 2 dir / 4 symlink, `file` = NSKeyedArchiver bplist (Size, Mode, Digest, ...).
  - When replacing a file, update Size and Digest. Whether the device verifies them is unknown [U].
  - Encrypted backups need keybag/PBKDF2 handling. Plan v1 around unencrypted backups only.
- Restore tooling: `idevicebackup2 restore` (libimobiledevice) or `pymobiledevice3 backup2`. Find My must be off.
- Reported restore failures [V]: restore reaches 100% but restores nothing on iOS 17.4.1 (libimobiledevice #1548); SSL error -4 (#1386); domain-name error 205 on iOS 18.1 beta (Nugget #18). Offer a manual Finder / Apple Configurator path as a fallback.
- Sequence that has worked historically (watoi guide): install and register WhatsApp on the iPhone, take an unencrypted backup, swap `ChatStorage.sqlite`, restore. Restore erases the device.

## 3. Prior art

| Project | Does | License | State |
|---|---|---|---|
| KnugiHK/Whatsapp-Chat-Exporter | Parses Android (plain, crypt12/14/15) and iOS backups, exports HTML/JSON | MIT | Active. Best reference for reading both schemas |
| ElDavoo/wa-crypt-tools | Decrypts and encrypts crypt12/14/15, OCR of key screenshots | GPL-3.0 | Maintained. Issue #123 asks whether crypt15 decryption broke [U] |
| residentsummer/watoi | Android -> iOS conversion via the iOS Core Data model | GPL-3.0 | Stale since Nov 2021. Media not imported |
| Kethen/WhatsAppIphoneToAndroid | iOS -> Android; writes a `/sdcard/WhatsApp` tree that WhatsApp restores itself | GPL-3.0 | Archived Aug 2021, alpha |
| KnugiHK/WhatsApp-Key-DB-Extractor | Legacy-APK key extraction | GPL-3.0 | Android 4-13 only |
| needs-coffee guide | Step-by-step around watoi | Guide | Archived Jun 2023 |

- License consequence: linking or copying the GPL tools makes OATH GPL-3.0. Recommendation: GPL-3.0, use `wa-crypt-tools` as a dependency, reimplement schema mappings from real databases, and keep provenance notes.
- Commercial tools (Tenorshare, MobileTrans, Backuptrans, Dr.Fone): no independent technical write-ups found. Backuptrans needs USB debugging and iTunes, consistent with backup-and-restore. Mechanism otherwise unverified [U].
- Official migrations: Android -> iOS needs Move to iOS, iOS 15.5+, a fresh or reset iPhone, same number, before activation [V]. iOS -> Android needs a fresh or reset Android (12+, Samsung 10+), cable, QR pairing, before activation [V].

## 4. Legal / ToS (not legal advice)

- WhatsApp ToS prohibits reverse engineering and derivative works. Realistic user risk is account restriction. Reader-only offline converters differ from unofficial clients, but the risk is not zero.
- DMCA 1201 is a gray area for decrypting your own backup; EU Software Directive Art. 6 and GDPR Art. 20 support interoperability and portability.
- Naming: avoid WhatsApp trademarks in the name. "OATH" is close; consider a name without "Whats" before publishing.
- Do not ship WhatsApp APKs or IPAs. Users fetch their own.

## 5. Design principles

1. Local only, no telemetry.
2. Never modify originals. Work on copies, verify checksums, keep a rollback.
3. Dry-run summary (chats, messages, media) before any write.
4. Require and verify backups of both phones before any restore.
5. Prefer producing artifacts WhatsApp restores itself over touching private app data.
6. Pin supported WhatsApp/iOS versions, keep per-version schema fixtures, fail closed on unknown schema.
7. Official migration check first; offer conversion only where it does not apply.
8. Wipe temp files and keys on exit.

## 6. Proposed architecture

Pipeline: extract -> decrypt/decompress -> parse to a canonical model (chats, participants, messages, media, reactions, quotes) -> writer for the target platform -> verify.
- Readers: Android (`wa-crypt-tools` + msgstore), iOS (backup + ChatStorage).
- Writers: iOS backup injector (seeded from the target's own DB); Android crypt15 backup builder.
- TUI (Textual): device check, source selection, dry-run report, confirmation gates, progress.

## 7. Open questions requiring real devices

1. Does a self-built crypt15 restore on current Android WhatsApp? (header fields, MD5, feature flags) [U] — still open; iOS -> Android direction not built yet.
2. Does iOS verify Manifest.db Size/Digest at restore? **[D] Yes — strictly.** A single pre-existing, unrelated-to-WhatsApp Size mismatch anywhere in the backup fails the *entire* restore with "the backup was corrupt" in Finder. See section 9.
3. Minimum consistent set of columns and Z_MAX handling to avoid crashes. [D] Resolved for the write path in `oath/ios/writer.py`; validated by a real restore accepting the resulting `ChatStorage.sqlite`.
4. LID vs phone JID handling on both platforms. [D] Real `ZSESSIONTYPE` values go beyond the assumed 0=individual/1=group: 2 (broadcast/status), 4 (some `@g.us` groups), 5 (`@newsletter`/Channels) were observed on a real device. OATH now skips-and-warns on session types it doesn't recognise rather than guessing.
5. Restore reliability on iOS 17.4+ and iOS 26 with each tool. [D] Confirmed working restore on iOS 27.0 (beta) with `pymobiledevice3 backup2 restore`, after fixing the two Manifest.db encoding bugs in section 9.
6. Authoritative `message_type` / `status` enums for current versions. [D] `15` = sticker confirmed against a real device backup; added to `TYPE_FROM_IOS`.
7. Do the 2026 official changes remove the fresh-device limit? [U] — not re-checked since first written.
8. Does `wa-crypt-tools` still decrypt current crypt15 (issue #123)? **[D] Yes**, decrypted a real, current-format crypt15 export without modification.

## 9. Real-device restore failures and root causes (2026-09-24)

The first end-to-end real-device run took several iterations to get past Apple's restore
validation. Every failure was root-caused with real evidence (never guessed) before being
fixed. In order of discovery:

1. **`plistlib.UID` field resolution.** Manifest.db's per-file records are NSKeyedArchiver
   plists. `plistlib.loads()` does not dereference `UID` objects automatically — reading a
   record's `Digest` field without resolving the UID reference produced ~19k false-positive
   "digest mismatch" reports from OATH's own `verify()`. Fixed by resolving `UID` references
   against `$objects` (`UID.__index__` makes `objects[uid]` work once you know to do it).

2. **Absent `Digest` is normal, not an error.** The large majority of files in a real backup
   (17490/19334 sampled) have no `Digest` key at all. `verify()` was comparing a missing key
   against a computed hash and always failing. Fixed to only check when the key is present.

3. **"Could not restore ... because the backup was corrupt" (Finder's own error).** Root
   cause: 243 pre-existing files, unrelated to WhatsApp, had a `Size` in Manifest.db that
   didn't match the real file on disk (normal drift — some files are captured mid-write, e.g.
   active SQLite journals). OATH's own `verify()` treated this as informational; Apple's real
   restore validator refuses the *whole* backup over even one such file. Fixed by adding
   `IOSBackup.repair_metadata()`, which patches only `Size`/`Digest` on the existing record,
   in place.

4. **First `repair_metadata()` was destructive.** The first version rebuilt each patched
   record from scratch via the same helper used to create new records, which silently
   dropped fields the original record had — `ProtectionClass`, `InodeNumber`,
   `Birth`/`LastModified`/`LastStatusChange`, `ExtendedAttributes`. This surfaced as a device
   -side restore failure: `ErrorCode 4, linkat() ... No such file or directory` for the
   specific file whose record had been rebuilt. Fixed by patching `Size`/`Digest` inside the
   existing NSKeyedArchiver structure in place, never rebuilding the record.

5. **`ErrorCode 205: Multiple errors downloading files` — the big one.** After the fixes
   above, `verify()` reported 0 problems and Finder's specific corruption error was gone, but
   the restore still failed, generically, at ~7-13% with no file named. Diagnosed by
   capturing the device's own live syslog (`pymobiledevice3 syslog live`) in parallel with a
   restore attempt (its `<private>`-redacted per-app OSLog output doesn't help, but kernel
   `AKSAnalytics` and `BackupAgent2` FAULT/ERROR lines do). Two distinct structural bugs in
   `mbfile_plist()` (used to build the record for every file OATH writes or rewrites), both
   affecting every one of the 11543 files OATH had written in the test corpus:
   - **Digest encoded inline instead of as a UID reference.** Real records store `Digest` as
     a `UID` pointing to a separate `$objects` entry holding the raw bytes; OATH's own writer
     inlined the bytes directly into the record dict. This round-trips fine through OATH's own
     reader (`plistlib` resolves either shape), but Apple's real `NSKeyedUnarchiver` requires
     an object reference for `decodeObjectForKey:` and rejects an inlined data literal with
     `*** -[NSKeyedUnarchiver decodeObjectForKey:]: value for key (Digest) is not an object`,
     visible in the device's own syslog (`BackupAgent2` process) but *not* surfaced in the
     MobileBackup2 protocol's own terse `{ErrorCode, ErrorDescription}` error.
   - **Missing `ProtectionClass` on file records.** Every real WhatsApp file record sampled
     (500/500) carries `ProtectionClass = 3`
     (`NSFileProtectionCompleteUntilFirstUserAuthentication`); OATH's writer set none.
     The device defaulted to setting protection class 0 on restore, which is invalid for a
     file (0 is only valid for directories) and failed with `NSPOSIXErrorDomain Code=22`
     (`EINVAL`), visible in syslog as `Setting protection class to 0` immediately followed by
     `Failed to open ...`.

   Both are fixed in `mbfile_plist()`. After the fix, all three markers held on a fresh
   build: `verify()` = 0 problems, all 11543 written files carry a UID-referenced `Digest`
   and `ProtectionClass = 3`, and the restore completed to 100% with `EXIT: 0` and no errors.

**Lesson for anyone building on this:** Apple's real restore-time `NSKeyedUnarchiver` is far
stricter about Manifest.db record *shape* than either `plistlib`'s round-trip or OATH's own
`verify()` can catch on their own — matching the real device's exact encoding (UID references
for object-typed fields, all the fields a real record of that kind carries) matters more than
getting the file's bytes right. The device's own live syslog is the only place that names the
real cause when the MobileBackup2 protocol error is generic (`ErrorCode 205`); the two
specific failures above (`ErrorCode 4`/`linkat()` and `ErrorCode 205`) were each only
root-caused after correlating a live syslog capture with the exact moment of failure.

## 8. Suggested milestones

- M0: reader + report only. Decrypt an Android backup, parse an iOS backup, show counts. Zero writes.
- M1: canonical model + fixtures from real test accounts.
- M2: Android -> iOS writer against a test iPhone.
- M3: iOS -> Android crypt15 builder against a test Android phone.
- M4: TUI polish, docs, version pinning.

## Source list

wa-crypt-tools (github.com/ElDavoo/wa-crypt-tools, incl. key15.py, db14.py, db15.py, issue #123); KnugiHK/WhatsApp-Chat-Exporter; KnugiHK/WhatsApp-Key-DB-Extractor; residentsummer/watoi (+ issue #60); Kethen/WhatsAppIphoneToAndroid; needs-coffee/Whatsapp-android-to-ios-guide; thebinaryhick.blog 2022/06/09 msgstore write-up; andreas-mausch/whatsapp-viewer schema; group-ib.com whatsapp-forensic-artifacts; belkasoft iOS WhatsApp forensics; kacos2000/queries WhatsApp_Chatstorage_sqlite.sql; richinfante.com iOS backup reverse engineering; libimobiledevice issues #1548, #1283, #1386; leminlimez/Nugget #18; WhatsApp FAQ 686469079565350, 1295296267926284, 530788665445780; Google Android help 14669513; gbyte.com whatsapp-local-backup.
