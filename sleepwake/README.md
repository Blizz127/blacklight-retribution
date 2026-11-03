# BL:R native sleep/wake (option B: exit-0 init wrapper), LIVE

This is the native side of `~/dev/dit-ffxi-bridge-integration-20261029/docs/stackd/NATIVE-SLEEP-WAKE-CONTRACT.md`.
DIT accepted option A on 2026-11-02, and the layout mirrors ROSE (`~/dev/rose-online/SLEEPWAKE.md`).

## Status (2026-11-02)
- **03:20:42Z:** blrevive restarted on the owner's yes with a plain `docker start` of the pinned ID.
  Pre-restart backup: `~/.local/state/blr-lead/backups/pre-restart-20261102T032035Z`.
- **03:22Z:** DRY-RUN monitor installed.
- **03:28Z:** watched sleep → wake → ready through stackd r40 PASSED. Sleep POST 03:28:00, exit 2 at 03:28:05.9,
  asleep at 03:28:16; wake POST 03:28:22, ready at 03:28:41, same Id.
- **03:40Z:** stackd r41+ live, with the demand touch and the re-pins. A real wake POST created `state/demand`;
  sleep_now returned 13 and the monitor reset its streak.
- **03:41:56Z:** `ENABLE` created; auto-sleep **ARMED**.
- **04:11:55Z:** first automatic sleep, confirmed marker by blr-idle-monitor; stackd r43 reads asleep.
- **04:17:22Z:** wake POST on r43 from the automatic-sleep marker: 202 starting, then 200 ready at 04:17:37Z.
  Same Id, new StartedAt.
