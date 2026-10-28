#!/usr/bin/env python3
"""Patch the CPU clock in a TP-Link Archer C7 v5 'factory-uboot' dump.

The first-stage bootloader programs the QCA9563 PLLs with hardcoded values.
This tool finds that code by its instruction pattern, checks it, and changes:
  - the CPU PLL multiplier (NINT): CPU = 25 MHz x NINT
  - the AHB post-divider, so the AHB bus (and the SPI flash clock derived
    from it) never runs faster than stock (258.33 MHz)
DDR (650 MHz) is not touched.

Usage:
  python3 patch.py factory-uboot-backup.bin --mhz 925
  python3 patch.py factory-uboot-backup.bin --mhz 775   # back to stock values

Python 3.8+, standard library only.
"""
import argparse
import hashlib
import re
import struct
import sys

REF_MHZ = 25
STOCK_MHZ = 775
STOCK_AHB_MHZ = STOCK_MHZ / 3          # 258.33
PART_SIZE = 0x20000                    # factory-uboot partition: 128 KiB
LOAD_ADDR = 0x9F000000                 # flash is mapped here at boot
TESTED_MHZ = {775, 900, 925}
MIN_MHZ, MAX_MHZ = 775, 1000

# 1) Load of PLL values into registers, followed by the check for an
#    override block at flash 0x4ffe0 (magic 0xaabbccdd):
#      lui  t5, CPU_CFG1>>16     3c0d....
#      lui  t6, DDR_CFG1>>16     3c0e....
#      li   t4, 0                240c0000
#      li   t3, 0                240b0000
#      lui  t7, 0x9f04           3c0f9f04
#      ori  t7, t7, 0xffe0       35efffe0
#      lw   t8, 0(t7)            8df80000
#      lui  t7, 0xaabb           3c0faabb
#      ori  t7, t7, 0xccdd       35efccdd
SIG_LOAD = re.compile(
    rb"\x3c\x0d(..)\x3c\x0e(..)\x24\x0c\x00\x00\x24\x0b\x00\x00"
    rb"\x3c\x0f\x9f\x04\x35\xef\xff\xe0\x8d\xf8\x00\x00"
    rb"\x3c\x0f\xaa\xbb\x35\xef\xcc\xdd",
    re.DOTALL,
)
# 2) t5 is stored to CPU_PLL_CONFIG1 (0xb8050004):
#      lui t7,0xb805 / ori t7,t7,0x4 / sw t5,0(t7)
SIG_STORE_CPU = bytes.fromhex("3c0fb80535ef0004aded0000")
# 3) Write of CPU_DDR_CLOCK_CONTROL (0xb8050010) with PLLs bypassed:
#      lui t7,0xb805 / ori t7,t7,0x10 / lui t8,0x1 / ori t8,t8,IMM / sw t8,0(t7)
SIG_CLKCTRL = re.compile(
    rb"\x3c\x0f\xb8\x05\x35\xef\x00\x10\x3c\x18\x00\x01\x37\x18(..)\xad\xf8\x00\x00",
    re.DOTALL,
)
MAX_GAP = 0x400  # all of the above sit inside one short routine


def die(msg):
    sys.stdout.flush()
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def find_once(pattern, data, what):
    hits = list(pattern.finditer(data)) if hasattr(pattern, "finditer") else \
        [m.start() for m in re.finditer(re.escape(pattern), data)]
    if len(hits) != 1:
        die(f"expected exactly one {what}, found {len(hits)}. "
            "Is this a factory-uboot (mtd0) dump from an Archer C7 v5?")
    return hits[0]


def ahb_postdiv_for(cpu_mhz):
    """Smallest post-divider that keeps AHB at or below the stock rate."""
    postdiv = 0
    while cpu_mhz / (postdiv + 1) > STOCK_AHB_MHZ + 0.01:
        postdiv += 1
    return postdiv


