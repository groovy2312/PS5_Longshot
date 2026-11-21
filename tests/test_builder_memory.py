"""The builder's memory rules (ps5/builder/pipeline.py), on made-up lifted C, with no game and no compiler:

  - cut_parts: a chunk under the cap is one part; over it, parts that are runs of whole functions, which with the chunk's head
    put back in order are the chunk again, each within the cap where its functions allow; a giant function (split_giants cuts it
    at its compile) is counted at its pieces' size, and one function past the cap alone is a part of its own;
  - Gate: within a limit, a compile waits until those running leave room, and one always runs (a need past the limit alone);
  - lift_processes: one process up to 4 GB, more above, never past the cores; 0 (the lifter's own rule) without --memory.

python -I -B tests/test_builder_memory.py
"""
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "ps5", "builder"), os.path.join(ROOT, "nx2")]
import pipeline  # noqa: E402

HEAD = b'#include "a64.h"\nvoid f_80000000(Ctx *A64_NOESC);\n'


def fn(addr, nbytes):
    head = b"void f_%08x(Ctx *__restrict A64_NOESC c) {\n" % addr
    line = b"c->x[0] += 1;\n"
    return head + line * max(1, (nbytes - len(head) - 2) // len(line)) + b"}\n"


def chunk(sizes):
    return HEAD + b"".join(fn(0x80000000 + 0x100 * i, n) for i, n in enumerate(sizes))


def rejoin(text, parts):
    if len(parts) == 1:
        return text[parts[0][0]:parts[0][1]]
    return HEAD + b"".join(text[a:b] for a, b, _ in parts)


class Cut(unittest.TestCase):
    def test_small_chunk_is_whole(self):
        text = chunk([100 << 10] * 20)
        parts = pipeline.cut_parts(text, 1500, 256 << 10)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][:2], (0, len(text)))

    def test_big_chunk_cut_in_order(self):
        text = chunk([200 << 10] * 120)   # (~24 MB of small functions: ~1.6 GB whole)
        whole = pipeline.cut_parts(text, 100000, 256 << 10)[0][2]
        self.assertGreater(whole, 1500)
        for cap in (900, 1200, 1500):
            parts = pipeline.cut_parts(text, cap, 256 << 10)
            self.assertGreater(len(parts), 1)
            self.assertEqual(rejoin(text, parts), text)
            for a, b, need in parts:
                self.assertLessEqual(need, cap)
                self.assertTrue(text[a:].startswith(b"void f_"))

    def test_giant_counted_as_pieces(self):
        text = chunk([100 << 10] * 5 + [8 << 20] + [100 << 10] * 5)
        parts = pipeline.cut_parts(text, 1500, 256 << 10)
        self.assertEqual(len(parts), 1, "an 8 MB function is cut by split_giants: ~0.8 GB, not 2.4")
        no_split = pipeline.cut_parts(text, 1500, 1 << 30)   # (counted whole: it stands alone in a part past the cap)
        self.assertEqual(rejoin(text, no_split), text)
        self.assertTrue(any(need > 1500 for _, _, need in no_split))

    def test_mem_need_matches_cut(self):
        text = chunk([150 << 10] * 30)
        self.assertEqual(pipeline.mem_need(text), pipeline.cut_parts(text, 100000, 256 << 10)[0][2])


class Gating(unittest.TestCase):
    def test_limit(self):
        gate = pipeline.Gate(2000)
        a = gate.enter(1200)
        got = []
        t = threading.Thread(target=lambda: got.append(gate.enter(1200)))
        t.start()
        time.sleep(1.5)
        self.assertEqual(got, [], "two of 1.2 GB do not fit in 2 GB")
        gate.leave(a)
        t.join(5)
        self.assertEqual(len(got), 1)
        gate.leave(got[0])
        big = gate.enter(5000)   # (alone: it runs)
        gate.leave(big)


class Lift(unittest.TestCase):
    def test_processes(self):
        self.assertEqual(pipeline.lift_processes(0), 0)
        for gb in (2, 3, 4):
            self.assertEqual(pipeline.lift_processes(gb), 1)
        cores = (os.cpu_count() or 2) - 1
        prev = 1
        for gb in (5, 6, 8, 12, 16, 64):
            n = pipeline.lift_processes(gb)
            self.assertGreaterEqual(n, prev)
            self.assertLessEqual(n, max(1, cores))
            if n > 1:   # (n workers and the side process, each LIFT_MORE_GB, over the one process)
                self.assertLessEqual(pipeline.LIFT_ONE_GB + (n + 1) * pipeline.LIFT_MORE_GB, gb + 1e-9)
            prev = n


if __name__ == "__main__":
    unittest.main()
