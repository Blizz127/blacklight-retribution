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
