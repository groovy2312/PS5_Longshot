#!/usr/bin/env python3
"""The two shader lists a PS5 title folder carries, so its first start can build the game's pipelines before play
(gpu/vk/vk_draw.cpp prepare_run, precompile_run) instead of compiling each the first time it is drawn.

    python -I ps5/builder/shader_lists.py --src <folder with nx2/ and assets/> --nup <game.nup> --work <dir> --out <title folder>

- longshot_shaders.bin: every shader program in the player's own game (nx2/shader_census.py), made from the few romfs folders
  the census reads (about 290 MB of the 26 GB romfs, taken from the .nup with nx2/nup.py --paths). It holds game code: it is
  made on the player's machine from their game, never shipped or committed.
- longshot_pipeline_states_shipped.bin: the pipeline state list of assets/pipeline_states/ (no game code: hashes, formats and
  draw state), merged as nx2/make_game.py merges it for the PC, less the lists converted from another emulator's records
  (TRIMMED): those were never drawn by Longshot itself, none of their records turned up in any recorded list, the PS5's own play
  included (2026-11-19: 0 of 13,446), and they would add a fifth to the first start's work on the console.

Why on the PS5: a pipeline the driver compiles in play leaves its compile's memory in the title heap, freed but kept (a cold
driver cache: 906 MiB of heap mapped for 489 in use, against 570 for 485 warm, in the same Kokiri walk on a base console), and
the base console then runs out entering a new area (Castle Town, issues #4 and #24). Built before play and in a launch of its
own, which ends and gives that memory back, play then finds every listed pipeline in the driver's cache.
"""
import argparse
import os
import subprocess
import sys

# what nx2/shader_census.py reads (nx2/make_game.py CENSUS_READS: Shader, Layout2Shader, Lib, Movie (its Ndve), Effect, the
# zstd dictionaries), as nup.py --paths prefixes
CENSUS_PATHS = ("Shader/", "Layout2Shader/", "Lib/", "Movie/Ndve/", "Effect/", "Pack/ZsDic.pack.zs")
# contributed lists left out of the PS5's list (above): converted, not recorded
TRIMMED = ("pmwork-personal-fritchii-recipe",)
SEED = "longshot_shaders.bin"
LIST = "longshot_pipeline_states_shipped.bin"


def say(text):
    print(text, flush=True)


def state_list(src, out):
    """The trimmed merged list into out/LIST: how many pipelines, how many left out"""
    sys.path.insert(0, os.path.join(src, "nx2"))
    import pipeline_states
    d = pipeline_states.sources_dir(src)
    if not os.path.isdir(d):
        return 0, 0
    kept, dropped = [], 0
    for path in pipeline_states.sources(d):
        recs = pipeline_states.read(path)
        if os.path.basename(path).startswith(TRIMMED):
            dropped += len(recs)
            continue
        kept.append(recs)
    lists, lost = pipeline_states.as_v2(kept, force=True)
    recs = pipeline_states.unique(lists)
    data = pipeline_states.pack(recs)
    dst = os.path.join(out, LIST)
    with open(dst + ".new", "wb") as f:
        f.write(data)
    os.replace(dst + ".new", dst)
    return len(recs), dropped + lost


def seed(src, nup, work, out, env=None):
    """longshot_shaders.bin into out from the .nup's census folders (extracted into work/census once per .nup)"""
    census = os.path.join(work, "census")
    stamp = "%d %d" % (os.path.getsize(nup), int(os.path.getmtime(nup)))
    mark = os.path.join(census, ".from")
    if not (os.path.exists(mark) and open(mark).read() == stamp):
        subprocess.run([sys.executable, "-I", "-u", os.path.join(src, "nx2", "nup.py"), nup, "--only", "romfs", "--paths",
                        ",".join(CENSUS_PATHS), "--extract", census], env=env, check=True, stdout=subprocess.DEVNULL)
        open(mark, "w").write(stamp)
    dst = os.path.join(out, SEED)
    subprocess.run([sys.executable, "-I", "-u", os.path.join(src, "nx2", "shader_census.py"), os.path.join(census, "romfs"),
                    "--out", dst + ".new"], env=env, check=True, stdout=subprocess.DEVNULL)
    os.replace(dst + ".new", dst)
    return os.path.getsize(dst)


def make(src, nup, work, out, env=None):
    size = seed(src, nup, work, out, env)
    n, dropped = state_list(src, out)
    say("shader lists: %s (%d MB), %s (%d pipelines; %d converted ones left out)" % (SEED, size >> 20, LIST, n, dropped))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="the folder with nx2/ and assets/ (the repository, or the kit's src)")
    ap.add_argument("--nup", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True, help="the title folder")
    a = ap.parse_args()
    make(os.path.abspath(a.src), a.nup, os.path.abspath(a.work), os.path.abspath(a.out))


if __name__ == "__main__":
    sys.exit(main())
