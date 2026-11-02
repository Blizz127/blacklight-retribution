#!/bin/sh
# Option B init for blrevive: PID 1 that runs artemis as a child and turns a requested stop into exit 0.
# artemis 1.4.0 has no signal handler. As PID 1 it exits 2 on SIGTERM; as a child it dies by SIGTERM (143).
# Exit 0 only when a stop was requested (SIGTERM/SIGINT) AND artemis ended by that signal (143/130) or with 2.
# Any other end (crash, artemis Fatal exit 1) keeps its non-zero code, so restart=unless-stopped still restarts.
stop=0
child=
# Trap first: PID 1 drops a signal that has no handler, so a stop arriving before the trap would hang until SIGKILL.
trap 'stop=1; [ -n "$child" ] && kill -TERM "$child" 2>/dev/null' TERM INT
artemis "$@" &
child=$!
[ "$stop" = 1 ] && kill -TERM "$child" 2>/dev/null   # stop arrived before artemis started
while :; do
  wait "$child"; rc=$?
  kill -0 "$child" 2>/dev/null || break   # wait was interrupted by the trap; the child is still alive
done
if [ "$stop" = 1 ]; then
  case "$rc" in 2|130|143) echo "blr-init: stop requested, artemis rc=$rc -> exit 0" >&2; exit 0 ;; esac
fi
echo "blr-init: artemis rc=$rc stop=$stop" >&2
exit "$rc"
