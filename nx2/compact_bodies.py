"""The lifter's functions held small: the builder's translation step in less memory (nx2/lift.py installs it).

a64-kit's Lifter.walk returns each function's body as a dict {pc: (classify(w, pc), w)}: a dict entry, a tuple and two ints
for every instruction. Zelda's game has 24 million of them in 294,522 functions: ~3.4 GB of the ~4.1 GB the lift held after
discover, and every forked emit or edit process touched those pages too (the published v0.1.4 builder: 6.0 GB at its peak with
7 processes; 4.9 GB with one). Here a body is a Body: its runs of consecutive addresses only, each instruction read from the
image (and classified) again when it is asked for. After discover the lift holds ~0.9 GB; the emit takes one function's dict at
a time (dict_bodies, around the emit), so its speed stays the dict's.

The lifted C is the same, byte for byte (Body iterates in address order, a dict in walk order: no reader depends on that).
Pickled (chunks.spawn_emit's new processes on Windows) a Body is the plain dict, as before. LONGSHOT_LIFT_DICTS=1: the kit's
dicts, as before (to compare).

It wraps the kit (nx2/ convention, third_party/a64-kit/PROVENANCE.md) instead of editing it:
  - Lifter.walk: its dict made a Body;
  - a64_lift.classify: a word whose top byte none of its encodings has is an "op" at once, without its ten tests (the same
    answer: tests/test_compact_bodies.py checks every top byte).
"""
import bisect
import itertools
import os
import struct

import a64_lift

_installed = False
# (the top bytes of every encoding a64_lift.classify names, with nx2/insn.py's: B/BL, B.cond, CBZ/CBNZ, TBZ/TBNZ, BR/BLR/RET
# and the authenticated BRA*/BLRA*/RETA* (0xD6, 0xD7), SVC/BRK/HLT, ISB/IC. install() goes after insn.install(), around its classify)
SPECIAL_TOP = frozenset((0x14, 0x15, 0x16, 0x17, 0x94, 0x95, 0x96, 0x97, 0x54, 0x34, 0x35, 0x36, 0x37, 0xB4, 0xB5, 0xB6,
                         0xB7, 0xD4, 0xD5, 0xD6, 0xD7))
_MAYBE = bytes(1 if t in SPECIAL_TOP else 0 for t in range(256))
OP = ("op",)


def enabled():
    return os.environ.get("LONGSHOT_LIFT_DICTS") != "1"


class Body:
    """{pc: (kind, word)} to its readers (in, [], get, len, iteration, keys/values/items), from the instruction runs alone"""
    __slots__ = ("img", "starts", "ends")

    def __init__(self, img, pcs):
        self.img = img
        starts, ends = [], []
        for pc in sorted(pcs):
            if ends and pc == ends[-1]:
                ends[-1] = pc + 4
            else:
                starts.append(pc)
                ends.append(pc + 4)
        self.starts, self.ends = tuple(starts), tuple(ends)

    def __contains__(self, pc):
        i = bisect.bisect_right(self.starts, pc) - 1
        return i >= 0 and pc < self.ends[i] and not (pc - self.starts[i]) & 3

    def __getitem__(self, pc):
        if pc not in self:
            raise KeyError(pc)
        w = self.img.word(pc)
        return (a64_lift.classify(w, pc), w)

    def get(self, pc, default=None):
        return self[pc] if pc in self else default

    def __iter__(self):
        return itertools.chain.from_iterable(map(range, self.starts, self.ends, itertools.repeat(4)))

    def keys(self):
        return iter(self)

    def __len__(self):
        return (sum(self.ends) - sum(self.starts)) >> 2

    def __bool__(self):
        return bool(self.starts)

    def words(self):
        """(pc, w) in address order: each run's words in one read where the image is an NxImage (its mem from MAIN_BASE)"""
        img = self.img
        mem = getattr(img, "mem", None)
        if mem is None:
            word = img.word
            return ((pc, word(pc)) for pc in self)
        import nx_image
        base = nx_image.MAIN_BASE
        return itertools.chain.from_iterable(
            zip(range(s, e, 4), struct.unpack_from("<%dI" % ((e - s) >> 2), mem, s - base)) for s, e in zip(self.starts, self.ends))

    def items(self):
        classify = a64_lift.classify
        for pc, w in self.words():
            yield pc, (classify(w, pc), w)

    def values(self):
        return (v for _, v in self.items())

    def as_dict(self):
        return dict(self.items())

    def __eq__(self, other):
        return self.as_dict() == (other.as_dict() if isinstance(other, Body) else other)

    __hash__ = None

    def __reduce__(self):
        return (dict, (list(self.items()),))


def dict_bodies(L, emit):
    """emit (a Lifter's bound emit_function, wrapped as nx2/ wraps it) with the function's body a dict while it runs: the emit
    reads every instruction several times (dead_flags, dirty_regs, the text), a dict's lookup is the fast one there"""
    def emit_function(e, texts):
        got = L.funcs.get(e)
        if got is None or not isinstance(got[0], Body):
            return emit(e, texts)
        L.funcs[e] = (got[0].as_dict(), got[1])   # (the labels set is the same one: the emit may add to it, as before)
        try:
            return emit(e, texts)
        finally:
            L.funcs[e] = got
    return emit_function


def install():
    global _installed
    if _installed or not enabled():
        return
    _installed = True
    base_classify = a64_lift.classify

    def classify(w, pc):
        """(a64_lift.classify, an "op" at once when the word's top byte is none of its encodings')"""
        if not _MAYBE[w >> 24]:
            return OP
        return base_classify(w, pc)

    a64_lift.classify = classify
    base_walk = a64_lift.Lifter.walk

    def walk(self, entry):
        body, labels = base_walk(self, entry)
        return Body(self.img, body), labels

    a64_lift.Lifter.walk = walk
