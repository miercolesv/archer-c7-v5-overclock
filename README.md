# Archer C7 v5 overclock

Run the QCA9563 in a TP-Link Archer C7 v5 at **900 or 925 MHz** instead of the stock 775 MHz, entirely from software on a router that already runs OpenWrt.

The CPU clock is programmed by the first-stage bootloader (`factory-uboot`, `/dev/mtd0`). `patch.py` changes two instructions in **your own dump** of that bootloader, and `router/flash-uboot.sh` writes it back and checks it byte for byte before you reboot.

> [!WARNING]
> This rewrites the first code the router runs. The QCA9563 has no recovery mode below it: if the patched bootloader doesn't start, the only fix is an SPI programmer (e.g. CH341A with a SOIC-8 clip). The scripts make a bad *write* very unlikely, but they can't guarantee that *your* chip is stable at a higher clock. Read [If it doesn't boot](#if-it-doesnt-boot) first.

## Results

Tested on one Archer C7 v5 running OpenWrt 25.12.6 (kernel 6.12.112):

| CPU | AHB | DDR | Result |
|---|---|---|---|
| 775 MHz (stock) | 258.33 MHz | 650 MHz | — |
| 900 MHz | 225.00 MHz | 650 MHz | boots, stability test passed |
| 925 MHz | 231.25 MHz | 650 MHz | boots, clocks confirmed |

These are short tests on a single unit. Long-term stability reports are welcome. Software flow offloading (Network → Firewall) is free and often helps routing throughput more than the clock does; try that first if routing speed is your goal.

## Requirements

- Archer C7 **v5** with OpenWrt installed (the flash script checks for board `tplink,archer-c7-v5`). Other QCA956x boards, including the Archer A7 v5, are untested and refused.
- SSH access to the router. Internet access on the router the first time, to install `kmod-mtd-rw`.
- Python 3.8+ on your computer (standard library only).

## Steps

Replace `<router-ip>` with your router's address.

**1. Back up every partition to your computer.** Keep these files somewhere safe; `art` holds your unit's Wi-Fi calibration and cannot be recreated.

```sh
mkdir -p c7v5-backup && cd c7v5-backup
ssh root@<router-ip> 'cat /proc/mtd' | tee mtd.txt
for dev in $(awk -F: '/^mtd/ {print $1}' mtd.txt); do
  name=$(grep "^$dev:" mtd.txt | sed 's/.*"\(.*\)"/\1/')
  echo ">>> dumping $dev ($name)"
  ssh root@<router-ip> "cat /dev/$dev" > "$dev-$name.bin"
done
cp mtd0-factory-uboot.bin ../factory-uboot-backup.bin
cd ..
```

Use `cat` over SSH like this rather than LuCI's "Save mtdblock contents": LuCI rounds sizes down, which cuts off partitions that aren't a multiple of 512 bytes (e.g. `kernel`).

**2. Patch your dump.**

```sh
python3 patch.py factory-uboot-backup.bin --mhz 900
```

It prints where it found the PLL code, the current and target clocks, and which bytes changed (2 for this build). If it can't find the code exactly once, in the expected order, it refuses to patch.

**3. Copy everything to the router.** Include the backup, so you can restore without a reboot if anything goes wrong.

```sh
scp -O factory-uboot-900mhz.bin factory-uboot-backup.bin router/flash-uboot.sh router/check-clocks.sh root@<router-ip>:/tmp/
```

**4. Flash it.**

```sh
ssh root@<router-ip>
sh /tmp/flash-uboot.sh /tmp/factory-uboot-900mhz.bin
```

The script checks the board, image size and U-Boot banner; shows exactly which bytes will change and refuses if more than 8 differ; checks for a [PLL override block](#pll-override-block); loads `kmod-mtd-rw`; writes; then reads the flash back and compares it with the image.

- **`VERIFIED`** → `reboot`.
- **`VERIFY FAILED`** → do **not** reboot or power off. Run `sh /tmp/flash-uboot.sh /tmp/factory-uboot-backup.bin --restore` and wait for `VERIFIED`.

**5. Check it.** After about a minute the router is back on the same address.

```sh
scp -O router/check-clocks.sh root@<router-ip>:/tmp/   # /tmp is wiped on reboot
ssh root@<router-ip> sh /tmp/check-clocks.sh
```

It prints the clocks and hashes 500 MiB of zeros five times; every hash must be `d8b61b2c0025919d5321461045c8226f`, and the kernel log must show no oops. Then use the router normally for a day or two. Random reboots or freezes mean your chip isn't stable at that clock: go one step lower.

**6. Optionally go higher.** Repeat steps 2–5 with `--mhz 925`. The patcher works on any of your dumps, so you can patch the backup or the current image.

**7. Clean up.** `apk del kmod-mtd-rw` (or `opkg remove kmod-mtd-rw`) makes the bootloader partitions read-only again.

## Reverting

Copy `factory-uboot-backup.bin` and `flash-uboot.sh` to `/tmp`, run

```sh
sh /tmp/flash-uboot.sh /tmp/factory-uboot-backup.bin
```

then `reboot` after `VERIFIED`. If you've lost the backup, `python3 patch.py <any dump> --mhz 775` writes the stock values back (for the tested build this gives a byte-identical original).

## If it doesn't boot

Power-cycle once and give it 4 minutes. If it's still dead, the bootloader has to be rewritten with an SPI programmer. `factory-uboot` is the first 128 KiB of the 16 MiB flash chip:

```sh
flashrom -p ch341a_spi -r chip.bin                                   # read the chip; do it twice and compare
dd if=factory-uboot-backup.bin of=chip.bin bs=64k conv=notrunc       # put your backup at offset 0
flashrom -p ch341a_spi -w chip.bin
```

## Frequencies

`patch.py --mhz` accepts multiples of 25 from 775 to 1000. It picks the smallest AHB divider that keeps AHB at or below stock (258.33 MHz):

| CPU | NINT | AHB | Status |
|---|---|---|---|
| 775 | 31 | 258.33 (÷3) | stock |
| 800 | 32 | 200.00 (÷4) | untested |
| 825 | 33 | 206.25 (÷4) | untested |
| 850 | 34 | 212.50 (÷4) | untested |
| 875 | 35 | 218.75 (÷4) | untested |
| 900 | 36 | 225.00 (÷4) | tested |
| 925 | 37 | 231.25 (÷4) | tested |
| 950 | 38 | 237.50 (÷4) | untested |
| 975 | 39 | 243.75 (÷4) | untested |
| 1000 | 40 | 250.00 (÷4) | untested |

## How it works

The QCA9563's PLL controller sits at `0x18050000`. With a 25 MHz reference:

| Register | Offset | Stock value | Meaning |
|---|---|---|---|
| CPU_PLL_CONFIG | `0x00` | `0x40001000` | ref divider 1, output divider 0 |
| CPU_PLL_CONFIG1 | `0x04` | `0x007c0000` | NINT = bits 26:18 = 31 → 25 × 31 = **775 MHz** |
| DDR_PLL_CONFIG1 | `0x0c` | `0x00680000` | NINT 26 → **650 MHz** |
| CPU_DDR_CLOCK_CONTROL | `0x10` | `0x0001001c` | AHB post-divider = bits 19:15 = 2 → 775 ÷ 3 = **258.33 MHz** |

The first-stage bootloader's assembly loads those values with immediate instructions, so the patch is:

| Instruction | Stock | 925 MHz |
|---|---|---|
| CPU multiplier (`lui t5, …`) | `lui t5, 0x7c` | `lui t5, 0x94` |
| Clock control (`ori t8, t8, …`) | `ori t8, t8, 0x001c` | `ori t8, t8, 0x801c` |

Why the AHB divider changes too: AHB is divided off the CPU PLL, so with ÷3 it would rise to 308 MHz at 925. The SPI flash clock is derived from AHB, and the bootloader reads the flash before Linux runs. With ÷4 everything on AHB runs slightly *slower* than stock. Under Linux the flash driver caps the flash at 25 MHz from the device tree regardless.

Things checked on the tested build:

- Only `factory-uboot` programs the CPU PLL. The second-stage `u-boot` (mtd1) and both decompressed U-Boot images only read the clock register.
- The changed bytes sit in the uncompressed bootstrap. The U-Boot image's CRC covers only the compressed part, and no code was found that verifies the bootstrap. The 16 bytes at the very end of `factory-uboot` look like a hash of unknown purpose; the patch leaves them unchanged and the patched images boot.
- DDR timing, the UART (clocked from the 25 MHz reference) and the Ethernet/switch clocks are untouched.

### PLL override block

Before using its built-in values, the bootloader looks for the magic `0xaabbccdd` at flash `0x4ffe0` (the last 32 bytes of the `partition-table` partition). If present, it loads the CPU/DDR PLL values from there instead. It's blank (`0xff`) on the tested unit. If yours has it set, the patch would have no effect, so `flash-uboot.sh` stops. Please open an issue with a dump of that partition.

## Known bootloader builds

| factory-uboot banner | Code offsets | Status |
|---|---|---|
| `U-Boot 1.1.4-g4480d5f9-dirty (May 20 2019 - 18:45:35)` | `0x32c0`, `0x33b8` | tested |

`patch.py` finds the code by pattern, not by fixed offset, so other builds may work. If yours differs, please report the banner `patch.py` prints and whether it booted.

## Tests

```sh
python3 -m unittest discover tests
```

The tests use a synthetic image built from the instruction pattern; no TP-Link binary is included in this repository.

## License

MIT, see [LICENSE](LICENSE). No warranty: you flash your bootloader at your own risk.
