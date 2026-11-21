"""nx2/compact_bodies.py: the lifter's bodies as runs give its readers what the dicts gave.

  - classify: the fast "op" (a word whose top byte none of the encodings has) is the full classify's answer (a64-kit's with
    nx2/insn.py's), for random words of every top byte and for each family's own encodings at random operands;
  - Body: in / [] / get / len / iteration (address order) / keys / values / items of a function with gaps between its runs,
    against the dict walk() made; pickled it is that dict;
  - a real a64_lift.Lifter over a made-up image (two functions, a jump between runs, a loop): walk() and the emit (through
    dict_bodies, as nx2/chunks.py calls it) give the same bodies and the same C as with the kit's dicts.

python -I -B tests/test_compact_bodies.py
"""
import pickle
import random
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import kit  # noqa: E402,F401  (a64-kit on the path)
import a64_lift  # noqa: E402
import insn  # noqa: E402

insn.install()
FULL_CLASSIFY = a64_lift.classify   # (the kit's with insn's, as nx2/lift.py has it before compact_bodies)
BASE_WALK = a64_lift.Lifter.walk
import compact_bodies  # noqa: E402
compact_bodies.install()

BASE = 0x80000000
RET = 0xd65f03c0


def add_x0(n):
    return 0x91000000 | (n << 10)


def b(pc, target):
    return 0x14000000 | (((target - pc) >> 2) & 0x3FFFFFF)


def cbnz_x0(pc, target):
    return 0xb5000000 | ((((target - pc) >> 2) & 0x7FFFF) << 5)


A, B = BASE + 0x1000, BASE + 0x3000
WORDS = {
    # A: a run, a jump over a gap to a second run, back to the first
    A: add_x0(1), A + 4: cbnz_x0(A + 4, A + 0x100), A + 8: add_x0(2), A + 12: RET,
    A + 0x100: add_x0(3), A + 0x104: add_x0(4), A + 0x108: b(A + 0x108, A + 8),
    # B: a loop to its own entry
    B: add_x0(5), B + 4: cbnz_x0(B + 4, B), B + 8: RET,
}


class Image:
    entries = {A, B}
    hooks = {}
    names = {}

    def word(self, pc):
        return WORDS[pc]

    def in_code(self, pc):
        return pc in WORDS

    def read(self, pc, n):
        return b"".join(struct.pack("<I", WORDS[pc + 4 * i]) for i in range(n // 4))


def texts():
    return {w: ("    c->x[0] += %d;" % ((w >> 10) & 0xFFF), 0) for w in WORDS.values() if (w >> 24) == 0x91}


class Classify(unittest.TestCase):
    def test_every_top_byte(self):
        rnd = random.Random(7)
        for top in range(256):
            for _ in range(3000):
                w = (top << 24) | rnd.getrandbits(24)
                pc = BASE + 4 * rnd.getrandbits(20)
                self.assertEqual(a64_lift.classify(w, pc), FULL_CLASSIFY(w, pc), "%08x" % w)

    def test_each_family(self):
        rnd = random.Random(8)
        fams = [(0x7C000000, 0x14000000), (0xFF000010, 0x54000000), (0x7E000000, 0x34000000), (0x7E000000, 0x36000000),
                (0xFFFFFC1F, 0xD61F0000), (0xFFFFFC1F, 0xD63F0000), (0xFFFFFC1F, 0xD65F0000), (0xFFE0001F, 0xD4000001),
                (0xFFE0001F, 0xD4200000), (0xFFE0001F, 0xD4400000), (0xFFFFF0FF, 0xD50330DF), (0xFFFFFFE0, 0xD50B7520),
                (0xFEFFF800, 0xD61F0800), (0xFEFFF800, 0xD63F0800), (0xFFFFFFFF, 0xD65F0BFF), (0xFFFFFFFF, 0xD65F0FFF)]
        for mask, value in fams:
            for _ in range(2000):
                w = value | (rnd.getrandbits(32) & ~mask & 0xFFFFFFFF)
                self.assertNotEqual(FULL_CLASSIFY(w, BASE)[0], "op", "%08x" % w)
                self.assertEqual(a64_lift.classify(w, BASE), FULL_CLASSIFY(w, BASE), "%08x" % w)


class Bodies(unittest.TestCase):
    def test_body_is_the_dict(self):
        L = a64_lift.Lifter(Image())
        for e in (A, B):
            plain, _ = BASE_WALK(L, e)
            body, _ = L.walk(e)
            self.assertIsInstance(body, compact_bodies.Body)
            self.assertEqual(len(body), len(plain))
            self.assertEqual(list(body), sorted(plain))
            self.assertEqual(list(body.keys()), sorted(plain))
            self.assertEqual(dict(body.items()), plain)
            self.assertEqual(list(body.values()), [plain[pc] for pc in sorted(plain)])
            self.assertEqual(body, plain)
            self.assertEqual(min(body), min(plain))
            for pc in range(e - 16, e + 0x120, 2):
                self.assertEqual(pc in body, pc in plain, hex(pc))
                self.assertEqual(body.get(pc), plain.get(pc))
                if pc in plain:
                    self.assertEqual(body[pc], plain[pc])
                else:
                    self.assertRaises(KeyError, body.__getitem__, pc)
            self.assertTrue({A + 0x80}.isdisjoint(body.keys()))
            copy = pickle.loads(pickle.dumps(body))
            self.assertIs(type(copy), dict)
            self.assertEqual(copy, plain)

    def test_emit_is_the_same(self):
        def lift(compact):
            L = a64_lift.Lifter(Image())
            for e in sorted(L.entries):
                L.funcs[e] = L.walk(e) if compact else BASE_WALK(L, e)
            emit = compact_bodies.dict_bodies(L, L.emit_function)
            out = [emit(e, texts()) for e in sorted(L.funcs)]
            self.assertTrue(all(isinstance(b, compact_bodies.Body) for b, _ in L.funcs.values()) == compact)
            return out
        self.assertEqual(lift(True), lift(False))


if __name__ == "__main__":
    unittest.main()
