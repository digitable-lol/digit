---
title: "Server Backups — Use when backing up a server: stream, verify, key off-box"
sidebar_label: "Server Backups"
description: "Use when backing up a server: stream, verify, key off-box"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Server Backups

Use when backing up a server: stream, verify, key off-box.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/software-development/server-backups` |
| Version | `1.0.0` |
| Author | Digit |
| License | MIT |
| Platforms | linux, macos |
| Tags | `backup`, `restore`, `gpg`, `encryption`, `rsync`, `tar`, `ops`, `disaster-recovery` |
| Related skills | [`systematic-debugging`](/user-guide/skills/bundled/software-development/software-development-systematic-debugging), [`digitable-engineering-docs`](/user-guide/skills/bundled/software-development/software-development-digitable-engineering-docs) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Digit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Server Backups

## Overview

A backup is not a file you produced. It is a restore you have performed. Most
backup work fails at one of three points that all *look* like success: the
archive was written (but never opened), the key was moved (but never checked),
the job was scheduled (but silently covers half the data).

This skill encodes the order of operations that makes those three failures
impossible to miss, and the arithmetic that catches a partial copy.

## When to Use

- Taking a copy of a live server, VPS, or container host
- Moving a decryption key off the machine it protects
- Reviewing a backup someone else set up ("do we have backups?" → this skill)
- Writing a scheduled backup job
- After any incident where restoring was, or nearly was, required

**Don't use for:** database replication or point-in-time recovery (different
problem — a snapshot of `/var/lib/mysql` taken from a running server is
usually corrupt); source code (that's what the remote git repo is);
object-storage lifecycle rules.

## The three rules

### 1. Stream — never stage

Compress at the source, encrypt in the middle, write at the destination. No
temporary archive anywhere.

```bash
ssh SRC "cd / && nice -n 19 tar --warning=no-file-changed --numeric-owner \
         -cf - PATH1 PATH2 2>/dev/null | nice -n 19 gzip -1" \
| gpg --batch --yes --symmetric --cipher-algo AES256 \
      --pinentry-mode loopback --passphrase-file KEYFILE \
| ssh DST "cat > /backups/NAME-$(date -u +%Y%m%dT%H%M%SZ).tar.gz.gpg"
```

Staging is what kills small servers: the machine you most want to back up is
the one with 16 GB free and 2 GB of RAM, and a 500 MB temporary archive is
how you turn a backup into an outage.

Pick the compression level from the *source's* constraints, not the
destination's disk price. `gzip -1` on a two-core box that has OOMed before;
`-9` only when the source is idle and the link is the bottleneck.

`--warning=no-file-changed` matters: on a live server files change under tar,
and without it tar exits non-zero on a backup that is actually fine.

### 2. Prove before you destroy

Any step that removes the only copy of something — a key, an old archive, the
source data — runs **after** a check that proves the new copy is good, never
before, and never in the same command.

```bash
# fetch
ssh REMOTE "sudo cat $SRC" > "$DST.tmp"
[ -s "$DST.tmp" ] || { rm -f "$DST.tmp"; exit 1; }

# prove
REMOTE_SUM=$(ssh REMOTE "sudo sha256sum $SRC" | awk '{print $1}')
LOCAL_SUM=$(sha256sum "$DST.tmp" | awk '{print $1}')
[ "$REMOTE_SUM" = "$LOCAL_SUM" ] || { rm -f "$DST.tmp"; exit 1; }
mv "$DST.tmp" "$DST"

# only now destroy
ssh REMOTE "sudo shred -u $SRC"
```

Completion criterion: the destructive command is unreachable unless the
comparison succeeded. If you can reorder the script and it still runs, the
guard is decorative.

### 3. Restore-test, or it is not a backup

Immediately after writing an archive, prove all four:

```bash
gpg --batch --decrypt --pinentry-mode loopback --passphrase-file KEY ARCHIVE \
  | tar -tzf - > /tmp/list.txt          # 1. decrypts  2. unpacks
awk '!/\/$/' /tmp/list.txt | wc -l      # 3. entry count vs source
gpg ... | tar -xzOf - PATH/TO/FILE | sha256sum   # 4. content, vs source sha256
```

Count alone is not enough — an archive of 16 000 zero-byte files has the right
count. Checksum one real file whose bytes you can compare at the source.

## Reconciling the count

Expect the counts to differ, and explain the difference — never round it off.

| Source command | Counts | Misses |
|---|---|---|
| `find … -type f` | regular files | symlinks, dirs, sockets, FIFOs |
| `tar -tzf` | every entry | — (dirs end in `/`) |

So `tar` entries minus directories will normally exceed `find -type f` by
exactly the number of symlinks. Verify that:

```bash
ssh SRC "find PATHS -type l | wc -l"
```

If the delta is 1 and there is 1 symlink, the archive is complete. If the
delta is unexplained, stop — you have a partial copy, and the arithmetic just
told you so. This is the difference between a verified backup and a hopeful
one.

## Where the key lives

Two schemes. Choose deliberately and write the choice down.

**Symmetric** (`--symmetric`, passphrase). One secret; whoever holds it can
both make and read backups. Correct only when the key leaves the machine
immediately after the run. Its failure mode: the passphrase sits on the same
host as the archive or the source, so one compromise yields both.

**Asymmetric** (`--encrypt --recipient`). The *public* key lives on the
server; the private key never touches it. This is the right default for
anything scheduled — the machine can write backups every night and cannot
read a single one, and neither can whoever takes the machine.

```bash
# once, on the operator's own machine:
gpg --quick-generate-key "backup@example" default default never
gpg --armor --export backup@example > pub.asc      # this file is not a secret

