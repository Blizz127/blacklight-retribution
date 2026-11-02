#!/usr/bin/env python3
"""Blacklight: Retribution native sleep stop (NATIVE-SLEEP-WAKE-CONTRACT.md, BLR exit-2 exception "option A").

`blr_stop.py --mode sleep --execute --by <blr-stop|blr-idle-monitor>`

Sequence (any failure stops right there, leaves the marker unconfirmed, i.e. an error that is never woken):
  1. take stop.lock (busy -> refuse) and DIT's migration-wake.lock (a wake in progress -> refuse)
  2. refuse on HOLD or the deploy lock
  3. observe + gate: pinned container running, botmatch/status.json fresh and from this incarnation, humans == 0,
     status.json and skill_ratings.json parse
  4. settle SETTLE_S, observe + gate again: same incarnation, still zero, skill_ratings digest unchanged
  5. private backup of skill_ratings.json, read back
  6. write the marker atomically WITHOUT confirmed
  7. docker stop -t 30 <pinned id>   (artemis 1.4.0 as PID 1 has no SIGTERM handler: Go exits 2, never kill -9)
  8. prove: docker stop rc 0, same container id, exited, ExitCode exactly ACCEPTED_EXIT_CODE (0, option B), FinishedAt in [requested_at, +60 s]
  9. post-check: status.json and skill_ratings.json parse, skill_ratings digest unchanged
 10. rewrite the marker with confirmed{}

Reads only docker inspect and files under ~/servers/blrevive (no /proc of a native process). Stdlib only.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CONTAINER = '0df4b910428c60bd951eeeb49746c6db5b75ff0f02737e20a1879fb516e9e5c1'
SERVER = Path(os.environ.get('BLR_SERVER_DIR', str(HOME / 'servers/blrevive')))
SW = Path(os.environ.get('BLR_SLEEPWAKE_DIR', str(HOME / 'servers/blrevive-sleepwake')))
BOTMATCH = SERVER / 'config/BLRevive/botmatch'
STATUS = BOTMATCH / 'status.json'
RATINGS = BOTMATCH / 'skill_ratings.json'
MARKER = SW / 'state/asleep.json'
HOLD = SW / 'HOLD'
DEPLOY_LOCK = SW / 'run/deploy.lock'
STOP_LOCK = SW / 'run/stop.lock'
DEMAND = SW / 'state/demand'                             # stackd touches it on a launcher PLAY/wake/status read
WAKE_LOCK = SERVER / 'run/migration-wake.lock'          # DIT's blacklight_start.py flock
RECEIPTS = HOME / '.local/state/blr-lead/stops'
BACKUPS = HOME / '.local/state/blr-lead/backups'
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')

FRESH_S = 60
DEMAND_GRACE_S = 600         # a player may be logging in (10-60 s) before status.json counts them
SETTLE_S = 5
STOP_TIMEOUT_S = 30
ACCEPTED_EXIT_CODE = 0       # option B: blr-init.sh wrapper exits 0 on a requested stop
FINISH_WINDOW_S = 60
BY_VALUES = ('blr-stop', 'blr-idle-monitor')


class Refuse(Exception):
    def __init__(self, reason, **data):
        super().__init__(reason)
        self.data = data


def log(event, **fields):
    print(json.dumps(dict(ts=round(time.time(), 3), game='blacklight', event=event, **fields), sort_keys=True),
          file=sys.stderr, flush=True)


def run(argv, timeout=45):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def parse_docker_time(text):
    m = re.match(r'(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?Z$', text or '')
    if not m:
        return None
    import calendar
    whole = calendar.timegm(tuple(int(x) for x in m.groups()[:6]) + (0, 0, 0))
    if whole <= 0:
        return None
    return whole + (float('0.' + m.group(7)) if m.group(7) else 0.0)


def boot_id():
    return BOOT_ID.read_text().strip()


def inspect(runner=run):
    """Pinned container facts, or raise Refuse('container_state_unknown')."""
    try:
        res = runner(['docker', 'inspect', CONTAINER], timeout=15)
        rows = json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = None
    if not isinstance(rows, list) or len(rows) != 1:
        raise Refuse('container_state_unknown')
    row = rows[0]
    st = row.get('State') or {}
    if row.get('Id') != CONTAINER or row.get('Name') != '/blrevive':
        raise Refuse('container_identity_changed')
    if any(st.get(k) is not False for k in ('Paused', 'Restarting', 'Dead')) or type(st.get('Pid')) is not int:
        raise Refuse('container_transition_or_unknown')
    running = st.get('Status') == 'running' and st.get('Running') is True and st['Pid'] > 1
    exited = st.get('Status') == 'exited' and st.get('Running') is False and st['Pid'] == 0
    if not (running or exited):
        raise Refuse('container_state_unsupported')
    return dict(id=row['Id'], running=running, pid=st['Pid'], started_at=parse_docker_time(st.get('StartedAt')),
                started_raw=st.get('StartedAt'), exit_code=st.get('ExitCode'),
                finished_at=parse_docker_time(st.get('FinishedAt')), finished_raw=st.get('FinishedAt'))


def read_json(path):
    with path.open('rb') as fh:
        data = fh.read()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def observe(now=None, runner=run, status=STATUS, ratings=RATINGS):
    """Facts for the gate. Unknown parts are None (the gate refuses on them)."""
    now = time.time() if now is None else now
    c = inspect(runner)
    obs = dict(container=c, humans=None, bots=None, status_reason=None, ratings_sha=None, ratings_ok=None)
    try:
        mtime = status.stat().st_mtime
        body, _ = read_json(status)
        if not isinstance(body, dict):
            raise ValueError
    except (OSError, ValueError):
        obs['status_reason'] = 'status_unreadable'
        return obs
    if now - mtime > FRESH_S:
        obs['status_reason'] = 'status_stale'
    elif c['started_at'] is None or mtime < c['started_at']:
        obs['status_reason'] = 'status_from_previous_incarnation'
    elif type(body.get('humans')) is not int or body['humans'] < 0:
        obs['status_reason'] = 'humans_unknown'
    else:
        obs['humans'], obs['bots'] = body['humans'], body.get('bots')
        obs['status_reason'] = 'ok'
    try:
        _, obs['ratings_sha'] = read_json(ratings)
        obs['ratings_ok'] = True
    except FileNotFoundError:
        obs['ratings_ok'], obs['ratings_sha'] = True, 'absent'
    except (OSError, ValueError):
        obs['ratings_ok'] = False
    return obs


def check_locks(hold=HOLD, deploy_lock=DEPLOY_LOCK, demand=DEMAND, now=None):
    if hold.exists():
        raise Refuse('hold_present')
    if deploy_lock.exists():
        raise Refuse('deploy_lock_present')
    check_demand(demand, now)


def check_demand(demand=DEMAND, now=None):
    """A launcher touched the demand file recently (or in the future): someone may be logging in."""
    now = time.time() if now is None else now
    try:
        mtime = demand.stat().st_mtime
    except FileNotFoundError:
        return
    except OSError:
        raise Refuse('demand_unreadable')
    if now - mtime <= DEMAND_GRACE_S:
        raise Refuse('recent_demand')


def gate(obs):
    if not obs['container']['running']:
        raise Refuse('container_not_running')
    if obs['status_reason'] != 'ok':
        raise Refuse(obs['status_reason'])
    if obs['humans'] != 0:
        raise Refuse('humans_present', humans=obs['humans'])
    if not obs['ratings_ok']:
        raise Refuse('ratings_unparsable')


def incarnation(obs):
    c = obs['container']
    return (c['id'], c['pid'], c['started_raw'])


@contextlib.contextmanager
def flock_nb(path, reason):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('a') as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refuse(reason)
        yield


def write_atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.%s.%d.tmp' % (path.name, os.getpid()))
    with open(tmp, 'w') as fh:
        json.dump(data, fh, sort_keys=True)
        fh.write('\n')
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def marker_state(runner=run, marker=MARKER):
    """'asleep' only for a confirmed marker that matches the stopped pinned container exactly; otherwise a reason."""
    c = inspect(runner)
    if c['running']:
        return 'running'
    try:
        m, _ = read_json(marker)
    except FileNotFoundError:
        return 'no_marker'
    except (OSError, ValueError):
        return 'marker_unreadable'
    conf = m.get('confirmed') if isinstance(m, dict) else None
    if not isinstance(conf, dict):
        return 'marker_unconfirmed'
    if (m.get('schema'), m.get('state'), m.get('container_id')) != (1, 'asleep', c['id']) or m.get('by') not in BY_VALUES:
        return 'marker_mismatch'
    if c['exit_code'] != ACCEPTED_EXIT_CODE or conf.get('exit_code') != ACCEPTED_EXIT_CODE:
        return 'exit_code_mismatch'
    if conf.get('finished_at_raw') != c['finished_raw']:
        return 'stopped_again_since_marker'
    return 'asleep'


def backup_ratings(ratings, sha, backups=BACKUPS):
    if sha == 'absent':
        return 'absent'
    dest = backups / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(backups, 0o700)
    target = dest / ratings.name
    data = ratings.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise Refuse('ratings_changed_during_backup')
    target.write_bytes(data)
    os.chmod(target, 0o600)
    if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
        raise Refuse('backup_readback_mismatch')
    return sha


def stop_sleep(by, runner=run, sleep=time.sleep, clock=time.time, paths=None):
    """Full sleep stop. Returns the confirmed marker dict, or raises Refuse (marker left unconfirmed or unwritten)."""
    p = dict(status=STATUS, ratings=RATINGS, marker=MARKER, hold=HOLD, deploy_lock=DEPLOY_LOCK, stop_lock=STOP_LOCK,
             wake_lock=WAKE_LOCK, backups=BACKUPS, boot_id=boot_id, demand=DEMAND)
    p.update(paths or {})
    if by not in BY_VALUES:
        raise Refuse('invalid_by')
    with flock_nb(p['stop_lock'], 'another_stop_in_progress'), flock_nb(p['wake_lock'], 'wake_in_progress'):
        check_locks(p['hold'], p['deploy_lock'], p['demand'], clock())
        first = observe(clock(), runner, p['status'], p['ratings'])
        gate(first)
        sleep(SETTLE_S)
        second = observe(clock(), runner, p['status'], p['ratings'])
        gate(second)
        if incarnation(second) != incarnation(first):
            raise Refuse('incarnation_changed')
        if second['ratings_sha'] != first['ratings_sha']:
            raise Refuse('ratings_changed_during_settle')
        backup_sha = backup_ratings(p['ratings'], second['ratings_sha'], p['backups'])
        check_locks(p['hold'], p['deploy_lock'], p['demand'], clock())
        last = observe(clock(), runner, p['status'], p['ratings'])
        gate(last)
        if incarnation(last) != incarnation(first) or last['ratings_sha'] != first['ratings_sha']:
            raise Refuse('changed_before_marker')

        check_demand(p['demand'], clock())
        requested_at = clock()
        marker = {'schema': 1, 'state': 'asleep', 'by': by, 'container': 'blrevive', 'container_id': CONTAINER,
                  'boot_id': p['boot_id'](), 'requested_at': requested_at}
        write_atomic(p['marker'], marker)
        log('marker_written', requested_at=requested_at, by=by)

        try:
            res = runner(['docker', 'stop', '-t', str(STOP_TIMEOUT_S), CONTAINER], timeout=STOP_TIMEOUT_S + 30)
        except (OSError, subprocess.TimeoutExpired):
            raise Refuse('docker_stop_failed')
        if res.returncode != 0:
            raise Refuse('docker_stop_failed', rc=res.returncode)
        after = inspect(runner)
        if after['running']:
            raise Refuse('still_running')
        if after['exit_code'] != ACCEPTED_EXIT_CODE:
            raise Refuse('unexpected_exit_code', exit_code=after['exit_code'])
        fin = after['finished_at']
        if fin is None or not (requested_at <= fin <= requested_at + FINISH_WINDOW_S):
            raise Refuse('finished_at_out_of_window')
        try:
            body, _ = read_json(p['status'])
            if not isinstance(body, dict):
                raise ValueError
        except (OSError, ValueError):
            raise Refuse('status_unparsable_after_stop')
        post = observe(clock(), runner, p['status'], p['ratings'])     # container now exited; ratings re-read
        if not post['ratings_ok'] or post['ratings_sha'] != first['ratings_sha']:
            raise Refuse('ratings_changed_or_unparsable_after_stop')

        marker['confirmed'] = {
            'stop_rc': 0, 'exit_code': after['exit_code'], 'finished_at': fin, 'finished_at_raw': after['finished_raw'],
            'humans_last_check': last['humans'], 'botmatch_ok': True, 'ratings_sha256': first['ratings_sha'],
            'backup_sha256': backup_sha, 'container_id_unchanged': after['id'] == CONTAINER, 'at': clock()}
        write_atomic(p['marker'], marker)
        log('asleep', exit_code=after['exit_code'], finished_at=fin)
        return marker


def write_receipt(result, receipts=RECEIPTS):
    receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_atomic(receipts / ('%s.json' % time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())), result, mode=0o600)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mode', choices=('sleep',), required=True)
    ap.add_argument('--execute', action='store_true', required=True)
    ap.add_argument('--by', choices=BY_VALUES, required=True)
    args = ap.parse_args(argv)
    if socket.gethostname().lower() not in ('banshee', 'bansheerealm'):
        print(json.dumps(dict(error='wrong_host')))
        return 1
    try:
        marker = stop_sleep(args.by)
        result = dict(ok=True, marker=marker)
        code = 0
    except Refuse as e:
        result = dict(ok=False, reason=str(e), **e.data)
        code = 3
    write_receipt(result)
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
