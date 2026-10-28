#!/bin/sh
# check-clocks.sh - show CPU/DDR/AHB clocks and run a quick stability test.
# usage: sh check-clocks.sh [rounds]     (default 5 rounds, ~30 s each)
#
# Each round hashes 500 MiB of zeros. The CPU must produce the same, known
# hash every time; a different hash or a kernel oops means it is unstable.

ROUNDS=${1:-5}
EXPECTED=d8b61b2c0025919d5321461045c8226f   # md5 of 500 MiB of zeros
say() { echo "[check] $*"; }

say "Clocks:"
[ -f /sys/kernel/debug/clk/clk_summary ] || mount -t debugfs none /sys/kernel/debug 2>/dev/null
if [ -f /sys/kernel/debug/clk/clk_summary ]; then
	awk '$1=="cpu"||$1=="ddr"||$1=="ahb" {printf "[check]   %-4s %8.2f MHz\n", $1, $5/1000000}' \
		/sys/kernel/debug/clk/clk_summary
else
	say "  could not read /sys/kernel/debug/clk/clk_summary"
fi

say "Stability test: $ROUNDS round(s)"
FAIL=0
i=1
while [ "$i" -le "$ROUNDS" ]; do
	say "  round $i/$ROUNDS: hashing 500 MiB of zeros..."
	H=$(dd if=/dev/zero bs=1M count=500 2>/dev/null | md5sum | cut -d' ' -f1)
	if [ "$H" = "$EXPECTED" ]; then
		say "  round $i/$ROUNDS: ok ($H)"
	else
		say "  round $i/$ROUNDS: WRONG HASH ($H)"
		FAIL=1
	fi
	i=$((i + 1))
done

say "Kernel log:"
if dmesg | grep -iE 'oops|panic|bug:|segfault'; then
	FAIL=1
else
	say "  no oops/panic/segfault"
fi

if [ "$FAIL" -eq 0 ]; then
	say "PASS. Now use the router normally for a day or two before trusting it."
else
	say "FAIL. Go back to a lower clock or your backup."
	exit 1
fi
