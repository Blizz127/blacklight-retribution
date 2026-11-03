import fcntl
import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import blr_idle_monitor as mon
import blr_sleep_now as now_btn
import blr_stop as b

STARTED = '2026-10-30T20:54:39.617672152Z'


def docker_time(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(int(t))) + ('%.9f' % (t - int(t)))[1:] + 'Z'


def row(running=True, pid=909375, exit_code=0, cid=b.CONTAINER, finished='0001-01-01T00:00:00Z', restarting=False):
    return [{'Id': cid, 'Name': '/blrevive',
             'State': {'Status': 'running' if running else 'exited', 'Running': running, 'Paused': False,
                       'Restarting': restarting, 'Dead': False, 'Pid': pid if running else 0,
                       'ExitCode': exit_code, 'StartedAt': STARTED, 'FinishedAt': finished}}]


class Docker:
    """Fake docker: inspect returns self.rows; stop moves to an exited row (exit code / timing configurable)."""

    def __init__(self, rows=None, stop_rc=0, stop_exit=None, finish_delay=1.0):
        self.rows = rows or row()
        self.stop_rc, self.finish_delay, self.calls = stop_rc, finish_delay, []
        self.stop_exit = b.ACCEPTED_EXIT_CODE if stop_exit is None else stop_exit
        self.on_stop = None

    def __call__(self, argv, timeout=45):
        self.calls.append(argv)
        if argv[:2] == ['docker', 'inspect']:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.rows), '')
        if argv[:2] == ['docker', 'stop']:
            if self.on_stop:
                self.on_stop()
            if self.stop_rc == 0:
                self.rows = row(running=False, exit_code=self.stop_exit,
                                finished=docker_time(time.time() + self.finish_delay))
            return subprocess.CompletedProcess(argv, self.stop_rc, '', '')
        raise AssertionError(argv)

    def stopped(self):
        return any(c[:2] == ['docker', 'stop'] for c in self.calls)