# on the server, after importing pub.asc:
... | gpg --batch --encrypt --trust-model always --recipient backup@example | ...
```

Trade-off to state out loud when you pick symmetric: once the passphrase is
off the box, **the box can no longer verify its own backups.** Restore-testing
becomes a human's job on a schedule, and if nobody owns that schedule, rule 3
is not being followed.

## Say what is not in the copy

A backup with an unstated hole is worse than a known-partial one, because it
buys confidence it has not earned. Enumerate the exclusions in the same place
the backup is documented:

- **No sudo at the source** → `/etc` (web-server config, cron, units) and
  `/var/lib/mysql` are unreadable. The sites come back; the thing that serves
  them does not.
- **Live database files** → present but likely inconsistent; needs
  `mysqldump`/`pg_dump`, not `tar`.
- **Deliberate excludes** → analytics counters, caches, `.htaccess` written by
  a control panel. Each `--exclude` is a decision; comment why.

Check for the hole rather than assuming:

```bash
for p in /etc /var/lib/mysql /opt /home; do
  [ -r "$p" ] && [ -x "$p" ] && echo "readable $p" || echo "NO ACCESS $p"
done
```

## Keyless integrity

The archive must stay checkable by whoever holds no key — that is normally the
machine storing it. Write a checksum next to it at creation:

```bash
cd /backups && sha256sum *.gpg >> SHA256SUMS && chmod 600 SHA256SUMS
sha256sum -c SHA256SUMS      # later, catches bit-rot and truncated transfers
```

This detects corruption. It does not detect that the archive was never
restorable — only rule 3 does that.

## Common Pitfalls

1. **Green job, empty archive.** `set -o pipefail` is missing, so a failed
   `tar` at the head of the pipe is masked by a successful `cat` at the tail.
   Add it, and check the size is in the expected order of magnitude.
2. **`rm` before the check.** Cleanup written above the verification, or in
   the same `&&` chain, so a partial transfer still deletes the original.
3. **Counting instead of reconciling.** "16053 vs 16052, close enough."
   Explain the one, or you don't know what else is missing.
4. **Restore never attempted.** The archive has existed for a year; nobody has
   opened it. Schedule the restore-test, not just the backup.
5. **Key beside the lock.** Passphrase file in the same home directory, same
   host, or same backup as the encrypted archive.
6. **Backing up a symlink farm.** Without `-h`/`--dereference` you archive the
   links; with it you may archive the same tree many times. Decide, and check
   the resulting size against the source.
7. **Silent partial scope.** The job backs up `/www` and the server also runs
   things from `/opt` and `/srv`. Enumerate mount points and web roots at the
   source instead of trusting the last person's path list.
8. **Compression chosen for the wrong machine.** `-9` on a two-core VPS turns
   a 40-second backup into a ten-minute CPU-starvation event.

## Verification Checklist

- [ ] Archive exists at the destination, size within an order of magnitude of
      the expected compressed size
- [ ] `sha256sum -c SHA256SUMS` passes at the destination
- [ ] Archive decrypts
- [ ] Archive unpacks (`tar -tzf` exits 0)
- [ ] Entry count reconciled against the source, including symlinks
- [ ] At least one real file's sha256 matches the source
- [ ] Exclusions written down where the backup is documented
- [ ] Key is not on the machine holding the archive, nor on the source
- [ ] Destructive steps in the script are unreachable unless a check passed
- [ ] Someone owns the restore-test schedule, by name

## One-Shot Recipes

**Copy a small server's web roots, encrypted, to a big host**

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
ssh SRC "cd / && nice -n 19 tar --warning=no-file-changed --numeric-owner \
         -cf - www/wwwroot opt/app 2>/dev/null | nice -n 19 gzip -1" \
| gpg --batch --yes --encrypt --trust-model always --recipient backup@example \
| ssh DST "cat > /backups/site-$TS.tar.gz.gpg"
ssh DST "cd /backups && sha256sum site-$TS.tar.gz.gpg >> SHA256SUMS"
```

**Move a passphrase off a server, safely**

Fetch → compare sha256 → `shred -u` only on match. Full pattern in rule 2.
Prompt before the destructive step; it is the only copy.

**Audit someone else's backup claim**

1. Find the archive. If nobody can name the path, there is no backup.
2. `sha256sum -c` at rest.
3. Restore it somewhere empty and diff a subtree against production.
4. List what is *not* in it, and say so in writing.
