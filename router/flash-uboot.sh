#!/bin/sh
# flash-uboot.sh - write a patched factory-uboot to a TP-Link Archer C7 v5
# running OpenWrt, then read it back and verify it. Never reboots for you.
#
# usage: sh flash-uboot.sh <image.bin>            normal patch (few bytes differ)
#        sh flash-uboot.sh <backup.bin> --restore  write your backup back
#
# Safety checks: board must be tplink,archer-c7-v5; image must be 128 KiB and
# contain a U-Boot 1.1.4 banner; a normal flash may differ from what is on the
# chip by at most MAX_DIFF bytes (a clock patch changes 2).

PART=factory-uboot
BOARD=tplink,archer-c7-v5
SIZE=131072
MAX_DIFF=8

say() { echo "[flash-uboot] $*"; }
die() { echo "[flash-uboot] ERROR: $*"; exit 1; }

FILE="$1"
RESTORE=0
[ "$2" = "--restore" ] && RESTORE=1
[ -n "$FILE" ] || die "usage: sh $0 <image.bin> [--restore]"
[ -f "$FILE" ] || die "file not found: $FILE"

say "Step 1/7: checking the board"
B=$(cat /tmp/sysinfo/board_name 2>/dev/null)
say "  board: ${B:-unknown}"
[ "$B" = "$BOARD" ] || die "this script is only for $BOARD"

say "Step 2/7: checking image $FILE"
S=$(wc -c < "$FILE")
[ "$S" -eq "$SIZE" ] || die "image is $S bytes, expected $SIZE"
grep -q "U-Boot 1.1.4" "$FILE" || die "no U-Boot 1.1.4 banner in image - wrong file?"
say "  size ok, md5 $(md5sum "$FILE" | cut -d' ' -f1)"

say "Step 3/7: locating partition '$PART'"
DEV=$(grep "\"$PART\"" /proc/mtd | cut -d: -f1)
[ -n "$DEV" ] || die "partition $PART not found in /proc/mtd"
say "  /dev/$DEV ($(grep "^$DEV:" /proc/mtd))"

say "Step 4/7: comparing image with what is on flash now"
cmp -l "/dev/$DEV" "$FILE" > /tmp/flash-uboot.diff 2>/dev/null
N=$(wc -l < /tmp/flash-uboot.diff)
if [ "$N" -eq 0 ]; then say "  flash already holds this image. Nothing to do."; exit 0; fi
say "  $N byte(s) differ:"
awk 'NR<=8 {printf "    offset 0x%05x: %s -> %s (octal)\n", $1-1, $2, $3}' /tmp/flash-uboot.diff
[ "$N" -gt 8 ] && say "    ..."
if [ "$N" -gt "$MAX_DIFF" ] && [ "$RESTORE" -ne 1 ]; then
	die "more than $MAX_DIFF bytes differ - not a clock patch of this bootloader. Use --restore only to write your own backup."
fi

say "Step 5/7: checking for a PLL override block"
PT=$(grep '"partition-table"' /proc/mtd | cut -d: -f1)
if [ -n "$PT" ]; then
	printf '\252\273\314\335' > /tmp/flash-uboot.magic
	dd if="/dev/$PT" bs=1 skip=65504 count=4 2>/dev/null > /tmp/flash-uboot.tail
	if cmp -s /tmp/flash-uboot.magic /tmp/flash-uboot.tail; then
		say "  override magic 0xaabbccdd found at flash 0x4ffe0: the bootloader takes"
		say "  its PLL values from there, so a patched image would change nothing."
		[ "$RESTORE" -eq 1 ] || die "stopping - see README, 'PLL override block'"
	else
		say "  none (bootloader uses its built-in values)"
	fi
fi

say "Step 6/7: making the bootloader partition writable (kmod-mtd-rw)"
if ! lsmod | grep -q '^mtd_rw'; then
	if ! insmod mtd-rw i_want_a_brick=1 2>/dev/null; then
		say "  installing kmod-mtd-rw..."
		if command -v apk >/dev/null 2>&1; then
			apk update && apk add kmod-mtd-rw || die "could not install kmod-mtd-rw"
		else
			opkg update && opkg install kmod-mtd-rw || die "could not install kmod-mtd-rw"
		fi
		insmod mtd-rw i_want_a_brick=1 || die "insmod mtd-rw failed"
	fi
fi
say "  mtd-rw loaded"

say "Step 7/7: writing $FILE to /dev/$DEV - DO NOT power off"
TRY=1
while :; do
	mtd write "$FILE" "$PART" || say "  mtd reported an error, verifying anyway"
	if cmp -s "/dev/$DEV" "$FILE"; then
		say "VERIFIED: /dev/$DEV matches $FILE byte for byte (attempt $TRY)."
		say "You can reboot now:  reboot"
		exit 0
	fi
	[ "$TRY" -ge 3 ] && break
	TRY=$((TRY + 1))
	say "  readback mismatch, writing again (attempt $TRY)..."
done

echo
say "!!! VERIFY FAILED - DO NOT REBOOT OR POWER OFF !!!"
say "Write your backup back while the router is still running:"
say "  sh $0 /tmp/factory-uboot-backup.bin --restore"
exit 1