class Env(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.d = d
        (d / 'bm').mkdir()
        self.p = dict(status=d / 'bm/status.json', ratings=d / 'bm/skill_ratings.json', marker=d / 'sw/state/asleep.json',
                      hold=d / 'sw/HOLD', deploy_lock=d / 'sw/run/deploy.lock', stop_lock=d / 'sw/run/stop.lock',
                      wake_lock=d / 'srv/run/migration-wake.lock', backups=d / 'backups', boot_id=lambda: 'boot-1',
                      demand=d / 'sw/state/demand')
        self.write_status(0)
        self.p['ratings'].write_text(json.dumps({'version': 1, 'players': {}}))

    def write_status(self, humans, age=2.0, mtime=None):
        self.p['status'].write_text(json.dumps({'humans': humans, 'bots': 15}))
        t = time.time() - age if mtime is None else mtime
        os.utime(self.p['status'], (t, t))

    def stop(self, docker, by='blr-idle-monitor'):
        return b.stop_sleep(by, runner=docker, sleep=lambda s: None, paths=self.p)

    def marker(self):
        return json.loads(self.p['marker'].read_text())


class StopSleep(Env):
    def test_happy_path_confirms_marker(self):
        m = self.stop(Docker())
        disk = self.marker()
        self.assertEqual(m, disk)
        self.assertEqual((disk['schema'], disk['state'], disk['by'], disk['container'], disk['container_id'],
                          disk['boot_id']), (1, 'asleep', 'blr-idle-monitor', 'blrevive', b.CONTAINER, 'boot-1'))
        c = disk['confirmed']
        self.assertEqual((c['stop_rc'], c['exit_code'], c['humans_last_check'], c['botmatch_ok'],
                          c['container_id_unchanged']), (0, b.ACCEPTED_EXIT_CODE, 0, True, True))
        self.assertTrue(disk['requested_at'] <= c['finished_at'] <= disk['requested_at'] + 60)
        self.assertEqual(self.p['marker'].stat().st_mode & 0o777, 0o644)
        self.assertEqual(c['backup_sha256'], c['ratings_sha256'])

    def test_marker_unconfirmed_on_disk_at_stop_time(self):
        docker = Docker()
        seen = {}
        docker.on_stop = lambda: seen.update(m=self.marker())
        self.stop(docker)
        self.assertNotIn('confirmed', seen['m'])

    def assert_refused(self, docker, reason, marker_written):
        with self.assertRaises(b.Refuse) as cm:
            self.stop(docker)
        self.assertEqual(str(cm.exception), reason)
        if marker_written:
            self.assertNotIn('confirmed', self.marker())
        else:
            self.assertFalse(self.p['marker'].exists())
            self.assertFalse(docker.stopped())

    def test_exit_137_is_not_confirmed(self):
        self.assert_refused(Docker(stop_exit=137), 'unexpected_exit_code', True)

    def test_other_option_exit_is_not_confirmed(self):
        # option A accepts only 2, option B only 0: the other one is never confirmed
        self.assert_refused(Docker(stop_exit=2 - b.ACCEPTED_EXIT_CODE), 'unexpected_exit_code', True)

    def test_confirmed_marker_with_wrong_exit_code_is_not_asleep(self):
        docker = Docker()
        self.stop(docker)
        docker.rows[0]['State']['ExitCode'] = 2 - b.ACCEPTED_EXIT_CODE
        self.assertEqual(b.marker_state(docker, self.p['marker']), 'exit_code_mismatch')

    def test_docker_stop_failure(self):
        self.assert_refused(Docker(stop_rc=1), 'docker_stop_failed', True)

    def test_finished_late_is_not_confirmed(self):
        self.assert_refused(Docker(finish_delay=120), 'finished_at_out_of_window', True)

    def test_ratings_changed_by_stop_is_not_confirmed(self):
        docker = Docker()
        docker.on_stop = lambda: self.p['ratings'].write_text('{"version": 1, "players": {"x": 1}}')
        self.assert_refused(docker, 'ratings_changed_or_unparsable_after_stop', True)

    def test_humans_refuse_before_marker(self):
        self.write_status(1)
        self.assert_refused(Docker(), 'humans_present', False)

    def test_stale_status_refuses(self):
        self.write_status(0, age=120)
        self.assert_refused(Docker(), 'status_stale', False)

    def test_status_from_previous_incarnation_refuses(self):
        self.write_status(0)
        later = row()
        later[0]['State']['StartedAt'] = docker_time(time.time() + 1)
        self.assert_refused(Docker(rows=later), 'status_from_previous_incarnation', False)

    def test_unparsable_ratings_refuse(self):
        self.p['ratings'].write_text('{torn')
        self.assert_refused(Docker(), 'ratings_unparsable', False)

    def test_hold_and_deploy_lock_refuse(self):
        self.p['hold'].parent.mkdir(parents=True, exist_ok=True)
        self.p['hold'].write_text('x')
        self.assert_refused(Docker(), 'hold_present', False)
        self.p['hold'].unlink()
        self.p['deploy_lock'].parent.mkdir(parents=True, exist_ok=True)
        self.p['deploy_lock'].write_text('x')
        self.assert_refused(Docker(), 'deploy_lock_present', False)

    def test_wake_in_progress_and_busy_refuse(self):
        for key, reason in (('wake_lock', 'wake_in_progress'), ('stop_lock', 'another_stop_in_progress')):
            self.p[key].parent.mkdir(parents=True, exist_ok=True)
            with self.p[key].open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                self.assert_refused(Docker(), reason, False)

    def test_foreign_or_restarting_container_refuses(self):
        self.assert_refused(Docker(rows=row(cid='f' * 64)), 'container_identity_changed', False)
        self.assert_refused(Docker(rows=row(restarting=True)), 'container_transition_or_unknown', False)

    def touch_demand(self, age):
        self.p['demand'].parent.mkdir(parents=True, exist_ok=True)
        self.p['demand'].write_text('')
        t = time.time() - age
        os.utime(self.p['demand'], (t, t))

    def test_recent_or_future_demand_refuses_before_marker(self):
        self.touch_demand(30)
        self.assert_refused(Docker(), 'recent_demand', False)
        self.touch_demand(-120)
        self.assert_refused(Docker(), 'recent_demand', False)

    def test_old_demand_is_ignored(self):
        self.touch_demand(b.DEMAND_GRACE_S + 60)
        self.assertIn('confirmed', self.stop(Docker()))

    def test_demand_during_settle_refuses_before_marker(self):
        docker = Docker()
        def sleep(_):
            self.touch_demand(0)
        with self.assertRaises(b.Refuse) as cm:
            b.stop_sleep('blr-stop', runner=docker, sleep=sleep, paths=self.p)
        self.assertEqual(str(cm.exception), 'recent_demand')
        self.assertFalse(self.p['marker'].exists())
        self.assertFalse(docker.stopped())

    def test_invalid_by(self):
        with self.assertRaises(b.Refuse):
            self.stop(Docker(), by='someone')


class MarkerState(Env):
    def test_asleep_only_for_matching_confirmed_marker(self):
        docker = Docker()
        self.stop(docker)
        self.assertEqual(b.marker_state(docker, self.p['marker']), 'asleep')
        # started and stopped again by hand: FinishedAt differs -> never asleep
        docker.rows = row(running=False, exit_code=b.ACCEPTED_EXIT_CODE, finished=docker_time(time.time() + 500))
        self.assertEqual(b.marker_state(docker, self.p['marker']), 'stopped_again_since_marker')

    def test_unconfirmed_and_missing(self):
        docker = Docker(rows=row(running=False, exit_code=b.ACCEPTED_EXIT_CODE, finished=docker_time(time.time())))
        self.assertEqual(b.marker_state(docker, self.p['marker']), 'no_marker')
        b.write_atomic(self.p['marker'], {'schema': 1, 'state': 'asleep', 'by': 'blr-stop', 'container_id': b.CONTAINER})
        self.assertEqual(b.marker_state(docker, self.p['marker']), 'marker_unconfirmed')

    def test_incident_stop_is_not_asleep(self):
        docker = Docker(rows=row(running=False, exit_code=b.ACCEPTED_EXIT_CODE, finished='2026-11-01T05:54:51.060668061Z'))
        self.assertEqual(b.marker_state(docker, self.p['marker']), 'no_marker')


class SleepNow(Env):
    def decide(self, docker):
        return now_btn.decide(docker, stop_lock=self.p['stop_lock'], wake_lock=self.p['wake_lock'], hold=self.p['hold'],
                              deploy_lock=self.p['deploy_lock'], status=self.p['status'], ratings=self.p['ratings'],
                              marker=self.p['marker'], demand=self.p['demand'])

    def test_codes(self):
        self.assertEqual(self.decide(Docker()), (0, {'state': 'stopping', 'eta_s': now_btn.ETA_S}))
        self.write_status(3)
        self.assertEqual(self.decide(Docker()), (10, {'humans': 3}))
        self.write_status(0, age=300)
        self.assertEqual(self.decide(Docker())[0], 1)
        self.write_status(0)
        self.p['deploy_lock'].parent.mkdir(parents=True, exist_ok=True)
        self.p['deploy_lock'].write_text('x')
        self.assertEqual(self.decide(Docker()), (11, {'code': 'deploy_lock_present'}))
        self.p['deploy_lock'].unlink()
        self.p['hold'].write_text('x')
        self.assertEqual(self.decide(Docker()), (13, {'code': 'hold_present'}))
        self.p['hold'].unlink()
        self.p['demand'].parent.mkdir(parents=True, exist_ok=True)
        self.p['demand'].write_text('')
        self.assertEqual(self.decide(Docker()), (13, {'code': 'recent_demand'}))
        self.p['demand'].unlink()
        with self.p['stop_lock'].open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.decide(Docker()), (12, {'code': 'another_stop_in_progress'}))

    def test_already_asleep_and_unknown_stop(self):
        docker = Docker()
        self.stop(docker)
        self.assertEqual(self.decide(docker), (2, {'state': 'asleep'}))
        self.p['hold'].write_text('x')
        self.assertEqual(self.decide(docker), (1, {'error': 'stopped_under_hold'}))
        self.p['hold'].unlink()
        incident = Docker(rows=row(running=False, exit_code=b.ACCEPTED_EXIT_CODE, finished='2026-11-01T05:54:51.060668061Z'))
        self.p['marker'].unlink()
        self.assertEqual(self.decide(incident), (1, {'error': 'stopped_no_marker'}))

    def test_decide_never_stops(self):
        docker = Docker()
        self.decide(docker)
        self.assertFalse(docker.stopped())


