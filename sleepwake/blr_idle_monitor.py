#!/usr/bin/env python3
"""Blacklight: Retribution idle monitor, one tick per timer run (native side of NATIVE-SLEEP-WAKE-CONTRACT.md).

Keeps a zero-human streak. A tick counts only on positive proof (blr_stop.observe + gate): pinned container
running, botmatch/status.json fresh and written by this incarnation, humans == 0 (bots never count), ratings parse.
Anything else (unknown, humans, HOLD, deploy lock, a launcher demand touch within 600 s, new incarnation, a tick
gap) resets the streak.

After WINDOW_S of unbroken proof:
  * without the ENABLE file: logs `would_sleep` and changes nothing (DRY-RUN, the default);
  * with ENABLE: runs blr_stop.stop_sleep(by='blr-idle-monitor'), which re-gates and proves everything itself.
"""
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blr_stop as b  # noqa: E402

STATE = b.SW / 'state/monitor.json'
ENABLE = b.SW / 'ENABLE'
WINDOW_S = 1200
MAX_TICK_GAP_S = 180


def log(event, **fields):
    print(json.dumps(dict(ts=round(time.time(), 3), game='blacklight', event=event, **fields), sort_keys=True),
          flush=True)


def load_state(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def evaluate(now, prev, obs_or_reason):
    """Pure streak step -> (new_state, decision, detail); decision in reset|counting|window_met."""
    if isinstance(obs_or_reason, str):
        return {}, 'reset', obs_or_reason
    inc = list(b.incarnation(obs_or_reason))
    same = (prev.get('incarnation') == inc and isinstance(prev.get('zero_since'), (int, float))
            and isinstance(prev.get('last_tick'), (int, float)) and 0 <= now - prev['last_tick'] <= MAX_TICK_GAP_S)
    zero_since = prev['zero_since'] if same else now
    elapsed = now - zero_since
    state = dict(incarnation=inc, zero_since=zero_since, last_tick=now, bots=obs_or_reason.get('bots'))
    return state, ('window_met' if elapsed >= WINDOW_S else 'counting'), 'zero_humans_for_%ds' % int(elapsed)


def tick(now=None, runner=b.run, state_path=None, enable=None, paths=None, stop=None, receipts=None):
    now = time.time() if now is None else now
    state_path, enable = state_path or STATE, enable or ENABLE
    p = dict(status=b.STATUS, ratings=b.RATINGS, hold=b.HOLD, deploy_lock=b.DEPLOY_LOCK, demand=b.DEMAND)
    p.update(paths or {})
    try:
        b.check_locks(p['hold'], p['deploy_lock'], p['demand'], now)
        obs = b.observe(now, runner, p['status'], p['ratings'])
        b.gate(obs)
    except b.Refuse as e:
        obs = str(e) + (':%s' % e.data['humans'] if 'humans' in e.data else '')
    state, decision, detail = evaluate(now, load_state(state_path), obs)
    b.write_atomic(state_path, state)
    if decision != 'window_met':
        log(decision, detail=detail)
        return decision
    if not enable.exists():
        log('would_sleep', detail=detail, dry_run=True)
        return 'would_sleep'
    stop = stop or (lambda: b.stop_sleep('blr-idle-monitor', runner=runner, paths=paths))
    try:
        marker = stop()
        result, code = dict(ok=True, by='blr-idle-monitor', marker=marker), 'asleep'
    except b.Refuse as e:
        result, code = dict(ok=False, by='blr-idle-monitor', reason=str(e), **e.data), 'stop_refused'
    try:
        b.write_receipt(result, receipts or b.RECEIPTS)       # same receipt trail as a blr_stop CLI run
    except OSError:
        pass
    b.write_atomic(state_path, {})
    log(code, **{k: v for k, v in result.items() if k != 'marker'})
    return code


def main():
    if socket.gethostname().lower() not in ('banshee', 'bansheerealm'):
        log('refused', detail='wrong_host')
        return 2
    return 1 if tick() == 'stop_refused' else 0


if __name__ == '__main__':
    sys.exit(main())