- **14:25Z, OPTION B (on the owner's yes):**
  - The container was recreated with the `optionb/blr-init.sh` wrapper as PID 1, giving a new ID `0df4b910428c…`.
  - The old `7fad6a7a…` is kept as `blrevive-pre-optionb` with restart=no, as the rollback. Never `rm -v` it: the assets volume is anonymous.
  - stackd r45 (DIT) pins the new ID, the wrapper mount and the Entrypoint/Cmd, and expects exit 0. The exit-2 exception is gone.
  - The watched cycle passed: sleep at 14:28:14Z gave exit 0 with a confirmed marker, and stackd read asleep; the wake POST at 14:28:32Z was ready at 14:28:47Z.
  - The banshee-auth `bindings.json` write error is gone (starts at 14:25:59Z and 14:28:38Z).

Deployed sha256:
- `blr_stop.py` 3b8e9d57… (pinned by stackd; CONTAINER 0df4b910…, ACCEPTED_EXIT_CODE 0)
- `blr_sleep_now.py` 664b72c4… (pinned by stackd)
- `blr_idle_monitor.py` 838309ec…

stackd row: `readiness_only`, health `blacklight_start.py --sleep-health`, wake `--wake`, sleep = `blr_sleep_now.py`,
demand = `state/demand`.

## Owner controls
- **Disarm auto-sleep:** `rm ~/servers/blrevive-sleepwake/ENABLE`. The monitor keeps running in DRY-RUN.
- **Keep it awake:** press PLAY (or wake it) FIRST, then `touch ~/servers/blrevive-sleepwake/HOLD`.
  HOLD on a server that is already asleep means maintenance: stackd reads error, and the launcher will NOT wake it
  until HOLD is removed. Removing HOLD restores asleep, because the marker still matches.
- **Deploy work:** `~/servers/blrevive-sleepwake/run/deploy.lock` behaves the same as HOLD.

## Paths on Banshee (all owned by the lead)
| What | Path |
| --- | --- |
| Graceful stop | `~/servers/blrevive-sleepwake/blr_stop.py --mode sleep --execute --by <blr-stop\|blr-idle-monitor>` |
| Sleep button (stackd sleep argv) | `/usr/bin/python3 -I ~/servers/blrevive-sleepwake/blr_sleep_now.py` |
| Idle monitor (one tick) | `~/servers/blrevive-sleepwake/blr_idle_monitor.py` |
| Units | `~/.config/systemd/user/blr-idle-monitor.{service,timer}` (60 s) |
| Asleep marker (0644, atomic) | `~/servers/blrevive-sleepwake/state/asleep.json` |
| Monitor state | `~/servers/blrevive-sleepwake/state/monitor.json` |
| Hold (exists: never stop) | `~/servers/blrevive-sleepwake/HOLD` |
| Deploy lock (exists: never stop) | `~/servers/blrevive-sleepwake/run/deploy.lock` |
| Launcher demand hint (stackd touches it on every authenticated wake POST, i.e. each launcher PLAY, never on status reads; mtime ≤ 600 s old or in the future = never stop) | `~/servers/blrevive-sleepwake/state/demand` |
| Enable (exists: the monitor really sleeps) | `~/servers/blrevive-sleepwake/ENABLE` |
| Stop exclusion | flock `~/servers/blrevive-sleepwake/run/stop.lock` and DIT's `~/servers/blrevive/run/migration-wake.lock` |
| Stop receipts / backups (0700) | `~/.local/state/blr-lead/{stops,backups}/` |

The tools read only `docker inspect`, files under `~/servers/blrevive`, and `/proc/sys/kernel/random/boot_id`.
They never read `/proc` of a native process, so `sg docker` is enough.

## Marker
```json
{"schema":1,"state":"asleep","by":"blr-idle-monitor","container":"blrevive","container_id":"<full pinned id>",
 "boot_id":"<boot>","requested_at":1793590000.1,
 "confirmed":{"stop_rc":0,"exit_code":0,"finished_at":1793590001.2,"finished_at_raw":"<docker FinishedAt>",
   "humans_last_check":0,"botmatch_ok":true,"ratings_sha256":"<sha|absent>","backup_sha256":"<sha|absent>",
   "container_id_unchanged":true,"at":1793590001.5}}
```
- The marker is written WITHOUT `confirmed` just before `docker stop`, and rewritten with it only after every proof
  holds.
- `marker_state()` returns asleep only when all of these match: the stopped pinned container, ExitCode 0, and
  `finished_at_raw` equal to the container's current FinishedAt.
- A stop and restart by hand changes FinishedAt, so it can never read as asleep. Neither can the 11-01 incident stop,
  which has no marker.

## Stop sequence (`blr_stop.py`)
1. Take stop.lock and the wake lock (LOCK_NB). Refuse on HOLD or the deploy lock.
2. Observe and gate:
   - pinned container running;
   - `botmatch/status.json` no older than 60 s and written by this incarnation;
   - `humans == 0`, with bots never counted;
   - status.json and skill_ratings.json both parse.
3. Settle 5 s, then observe and gate again. Incarnation and ratings digest must be unchanged.
4. Back up `skill_ratings.json` privately and read the backup back.
5. Check the locks again, then observe and gate one last time.
6. Write the marker without `confirmed`. Then `docker stop -t 30 <id>`, never kill -9.
7. Prove the stop:
   - rc 0, same id, exited;
   - ExitCode exactly 0 (the option B wrapper);
   - FinishedAt in [requested_at, requested_at + 60 s];
   - status.json parses;
   - ratings digest unchanged and parses.
8. Rewrite the marker with `confirmed`.

If anything fails, it stops there with the marker unconfirmed, and stackd treats that as an error that is never woken.
Every run writes a receipt.

Why the wrapper: artemis-blrevive 1.4.0 has no signal handler. As PID 1 it exited 2 on SIGTERM (option A, retired). `blr-init.sh` is now PID 1: it forwards TERM and exits 0 only when a stop was requested and artemis ended by it.

Why it's save-safe:
- botmatch writes its JSON files to a temp file and then MoveFileExA(REPLACE_EXISTING).
- Ratings only change during duels with humans.
- Only an append-only `*.jsonl` log line can be torn.
- Player accounts live in zcure, which stays up.

Option B is LIVE (see the status above). The wrapper, plan and recreate tool are in `optionb/`.

## Sleep button exit codes (`blr_sleep_now.py`)
**It ignores all arguments, and every run is a REAL sleep request** (even `--help`). Use `blr_sleep_now.decide()` from Python for a read-only check. On 2026-11-02 at 14:28:09Z a stray `blr_sleep_now.py --help` started the option B test sleep. It was gated at 0 humans and harmless.

| Code | Output | Meaning |
| --- | --- | --- |
| 0 | `{"state":"stopping","eta_s":15,"job_unit":"blr-sleep-now-<ts>"}` | gates passed; the stop is detached via systemd-run |
| 2 | `{"state":"asleep"}` | already asleep |
| 10 | `{"humans":n}` | humans present |
| 11 | `{"code":"deploy_lock_present"}` | deploy lock |
| 12 | `{"code":"another_stop_in_progress"\|"wake_in_progress"}` | busy |
| 13 | `{"code":"hold_present"\|"recent_demand"}` | other refusal |
| 1 | `{"error":...}` | anything else, including unknown, stale or stopped without a valid marker |

## Monitor
Each tick runs the same observe and gate as the stop. 1200 s of unbroken proof (tick gap ≤ 180 s, same incarnation)
then does one of two things:
- without ENABLE, it logs `would_sleep` (DRY-RUN);
- with ENABLE, it runs `blr_stop` (by `blr-idle-monitor`).

## Wake
stackd runs DIT's pinned `blacklight_start.py`, which does a plain `docker start` of the pinned ID. Never use
`compose up`: the compose file has drifted from the running container, and a recreate changes the ID.

Tests: `python3 -B -m unittest -v test_blr_sleepwake` (33 fixture tests, no Docker; exit-code agnostic).

## Nightly backup and cleanup audit (2026-11-02)

Owner policy: no database migrations; preserve existing stores, use MariaDB for new stores.
Every server requires nightly NAS backups. Remove old local backups only after verifying
NAS coverage; retain current rollback, in-flight work and anything not yet verified.

Blacklight account/progression data is in MariaDB container `zcure-db`, schema `zcure_blr`.
The local `~/backups/zcure/zcure_blr/daily/2026-11-02.sql.gz` passed gzip validation and
has a completed dump footer and 17 InnoDB table definitions, including Users,
BansheeAccounts, Inventory, Loadouts and Stats. No player rows were printed.

Root-owned `/usr/local/libexec/backup-nas/backup-nas.sh` already discovers `zcure-db`
and streams `mariadb-dump --single-transaction --quick --routines --events --triggers
--hex-blob --all-databases` to restic path `/db/zcure-db.sql`. Its enabled timer runs
at 03:15 America/Chicago plus up to five minutes jitter. The November 2 log records
snapshot `2aa6d19f` saved at approximately 03:21 CST. This establishes successful
capture, not a tested ZCure restore. The separate local dump timer runs after NAS
backup; NAS coverage of the database uses its direct stream, not that local dump.

Backup-owner follow-up sent to the coordinator through its native inbox:
- Verify `/db/zcure-db.sql` contains `zcure_blr` and perform a scoped restore check.
  The current monthly restore test covers FFXI and SandalCore, not ZCure.
- Confirm/add file coverage for `/home/blizz/servers/blrevive/config`, especially
  `BLRevive/banshee_auth` and `botmatch`, plus `/home/blizz/servers/blrevive-sleepwake`
  and `/home/blizz/.local/state/blr-lead`. Sidecar file consistency must be accounted
  for separately from the transactional database dump; it is not proven here.
- Before cleanup, verify individual NAS path/content coverage of
  `/home/blizz/backups/zcure/zcure_blr`, `~/servers/blrevive/archive`,
  `~/servers/blrevive/rollout-backup-20260929-053723`, and old lead backups/releases.
  Files snapshot `0e429a91` exists, but that alone does not establish these paths.
- NAS retention reported `forget-db` and `forget-files` warnings involving lock
  `9ee9c310`. The shared backup owner must investigate; this lane did not unlock it.

Reading `/etc/restic/include.txt` and `exclude.txt` was permission-denied. No sudo,
credential access or permission workaround was attempted. Cleanup remains pending
verified NAS copies, not routine coordinator approval.

Retain `blrevive-pre-optionb` and its anonymous assets volume, data rollback
`~/.local/state/blr-lead/backups/optionb-20261102T142502Z`, prior tools
`~/.local/state/blr-lead/releases/20261102T142639Z`, and the newest local SQL dump.
All candidates were retained during this audit. **Freed: 0 bytes (0.000 GB).**
The source archive occupies about 0.112 GB; local SQL backups occupy about 0.005 GB.
Root filesystem observation: 627,971,174,400 bytes used of 972,760,846,336 (df: 69%).
No live data, containers, schedules, or remote backup files were changed.

## Lane-owned staging cleanup (2026-11-02)

Removed Banshee `~/.local/state/blr-lead/releases/optionb-build` (four files,
45,056 allocated bytes including directory). All four SHA256 hashes matched the
off-host committed copy at devbox Git commit `ea9d78f`; `git fsck` passed.
The staging directory was not mounted by either live or rollback container;
accessible process cwd/open-file checks found no use before removal.

On the devbox, removed `blr_idle_monitor.py`, `blr_sleep_now.py` and
`test_blr_sleepwake.py` from each of `rel/` and `optb/` under
`~/.cache/claude-tmp/claude-1000/-home-blizz-dev-blacklight-retribution/70baa612-20db-4300-b0ae-1aa7403ecbe0/scratchpad/`.
Each file was byte-compared to its retained Git blob before unlinking.
Devbox allocated bytes removed: 49,152. Both staged `blr_stop.py` variants differ
from the committed version and were retained, along with unique scratch/evidence.

Total allocated bytes removed: **94,208 (0.000094208 decimal GB)**.
This is the allocation of deleted entries, not a host-wide df delta; other lanes
are concurrently reclaiming space. No dead project worktrees were present.
No live data, newest rollback, backup configuration, other-lane bot builds,
or historical backups lacking NAS verification were deleted. The older NAS
verification request to banshee-realm-client remains pending.

## NAS-verified historical cleanup (2026-11-03)

The owner relayed banshee-realm-client's path receipts for files snapshot
`e1ca0b29` and database snapshot `9bac58f1`, then authorized covered cleanup.
Removed the covered historical archive contents (excluding all `.git/logs`
entries), September rollout backup, superseded lead backups/releases and older
SQL dumps. Kept the option B data rollback, `20261102T142639Z` tools rollback,
newest ratings backup `20261102T150014Z`, and newest SQL `2026-11-02.sql.gz`.
Live config, containers and anonymous assets volume were untouched.

Deletion checked candidate timestamps against the snapshot, container mounts,
accessible process references, and inactive NAS/local database backup services.
Removed 2,214 files and 394 empty directories; allocated bytes reclaimed:
**116,150,272 (0.116150272 GB)** on Banshee. This accounts for hardlinks and
removed directory allocation rather than attributing concurrent host-wide df
changes to this lane. Full path receipt: `../audit/cleanup-20261103.json`.

Post-check: confirmed asleep marker remains valid; live and rollback containers
remain exited with their expected exit codes; retained SQL passes gzip validation.
All 31 uncovered reflog files remain with their parent directories. No new devbox
cleanup in this pass. Including prior staging cleanup, cumulative allocation
removed is **116,244,480 bytes (0.116244480 GB)** across both hosts.
ZCure dump content is verified, but its actual restore test remains realm-client's
open TODO. No backup-script or database-schema changes were made here.
