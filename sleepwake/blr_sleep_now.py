#!/usr/bin/env python3
"""Admin sleep button for Blacklight (stackd POST /v1/games/blacklight/sleep). Fast gates, then detaches
`blr_stop.py --mode sleep --execute --by blr-stop` in its own transient user unit (blr-sleep-now-<ts>) and
returns at once. The detached stop re-runs every gate itself (settle, backup, marker, proof).

Exit codes (DIT contract):
  0  {"state":"stopping","eta_s":N}     gates passed, stop detached
  2  {"state":"asleep"}                 already asleep (confirmed marker matching the stopped pinned container)
  10 {"humans":n}                       humans present
  11 {"code":"deploy_lock_present"}
  12 {"code":"another_stop_in_progress"|"wake_in_progress"}
  13 {"code":"<reason>"}                other refusal (hold_present, recent_demand)
  1  {"error":"<reason>"}               anything else (unknown state never sleeps)
"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blr_stop as b  # noqa: E402

ETA_S = 15


def decide(runner=b.run, stop_lock=None, wake_lock=None, hold=None, deploy_lock=None, status=None, ratings=None,
           marker=None, demand=None):
    stop_lock = stop_lock or b.STOP_LOCK
    wake_lock = wake_lock or b.WAKE_LOCK
    try:
        c = b.inspect(runner)
        if not c['running']:
            # Same as stackd's rule: a stop under HOLD or the deploy lock is never "asleep".
            if (hold or b.HOLD).exists():
                return 1, dict(error='stopped_under_hold')
            if (deploy_lock or b.DEPLOY_LOCK).exists():
                return 1, dict(error='stopped_under_deploy_lock')
            st = b.marker_state(runner, marker or b.MARKER)
            return (2, dict(state='asleep')) if st == 'asleep' else (1, dict(error='stopped_' + st))
        with b.flock_nb(stop_lock, 'another_stop_in_progress'), b.flock_nb(wake_lock, 'wake_in_progress'):
            b.check_locks(hold or b.HOLD, deploy_lock or b.DEPLOY_LOCK, demand or b.DEMAND)
            b.gate(b.observe(None, runner, status or b.STATUS, ratings or b.RATINGS))
    except b.Refuse as e:
        reason = str(e)
        if reason == 'humans_present':
            return 10, dict(humans=e.data.get('humans'))
        if reason == 'deploy_lock_present':
            return 11, dict(code=reason)
        if reason in ('another_stop_in_progress', 'wake_in_progress'):
            return 12, dict(code=reason)
        if reason in ('hold_present', 'recent_demand'):
            return 13, dict(code=reason)
        return 1, dict(error=reason)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
        return 1, dict(error='%s: unknown state' % type(e).__name__)
    return 0, dict(state='stopping', eta_s=ETA_S)


def main():
    if socket.gethostname().lower() not in ('banshee', 'bansheerealm'):
        print(json.dumps(dict(error='wrong_host')))
        raise SystemExit(1)
    code, payload = decide()
    if code == 0:
        unit = 'blr-sleep-now-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        p = subprocess.run(['/usr/bin/systemd-run', '--user', '--quiet', '--collect', '--unit=' + unit,
                            '/usr/bin/python3', '-I', str(Path(__file__).resolve().parent / 'blr_stop.py'),
                            '--mode', 'sleep', '--execute', '--by', 'blr-stop'],
                           capture_output=True, text=True, timeout=15)
        if p.returncode:
            code, payload = 1, dict(error='detach_failed rc=%d' % p.returncode)
        else:
            payload['job_unit'] = unit
    print(json.dumps(payload))
    raise SystemExit(code)


if __name__ == '__main__':
    main()