class SleepNowCLI(unittest.TestCase):
    def test_help_and_invalid_arguments_have_no_side_effects(self):
        for argv, code in [(['--help'], 0), (['-h'], 0), (['--invalid'], 2), (['sleep'], 2)]:
            with self.subTest(argv=argv), mock.patch.object(now_btn.socket, 'gethostname', return_value='banshee'), \
                    mock.patch.object(now_btn, 'decide') as decide, \
                    mock.patch.object(now_btn.subprocess, 'run') as run, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    now_btn.main(argv)
                self.assertEqual(raised.exception.code, code)
                decide.assert_not_called()
                run.assert_not_called()

    def test_no_arguments_still_dispatches_the_guarded_stop(self):
        with mock.patch.object(now_btn.socket, 'gethostname', return_value='banshee'), \
                mock.patch.object(now_btn, 'decide', return_value=(0, {'state': 'stopping', 'eta_s': 15})) as decide, \
                mock.patch.object(now_btn.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                now_btn.main([])
            self.assertEqual(raised.exception.code, 0)
            decide.assert_called_once_with()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][-5:], ['--mode', 'sleep', '--execute', '--by', 'blr-stop'])
            self.assertEqual(json.loads(output.getvalue())['state'], 'stopping')


class Monitor(Env):
    def setUp(self):
        super().setUp()
        self.state = self.d / 'sw/state/monitor.json'
        self.enable = self.d / 'sw/ENABLE'
        self.stops = []

    def tick(self, t, docker=None):
        self.write_status(0, mtime=t - 2)
        return mon.tick(now=t, runner=docker or Docker(), state_path=self.state, enable=self.enable, paths=self.p,
                        stop=lambda: self.stops.append(t) or {'confirmed': {}}, receipts=self.d / 'receipts')

    def window(self, n, start=None):
        start = time.time() if start is None else start
        return [self.tick(start + 60 * i) for i in range(n)][-1]

    def test_dry_run_full_window_never_stops(self):
        self.assertEqual(self.window(21), 'would_sleep')
        self.assertEqual(self.stops, [])

    def test_short_window_counts(self):
        self.assertEqual(self.window(20), 'counting')

    def test_enabled_full_window_stops_once_and_resets(self):
        self.enable.parent.mkdir(parents=True, exist_ok=True)
        self.enable.write_text('')
        self.assertEqual(self.window(21), 'asleep')
        self.assertEqual(len(self.stops), 1)
        self.assertEqual(json.loads(self.state.read_text()), {})
        receipts = list((self.d / 'receipts').iterdir())
        self.assertEqual(len(receipts), 1)
        self.assertTrue(json.loads(receipts[0].read_text())['ok'])

    def test_human_resets(self):
        t0 = time.time()
        self.window(10, t0)
        self.write_status(1, mtime=t0 + 598)
        r = mon.tick(now=t0 + 600, runner=Docker(), state_path=self.state, enable=self.enable, paths=self.p)
        self.assertEqual(r, 'reset')
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_gap_and_new_incarnation_restart_streak(self):
        t0 = time.time()
        self.window(5, t0)
        t = t0 + 4 * 60 + mon.MAX_TICK_GAP_S + 1
        self.tick(t)
        self.assertEqual(json.loads(self.state.read_text())['zero_since'], t)
        self.tick(t + 60, Docker(rows=row(pid=4242)))
        self.assertEqual(json.loads(self.state.read_text())['zero_since'], t + 60)

    def test_hold_resets(self):
        self.p['hold'].parent.mkdir(parents=True, exist_ok=True)
        self.p['hold'].write_text('x')
        self.assertEqual(self.tick(time.time()), 'reset')

    def test_demand_resets_streak(self):
        t0 = time.time()
        self.window(10, t0)
        self.p['demand'].parent.mkdir(parents=True, exist_ok=True)
        self.p['demand'].write_text('')
        os.utime(self.p['demand'], (t0 + 590, t0 + 590))
        self.assertEqual(self.tick(t0 + 600), 'reset')
        self.assertEqual(self.tick(t0 + 660), 'reset')                       # still inside the grace
        self.assertEqual(self.tick(t0 + 590 + b.DEMAND_GRACE_S + 1), 'counting')

    def test_stopped_container_resets(self):
        self.assertEqual(self.tick(time.time(), Docker(rows=row(running=False, exit_code=2))), 'reset')


if __name__ == '__main__':
    unittest.main()
