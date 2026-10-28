"""Tests for patch.py on a synthetic image (no TP-Link binary needed).

Run from the repo root:  python3 -m unittest discover tests
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("patch", ROOT / "patch.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

# Instruction words copied from the real factory-uboot PLL routine.
LOAD = bytes.fromhex(
    "3c0d007c" "3c0e0068" "240c0000" "240b0000"
    "3c0f9f04" "35efffe0" "8df80000" "3c0faabb" "35efccdd")
STORE = bytes.fromhex("3c0fb805" "35ef0004" "aded0000")
CLK = bytes.fromhex("3c0fb805" "35ef0010" "3c180001" "3718001c" "adf80000")
NOPS = bytes(4 * 16)


def make_image(blocks):
    img = bytearray(b"\xff" * patch.PART_SIZE)
    img[0x100:0x100 + 32] = b"U-Boot 1.1.4-test (synthetic)\0\0\0"
    pos = 0x3000
    for b in blocks:
        img[pos:pos + len(b)] = b
        pos += len(b) + len(NOPS)
    return bytes(img)


STOCK = make_image([LOAD, STORE, CLK])


class PatchTests(unittest.TestCase):
    def test_stock_is_read_correctly(self):
        info = patch.analyse(STOCK)
        self.assertEqual((info["nint"], info["postdiv"]), (31, 2))

    def test_925_changes_two_bytes(self):
        _, out, nint, postdiv = patch.patch(STOCK, 925)
        self.assertEqual((nint, postdiv), (37, 3))
        self.assertEqual(sum(a != b for a, b in zip(STOCK, out)), 2)

    def test_roundtrip_back_to_stock(self):
        _, oc, _, _ = patch.patch(STOCK, 925)
        _, back, _, _ = patch.patch(oc, 775)
        self.assertEqual(back, STOCK)

    def test_ahb_never_above_stock(self):
        for mhz in range(patch.MIN_MHZ, patch.MAX_MHZ + 1, 25):
            pd = patch.ahb_postdiv_for(mhz)
            self.assertLessEqual(mhz / (pd + 1), patch.STOCK_AHB_MHZ + 0.01, mhz)

    def test_missing_code_is_refused(self):
        with self.assertRaises(SystemExit):
            patch.analyse(make_image([STORE, CLK]))

    def test_duplicate_code_is_refused(self):
        with self.assertRaises(SystemExit):
            patch.analyse(make_image([LOAD, LOAD, STORE, CLK]))

    def test_wrong_order_is_refused(self):
        with self.assertRaises(SystemExit):
            patch.analyse(make_image([CLK, STORE, LOAD]))


if __name__ == "__main__":
    unittest.main()
