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
- `blr_sleep_now.py` 029251b2… (deployed 2026-11-03; pinned by stackd r58)
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
**Deployed version `029251b2` handles help without requesting sleep.** The previous `664b72c4` ignored all arguments. Use `blr_sleep_now.decide()` for read-only state checks. On 2026-11-02 at 14:28:09Z a stray `--help` invocation started the option B test sleep.

The deployed fix parses arguments first: `--help`/`-h` print usage and exit 0; unsupported arguments print a usage error and exit 2, without host checks, Docker inspection, locks or stop dispatch. No-argument invocation retains the existing stackd contract below. CLI usage exit codes apply only when arguments are supplied.

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

Tests: `python3 -B -m unittest -v test_blr_sleepwake` (35 fixture tests, no Docker; exit-code agnostic).

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
credential access or permission workaround was attempted. At this audit checkpoint, cleanup awaited
verified NAS copies, not routine coordinator approval. That dependency was later
resolved in the November 3 NAS-verified cleanup recorded below.

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
verification request was still pending at this checkpoint; see the completed
November 3 NAS-verified cleanup below.

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

## Monitor reconciliation and sleep-button CLI fix (2026-11-03)

Read-only monitor check: timer active, ENABLE present, no HOLD/deploy lock;
confirmed automatic sleep by `blr-idle-monitor` at `2026-11-03T05:28:54.007849122Z`,
exit 0. Deployed hashes remain the recorded stop `3b8e9d57`, sleep button
`664b72c4` and monitor `838309ec`. The dry-run/live cutover is already complete.
DIT was sent this evidence to reconcile the stale review checkpoint.

Local sleep-button fix SHA256:
`029251b221f47ad9981fd4dd88e3894ce1f54c7361adbca1b6e977399f010264`.
Validation: five focused tests (`SleepNowCLI` and `SleepNow`) passed; subprocess
calls were mocked or fixture-based, with no live server action or real save access.
New CLI tests exercise help, unknown arguments and unchanged no-argument dispatch.
At this local-test checkpoint deployment awaited a coordinated DIT stackd
hash re-pin. The subsequent deployment and r58 confirmation below close it. No build/full suite
was run under high devbox load.

## CLI fix deployment (2026-11-03 05:46:47Z)

DIT explicitly sequenced native deployment before its staged re-pin. The new
`blr_sleep_now.py` is deployed with verified SHA256 `029251b221f47ad9981fd4dd88e3894ce1f54c7361adbca1b6e977399f010264`.
Backup: `~/.local/state/blr-lead/releases/sleep-cli-20261103T054647Z`
(previous button and systemd unit, copied and byte-verified before replacement).
Keep this as the newest CLI rollback in addition to the option B container/data
rollback. Stop/wake exclusion locks covered the file swap.

The unit description now says ENABLE controls automatic sleep. Only a user
systemd daemon-reload was needed; the timer stayed active. On-host help returned
0, an invalid argument returned 2, and the container ID/status/start/finish times
remained unchanged. No game restart, wake, sleep or save modification occurred.

Deployment receipt and full hash were sent directly to DIT and the coordinator.
DIT confirmed stackd r58 active since 05:47:09Z: the only change is the
Blacklight sleep-button pin, verified on disk. Blacklight reads asleep with
native idle sleep, no pin error, and no game in error. Launcher SLEEP argv is
unchanged. DIT rollback: `backups/pre-r58-*/20-docker-group.conf`. This closes
the deployment and supersedes the local-only status above; evidence is DIT's
confirmation, without another server test from this lane.
DIT also reports AI-only bot chat live via `dit-blr-chat-bridge`; actual in-game
chat has not been playtested by this lane.

## Completed local follow-up: release-helper failure propagation (2026-11-03)

The runtime backlog is complete. Check `optionb/make_release.sh`: its POSIX shell
pipeline sends unittest output through `tail`, potentially hiding a failed test
exit status. Reproduce with a tiny failing fixture, fix propagation, and verify
both success and failure with isolated temporary release directories. This work
is local tooling only; no live scripts, stackd pins, saves or server cycles change.

Result: reproduced the bug with a failing unittest fixture (helper incorrectly
returned 0), then removed the output pipeline so `set -e` sees unittest's actual
exit status. Failed tests now stop before release hashes are printed and retain
full diagnostics. Both isolated helper regressions pass (failure rejects release,
success prints hashes), and `sh -n` passes. No full game suite or server build was
run. The fix is committed locally; the historical copy on Banshee was not invoked
or changed. No further runtime task is pending from this follow-up.

