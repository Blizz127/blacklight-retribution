#!/bin/sh
# Usage: make_release.sh <new full 64-hex container id> <out dir>
# Builds the option B release of the lead's tools: new pinned CONTAINER, ACCEPTED_EXIT_CODE 0. Runs the tests, prints shas.
set -eu
id=$1; out=$2; src=$(dirname "$0")/..
echo "$id" | grep -Eq '^[0-9a-f]{64}$' || { echo "bad id" >&2; exit 2; }
mkdir -p "$out"; cp "$src"/blr_stop.py "$src"/blr_sleep_now.py "$src"/blr_idle_monitor.py "$src"/test_blr_sleepwake.py "$out"/
sed -i -e "s/^CONTAINER = '[0-9a-f]\{64\}'/CONTAINER = '$id'/" \
       -e 's/^ACCEPTED_EXIT_CODE = 2 .*/ACCEPTED_EXIT_CODE = 0       # option B: blr-init.sh wrapper exits 0 on a requested stop/' \
       -e 's/ExitCode exactly 2,/ExitCode exactly ACCEPTED_EXIT_CODE (0, option B),/' "$out"/blr_stop.py
grep -q "^CONTAINER = '$id'" "$out"/blr_stop.py && grep -q '^ACCEPTED_EXIT_CODE = 0 ' "$out"/blr_stop.py
# Preserve unittest's status: a POSIX pipeline would report tail's success instead.
(cd "$out" && python3 -B -m unittest test_blr_sleepwake)
(cd "$out" && sha256sum blr_stop.py blr_sleep_now.py blr_idle_monitor.py)