def analyse(data):
    load = find_once(SIG_LOAD, data, "PLL value load (lui t5/t6 + 0xaabbccdd check)")
    cpu_imm_off = load.start() + 2
    cpu_imm = struct.unpack(">H", data[cpu_imm_off:cpu_imm_off + 2])[0]

    store = find_once(SIG_STORE_CPU, data, "store of t5 to CPU_PLL_CONFIG1")
    clk = find_once(SIG_CLKCTRL, data, "CPU_DDR_CLOCK_CONTROL write")
    clk_imm_off = clk.start() + 14
    clk_imm = struct.unpack(">H", data[clk_imm_off:clk_imm_off + 2])[0]

    if not (load.start() < store < clk.start() <= load.start() + MAX_GAP):
        die("PLL code found but not in the expected order/distance; refusing.")

    cfg1 = cpu_imm << 16
    if cfg1 & 0x3FFFF:
        die(f"CPU_PLL_CONFIG1 0x{cfg1:08x} has a fractional part; unexpected build.")
    nint = (cfg1 >> 18) & 0x1FF
    clk_ctrl = 0x10000 | clk_imm
    if clk_ctrl & 0x7FFF != 0x001C:
        die(f"CPU_DDR_CLOCK_CONTROL 0x{clk_ctrl:08x}: unexpected bits; refusing.")
    postdiv = (clk_ctrl >> 15) & 0x1F
    if not (MIN_MHZ <= REF_MHZ * nint <= MAX_MHZ) or postdiv not in (2, 3, 4):
        die(f"current values (NINT {nint}, AHB post-div {postdiv}) are outside "
            "anything this tool writes; refusing.")
    return {"cpu_imm_off": cpu_imm_off, "clk_imm_off": clk_imm_off,
            "nint": nint, "postdiv": postdiv}


def describe(nint, postdiv):
    cpu = REF_MHZ * nint
    return f"CPU {cpu} MHz (NINT {nint}), AHB {cpu / (postdiv + 1):.2f} MHz (/{postdiv + 1})"


def patch(data, mhz):
    info = analyse(data)
    nint = mhz // REF_MHZ
    postdiv = ahb_postdiv_for(mhz)
    out = bytearray(data)
    out[info["cpu_imm_off"]:info["cpu_imm_off"] + 2] = struct.pack(">H", (nint << 18) >> 16)
    out[info["clk_imm_off"]:info["clk_imm_off"] + 2] = struct.pack(">H", ((postdiv << 15) | 0x1C) & 0xFFFF)
    # self-check: re-analyse the result
    new = analyse(bytes(out))
    assert (new["nint"], new["postdiv"]) == (nint, postdiv)
    return info, bytes(out), nint, postdiv


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help="factory-uboot dump (128 KiB, from /dev/mtd0)")
    ap.add_argument("--mhz", type=int, required=True,
                    help=f"target CPU clock, multiple of 25 between {MIN_MHZ} and {MAX_MHZ}")
    ap.add_argument("-o", "--output", help="output file (default: factory-uboot-<MHZ>mhz.bin)")
    args = ap.parse_args()

    if args.mhz % REF_MHZ or not MIN_MHZ <= args.mhz <= MAX_MHZ:
        die(f"--mhz must be a multiple of {REF_MHZ} between {MIN_MHZ} and {MAX_MHZ}")

    print(f"[1/4] reading {args.input}")
    data = open(args.input, "rb").read()
    if len(data) != PART_SIZE:
        die(f"file is {len(data)} bytes, expected {PART_SIZE} (one factory-uboot partition)")
    banner = re.search(rb"U-Boot [ -~]+", data)
    print(f"      {banner.group().decode() if banner else 'no U-Boot banner found'}")
    print(f"      md5 {hashlib.md5(data).hexdigest()}")

    print("[2/4] locating PLL setup code")
    info, out, nint, postdiv = patch(data, args.mhz)
    print(f"      CPU multiplier at 0x{info['cpu_imm_off'] - 2:05x} "
          f"(0x{LOAD_ADDR + info['cpu_imm_off'] - 2:08x})")
    print(f"      AHB divider    at 0x{info['clk_imm_off'] - 2:05x} "
          f"(0x{LOAD_ADDR + info['clk_imm_off'] - 2:08x})")
    print(f"      current: {describe(info['nint'], info['postdiv'])}")
    print(f"      target : {describe(nint, postdiv)}   DDR unchanged")

    if out == data:
        print("[3/4] image already has these values - nothing to write")
        return
    diff = [i for i in range(len(data)) if data[i] != out[i]]
    print(f"[3/4] changed {len(diff)} byte(s) at {', '.join(hex(i) for i in diff)}")
    if args.mhz not in TESTED_MHZ:
        print(f"      WARNING: {args.mhz} MHz has not been tested. "
              f"Tested: {', '.join(str(m) for m in sorted(TESTED_MHZ))} MHz.")

    dst = args.output or f"factory-uboot-{args.mhz}mhz.bin"
    with open(dst, "wb") as f:
        f.write(out)
    print(f"[4/4] wrote {dst}")
    print(f"      md5 {hashlib.md5(out).hexdigest()}")


if __name__ == "__main__":
    main()
