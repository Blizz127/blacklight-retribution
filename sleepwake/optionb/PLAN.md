# Option B: blrevive exits 0 on a requested stop (completed 2026-11-02)

This is the completed cutover plan, retained for reference. The owner-approved cutover and watched sleep/wake cycle finished on 2026-11-02; see [the live record](../README.md). Do not replay these steps as pending work.

## Wrapper
`blr-init.sh` (dash, which the image has) runs as PID 1, starts `artemis start` as a child, and forwards SIGTERM/SIGINT to it.
- It exits 0 only when a stop was requested AND artemis ended by that signal (143/130) or with 2.
- Any other end keeps its code: an artemis Fatal gives 1, and `restart: unless-stopped` still applies.

Trap installed before artemis spawns. Tested as PID 1 in docker on the devbox (debian:stable-slim, with a fake artemis):
| Case | Result |
| --- | --- |
| dies by TERM | `docker stop` → exit 0 |
| exits 2 on TERM | exit 0 |
| self-exit 1 | exit 1 |

## Recreate facts (from `docker inspect`, 2026-11-02)
The container can't be changed in place, so B needs a new container, and that means a new ID.
- **Image:** pin by ID `sha256:97f9be01eb41…`. NOT `:latest`, which could pull a newer upstream.
- **Assets volume:** anonymous volume `934795ce45cf…` → `/mnt/blacklightre/blre`. Re-attach it BY NAME, or the assets are lost.
- **Binds:** (see the agreed section for the exact wrapper mount)
  - `config` rw;
  - `blacklight-ticket.key` → `…/banshee_auth/key` ro;
  - `game` ro and `modules` ro;
  - NEW `~/servers/blrevive-sleepwake/optionb/blr-init.sh` → `/opt/blr-init/blr-init.sh` ro.
- **Runtime settings:**
  - user `blrevive`;
  - network `blrevive_default`;
  - ports 7777 tcp+udp;
  - memory 2 GiB, swap total 2.5 GiB;
  - json-file log 50m×3;
  - restart unless-stopped.
- **Network:** alias `blrevive` (`--network-alias blrevive`). It is the only container on `blrevive_default` and has no depends_on; IP is dynamic.
- **Everything else is default:** shm 64M; no caps, secopts, ulimits, tmpfs, dns or sysctls; ipc/cgroupns private; no tty; no healthcheck. Hostname is the short ID and will change.
- **Env:** 28 keys, copied from inspect into a 0600 `--env-file`. Never printed.
- **Entrypoint and name:** `/bin/sh /opt/blr-init/blr-init.sh`, cmd `start`. Name `blrevive`. The compose labels are dropped, which is fine because `compose up` is already banned.

## Window (needs the owner's yes: a new container ID is a deployment)
1. The server is asleep with a confirmed marker. Touch HOLD so stackd shows maintenance and won't wake it, and touch deploy.lock.
2. Back up `botmatch` and `banshee_auth`. Save the old full inspect.
3. `docker rename blrevive blrevive-pre-optionb`. Do NOT remove it; it is the rollback.
4. `docker create` the new `blrevive` with the facts above, then `docker update --restart=no blrevive-pre-optionb`.
   Verify with a full `docker inspect` diff, old vs new. Only Id, Created, Name, Hostname, Entrypoint, Cmd, the new ro mount and the compose labels may differ.
   Then `docker start`. Ready = running + UDP 7777 + a fresh status.json.
5. Watched stop, gated the same way as `blr_stop`: status.json is fresh, from the new incarnation, and `humans == 0` (a player can still join on 7777). Expect ExitCode 0. Start again and confirm ready.
6. DIT re-pins the new ID and mounts in `blacklight_start.py` and drops the exit-2 exception. Lead deploys `blr_stop` with `ACCEPTED_EXIT_CODE=0` and the new pin, and DIT re-pins its sha.
7. Remove HOLD and deploy.lock. One watched sleep → wake → ready through stackd.

**Never `docker rm -v` the old container:** the assets volume is anonymous.

**Rollback before step 6:** stop the new container, rename it away, rename the old one back, and `docker start` the old pinned ID. DIT's pin is untouched until step 6.

## Agreed with DIT (2026-11-02)
- **Mount (exact-equality check in DIT's validation):** `/opt/blr-init/blr-init.sh` ← bind `/home/blizz/servers/blrevive-sleepwake/optionb/blr-init.sh`, ro.
  - The host file is 0644, copied to Banshee at the start of the window.
  - Never replace the file by rename while the container exists, because a bind pins the inode. Change it only in a recreate window.
- **Entrypoint/Cmd (DIT pins both):**
  - Entrypoint `["/bin/sh", "/opt/blr-init/blr-init.sh"]`
  - Cmd `["start"]`
- **Exit codes:** `asleep()` changes ExitCode 2 → 0, and `confirmed.exit_code` changes to 0. `blr_stop` then gets `ACCEPTED_EXIT_CODE = 0` and DIT re-pins its sha.
- **artemis exiting 0 on its own:** this also ends 0, but it is safe. `blr_stop` writes the unconfirmed marker before `docker stop` and confirms only after it observes its own stop, with FinishedAt within [requested_at, +60 s].
- **One window.** DIT pre-stages a stackd release with everything except the ID, and needs 15 min notice. At step 6 the lead sends the new ID. DIT pins it, tests, backs up, and restarts stackd (~5 min).
- **Between create and re-pin:** stackd shows `error/native_identity_changed` and won't wake. That is expected under HOLD.
- **Rollback:** the lead restores `blrevive-pre-optionb`; DIT restores the previous stackd release.

## Lead release at step 6 (pre-staged)
`optionb/make_release.sh <new id> <new dir>` requires an absent output directory (existing directories and symlinks are refused), then writes the new pinned `CONTAINER` and `ACCEPTED_EXIT_CODE = 0` into `blr_stop.py`, runs the 33 tests, and prints the shas.
- Only `blr_stop.py`'s sha changes.
- `blr_sleep_now.py` (664b72c4) and `blr_idle_monitor.py` (838309ec) stay byte-identical, because they import blr_stop.
- The tests are exit-code agnostic and pass under both A (2) and B (0).