## Release packaging smoke and current handoff (2026-11-03)

Ran the fixed helper against the actual local source in a fresh temporary output
with a synthetic 64-hex container ID. It exited 0; all 35 packaged fixture tests
passed in 0.088 seconds. Verified the ID was the only stop-script change, accepted
exit code remained 0, the other three copied files were byte-identical, and all
three printed SHA256 hashes matched their outputs. Source files were unchanged;
temporary output was removed. No deployment, Docker access or real saves.

This completes the release-helper follow-up, including fixture failure/success
regressions and real-source packaging. There is no next unblocked implementation
item in the recorded handoff. Do not repeat the completed monitor cutover, CLI
re-pin or packaging checks in response to stale stall-watch checkpoints.
The external ZCure restore-test TODO remains with banshee-realm-client; AI-only
bot chat awaits ordinary player use, not an artificial wake from this lane.
Next action: handle a concrete new owner task, reported bug, or external result.

## Local release-path audit (2026-11-03)

Audited helper paths, TODO/FIXME references and stale documentation statuses.
Top gap: `mkdir -p` reused an existing output and `cp` overwrote its files,
including through a directory symlink into a retained release. Both isolated
regressions reproduced the overwrite before the fix. The helper now claims a
fresh directory with `mkdir` before copying; existing directories and symlinks
are refused without modifying their contents. Missing parent directories can
still be created. Use a new output path for each attempt; failed staging remains
available for inspection and is never silently reused.

All four focused helper tests pass (new output succeeds, failed tests abort,
existing release is preserved, symlink target is preserved); shell syntax and
diff whitespace checks pass. Earlier pending-status prose was clarified as
historical and linked to its completed follow-up. No Banshee files, runtime
scripts or saves changed. The only recorded external TODO remains ZCure restore
verification with the backup owner; it is not part of this local audit.

## Local recreate-output guard (2026-11-03)

Extended the release/rollback preservation check to `optionb/recreate.py --body`.
Its former O_TRUNC open could overwrite an existing payload, follow a symlink,
or destroy the saved input inspect when given the same output path. Synthetic
subprocess regressions reproduced all four cases (regular file, symlink,
dangling symlink and input-file reuse). The output now uses O_CREAT|O_EXCL and
reports a refusal for occupied paths; no check-then-open overwrite window.

Focused validation: two tests, covering those four refusal cases plus successful
new-file creation, pass. They verify preserved bytes/links, complete generated
JSON, 0600 permissions and no environment-value output. Only synthetic fixtures
were used. No Docker invocation, Banshee change, real save access or push occurred.
The local recreate helper and release helper now both refuse existing outputs.

## Release/rollback helper coverage inventory (2026-11-03)

Repository-wide helper inventory confirms the refuse-overwrite staging work is
complete. There is no additional rollback-output script in this repository.

| Entry point | Writes and protection | Existing verification |
| --- | --- | --- |
| `optionb/make_release.sh` | Creates a fresh output directory; existing output directories/symlinks are refused before copying. Failed fixture tests abort before hashes. | `test_make_release.py`: four focused tests; passed in a34ef16. |
| `optionb/recreate.py --body` | Creates a new 0600 payload with O_EXCL; existing files, symlinks and input-file reuse are refused. | `test_recreate.py`: refusal subcases plus complete/private output; passed in 48e9787. |
| `optionb/recreate.py` default / `--diff` | Reads saved inspect data and prints redacted output; no file writes. | Source inspected during this inventory. |
| `optionb/blr-init.sh` and `optionb/fake/artemis` | Process wrapper and test stand-in; no release or rollback file outputs. | Source inspected during this inventory; neither executed. |

Runtime functions in `blr_stop.py` write lock files, atomic marker/state updates,
ratings backups and receipts. They are not release-staging helpers and were not
changed by this audit. In particular, this inventory does not claim immutable or
collision-proof runtime backup/receipt names. Applying a blanket exclusive-create
rule to runtime marker updates would break their intended state transitions.

This final inventory changed documentation only. No tests were rerun because the
previous focused checks already cover the unchanged implementations. No server,
save, runtime pin, or remote file was changed; no push was made. Further runtime
backup/receipt work would be a distinct change with its own focused validation,
not unfinished release-helper coverage.
