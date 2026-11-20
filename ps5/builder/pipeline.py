"""PS5 Longshot Plus Builder: the title folder made from a game file and the kit, on Linux, macOS or Windows.

    python pipeline.py --game <game.nup | folder with exefs/> --out <folder> [--kit <kit>] [--work <folder>] [--jobs N]

The kit (ps5/builder/make_kit.py) holds everything of the title that is not made from the game, compiled already. This
adds the game's part, on this PC and from the player's own copy:

  1. the game's program is read out of the game file (its exefs: about 50 MB, not the game's 25 GB);
  2. it is translated to C (the kit's lifter, nx2/lift.py);
  3. the C is compiled for the console (clang);
  4. it is linked with the kit's objects and libraries (ld.lld) and made the console's executable (ps5-native-tool);
  5. the title folder is laid out: <out>/<title id>, to copy to /data/homebrew on the console.

Nothing made here may be shared: eboot.bin and image.bin are made from the game.

The tools: clang, ld.lld, llvm-nm and ps5-native-tool, looked for in --tools, in LONGSHOT_PS5_TOOLS, in a tools/bin folder
beside the kit, then on PATH. Lines starting with "@@" are for the window (ps5/builder/app.py): "@@step <n> <of> <name>",
"@@progress <done> <total>", "@@done <folder>", "@@error <text>".
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = ".exe" if os.name == "nt" else ""
# The console the title is made for. attribute3 (sce_sys/param.json) is read by the console's kernel at each start: the Pro's
# value asks for its High CPU Frequency mode and its larger memory (13,232 MiB of direct memory); the base value starts the
# title as a base-generation process (12,016 MiB), on a Pro too, where it was measured: Kokiri Forest plays with 1.9 GB free
# at the least, against the Pro mode's 3.2 GB. With that much less room the base profile keeps PS5_Hookshot's 512-texel
# textures (the Pro's 1024 take 135 MB more in the forest). The switches go in longshot_env.txt in the title folder, which
# the title reads at its start (host/hos_main.c); a line the player adds there afterwards is theirs.
MODELS = {
    "base": {
        "name": "PS5 / PS5 Slim",
        "attribute3": 0x140040,
        "env": {
            "RX_VK_COMPILE_SHADERS": "most",
            "RX_VK_TEX_MAX_DIM": "256",
            "RX_VK_TEX_SKIP_MB": "1",
            "RX_VK_WB_MAX_KB": "1024",
        },
    },
    "pro": {
        "name": "PS5 Pro",
        "attribute3": 0x8540040,
        "env": {
            "RX_VK_COMPILE_SHADERS": "most",
            "RX_VK_WB_MAX_KB": "1024",
        },
    },
}
STEPS = ["Reading the game's program", "Translating it to C", "Compiling for the console", "Linking", "Making the title folder",
         "Listing the game's shaders"]


class Stop(Exception):
    pass


def say(text):
    print(text, flush=True)


def step(n):
    say("@@step %d %d %s" % (n, len(STEPS), STEPS[n - 1]))


def find_kit(given):
    for d in (given, os.environ.get("LONGSHOT_PS5_KIT"), os.path.join(HERE, "kit"), os.path.join(HERE, "..", "kit"),
              os.path.join(HERE, "..", "..", "dist", "kit")):
        if d and os.path.exists(os.path.join(d, "kit.json")):
            return os.path.abspath(d)
    raise Stop("the kit was not found (kit.json): give its folder with --kit")


def find_tool(name, kit, given):
    dirs = [given, os.environ.get("LONGSHOT_PS5_TOOLS"), os.path.join(kit, "..", "tools", "bin"), os.path.join(kit, "tools", "bin")]
    for d in dirs:
        if d and os.path.exists(os.path.join(d, name + EXE)):
            return os.path.abspath(os.path.join(d, name + EXE))
    found = shutil.which(name)
    if found:
        return found
    raise Stop("%s was not found: it comes with the builder (tools/bin); --tools names another folder" % name)


def available_gb():
    try:
        if os.name == "nt":
            import ctypes

            class M(ctypes.Structure):
                _fields_ = [("n", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(k, ctypes.c_ulonglong) for k in
                                                                               ("total", "avail", "a", "b", "c", "d", "e")]
            m = M()
            m.n = ctypes.sizeof(M)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.avail / 2**30
        if sys.platform == "darwin":
            pages = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0)
            return pages / 2**30 / 2   # (no "available" figure there: half of what the Mac has)
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except Exception:
        pass
    return None


def default_jobs():
    """Compiles at once: the cores less one, and no more than one for each 1.6 GB of memory free (a chunk's compile
    took up to 1.4 GB)"""
    n = max(1, (os.cpu_count() or 2) - 1)
    gb = available_gb()
    if gb:
        n = max(1, min(n, int(gb / 1.6)))
    return n


def run(cmd, env=None, cwd=None, what=None):
    """the command's output goes on as it comes; a failure stops the build with its last lines"""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=env, cwd=cwd)
    tail = []
    for line in p.stdout:
        line = line.rstrip("\n")
        tail = (tail + [line])[-25:]
        say(line)
    if p.wait():
        raise Stop("%s failed (exit %d):\n%s" % (what or os.path.basename(cmd[0]), p.returncode, "\n".join(tail[-12:])))


def python_env(kit, work):
    src = os.path.join(kit, "src")
    env = dict(os.environ, OOT_WORK=os.path.join(work, "lift"), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    env.pop("PYTHONPATH", None)
    return src, env


def read_game(kit, work, game):
    step(1)
    exefs = os.path.join(work, "game", "exefs")
    if os.path.isdir(game):
        for d in (game, os.path.join(game, "exefs")):
            if os.path.exists(os.path.join(d, "main")):
                return d
        raise Stop("no exefs (a file named main) in %s" % game)
    if not os.path.isfile(game):
        raise Stop("the game file was not found: %s" % game)
    stamp = "%d %d" % (os.path.getsize(game), int(os.path.getmtime(game)))
    mark = os.path.join(work, "game", ".from")
    if os.path.exists(os.path.join(exefs, "main")) and os.path.exists(mark) and open(mark).read() == stamp:
        say("read already, from this file")
        return exefs
    src, env = python_env(kit, work)
    shutil.rmtree(os.path.join(work, "game"), ignore_errors=True)
    os.makedirs(os.path.join(work, "game"))
    run([sys.executable, "-I", "-u", os.path.join(src, "nx2", "nup.py"), game, "--only", "exefs", "--extract",
         os.path.join(work, "game")], env=env, what="reading the game file")
    if not os.path.exists(os.path.join(exefs, "main")):
        raise Stop("the game file gave no program (exefs/main): is it the supported game and version?")
    open(mark, "w").write(stamp)
    return exefs


def tree_key(*dirs):
    h = hashlib.sha1()
    for d in dirs:
        for base, _, files in sorted(os.walk(d)):
            for f in sorted(files):
                p = os.path.join(base, f)
                h.update(os.path.relpath(p, d).encode() + b"%d" % os.path.getsize(p))
    return h.hexdigest()


def lift(kit, work, exefs, lift_jobs):
    step(2)
    src, env = python_env(kit, work)
    gen = os.path.join(work, "lift", "sa", "gen")
    with open(os.path.join(exefs, "main"), "rb") as f:
        key = hashlib.sha1(f.read()).hexdigest() + " " + json.load(open(os.path.join(kit, "kit.json")))["revisions"]["PS5_Longshot"]
    mark = os.path.join(work, "lift", ".done")
    if os.path.exists(mark) and open(mark).read() == key and os.path.exists(os.path.join(gen, "a64_table.c")):
        say("translated already, from this program with this kit")
        return gen
    if os.path.exists(mark):
        os.remove(mark)
    os.makedirs(os.path.join(work, "lift", "image"), exist_ok=True)
    if lift_jobs:
        env["LONGSHOT_LIFT_JOBS"] = str(lift_jobs)
    run([sys.executable, "-I", "-u", os.path.join(src, "nx2", "lift.py"), "--standalone", "--write", "--exefs", exefs,
         "--work", os.path.join(work, "lift")], env=env, what="the translation")
    if not os.path.exists(os.path.join(gen, "a64_table.c")) or not os.path.exists(os.path.join(work, "lift", "sa", "image", "image.bin")):
        raise Stop("the translation ended without its files: if it was stopped for want of memory, close other programs "
                   "and build again")
    open(mark, "w").write(key)
    return gen


def compile_game(kit, info, work, gen, clang, jobs):
    step(3)
    src, env = python_env(kit, work)
    rt64 = os.path.join(work, "rt64")
    # (the 64-bit guest window's runtime header and a64_rt.c: nx2/rtgen.py, as ps5/build.py makes them)
    run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import rtgen; rtgen.write(sys.argv[2])",
         os.path.join(src, "nx2"), rt64], env=env, what="the runtime header")
    place = lambda a: a.replace("{kit}", kit).replace("{rt64}", rt64).replace("{gen}", gen)
    flags = [place(a) for a in info["target_flags"] + info["game_flags"]]
    chunks = sorted(f for f in os.listdir(gen) if f.startswith("code_") and f.endswith(".c"))
    sources = [os.path.join(gen, c) for c in chunks] + [os.path.join(gen, f) for f in info["game_files"]] + \
        [os.path.join(rt64, "a64_rt.c")]
    obj_dir = os.path.join(work, "obj")
    os.makedirs(obj_dir, exist_ok=True)
    version = subprocess.run([clang, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    say("compiler: %s; %d files, %d at once" % (version, len(sources), jobs))
    heads = hashlib.sha1()
    for d in (rt64, os.path.join(kit, "src", "third_party", "a64-kit", "rt"), os.path.join(kit, "src", "nx2", "rt"), gen):
        for f in sorted(os.listdir(d)):
            if f.endswith(".h"):
                heads.update(f.encode() + open(os.path.join(d, f), "rb").read())
    base = hashlib.sha1((" ".join(info["target_flags"] + info["game_flags"]) + version).encode() + heads.digest()).hexdigest()

    def one(s):
        obj = os.path.join(obj_dir, "game_" + os.path.basename(s)[:-2] + ".o")
        h = hashlib.sha1(base.encode())
        with open(s, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        key, stamp = h.hexdigest(), obj + ".key"
        if os.path.exists(obj) and os.path.exists(stamp) and open(stamp).read() == key:
            return obj, None
        r = subprocess.run([clang] + flags + ["-c", s, "-o", obj], capture_output=True, text=True, errors="replace")
        if r.returncode:
            return obj, "%s: exit %d\n%s" % (os.path.basename(s), r.returncode, (r.stdout + r.stderr)[-3000:])
        open(stamp, "w").write(key)
        return obj, None

    sources.sort(key=lambda s: -os.path.getsize(s))   # (the longest first, so none is left alone at the end)
    objects, failed, done, t0 = [], [], 0, time.time()
    say("@@progress 0 %d" % len(sources))
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for obj, err in pool.map(one, sources):
            done += 1
            objects.append(obj)
            if err:
                failed.append(err)
                say(err)
            say("@@progress %d %d" % (done, len(sources)))
    if failed:
        hint = " A compiler stopped by the system (exit -9 or 137) wants fewer at once: --jobs." if any("exit -9" in e or "exit 137" in e for e in failed) else ""
        raise Stop("%d of %d files did not compile.%s" % (len(failed), len(sources), hint))
    say("compiled in %.0f s" % (time.time() - t0))
    return objects


def rsp(path, args):
    """a response file: one argument a line, quoted for lld"""
    with open(path, "w", encoding="utf-8") as f:
        for a in args:
            f.write('"%s"\n' % a.replace("\\", "/").replace('"', '\\"'))
    return "@" + path


def link(kit, info, work, objects, lld, nm):
    step(4)
    out = os.path.join(work, "link")
    os.makedirs(out, exist_ok=True)
    place = lambda a: a.replace("{kit}", kit)
    lk = info["link"]
    # (the libc calls host/ps5 defines because no module a title loads exports them: kept inside the executable)
    with open(os.path.join(out, "local.map"), "w") as f:
        f.write("{\n    local:\n" + "".join("        %s;\n" % n for n in info.get("local_symbols", [
            "fnmatch", "isatty", "pthread_setname_np", "timegm", "syscall", "pipe2", "accept4", "posix_fadvise", "getpwuid",
            "umask"])) + "};\n")
    elf = os.path.join(out, "llvm-pie.elf")
    stubs = [os.path.join(kit, "lib", "stubs", n) for n in ("libSceAgc.so", "libSceAgcDriver.so")]
    sdk_lib = os.path.join(kit, "sdk", "target", "lib")
    args = (["-m", "elf_x86_64", "-pie", "--eh-frame-hdr", "-z", "max-page-size=0x4000", "-mllvm", "-emulated-tls"] +
            [place(a) for a in lk["script"]] + [place(a) for a in lk["flags"]] +
            ["--version-script", os.path.join(kit, "link", "native", "app-symbols.map"),
             "--version-script", os.path.join(out, "local.map"), "--exclude-libs=ALL", "--gc-sections", "-e", "_start",
             "-Map=" + os.path.join(out, "longshot.map"), "-o", elf, os.path.join(kit, "obj", "app_crt.o")] +
            [os.path.join(kit, "obj", n) for n in info["objects"]] + sorted(objects) +
            [os.path.join(kit, "lib", n) for n in ("libavcodec.a", "libswscale.a", "libavutil.a", "libdav1d.a", "libzstd.a")] +
            stubs + [place(a) for a in lk["inputs"]] + ["--as-needed"] +
            sorted(os.path.join(sdk_lib, f) for f in os.listdir(sdk_lib) if f.endswith(".so")) + ["--hash-style=gnu"])
    t0 = time.time()
    run([lld, rsp(os.path.join(out, "link.rsp"), args)], what="the link")
    exported = set(open(os.path.join(kit, "link", "exported.txt")).read().split())

    def undefined(kinds):
        r = subprocess.run([nm, "-D", "--undefined-only", elf], capture_output=True, text=True, check=True).stdout
        return sorted({l.split()[1].split("@")[0] for l in r.splitlines() if len(l.split()) == 2 and l.split()[0] in kinds})
    # (a weak reference nothing defines reads as null: bound to 0 here, or the console's loader takes it for an import)
    weak = [s for s in undefined(("w", "v")) if s not in exported]
    if weak:
        run([lld, rsp(os.path.join(out, "link.rsp"), args + ["--defsym=%s=0" % s for s in weak])], what="the link")
    null = [s for s in undefined(("U",)) if s not in exported]
    if null:
        raise Stop("the link has imports that no module a title loads exports (null at run time): " + " ".join(null[:20]))
    say("linked: %d MB (%.0f s)" % (os.path.getsize(elf) >> 20, time.time() - t0))
    return elf


def title(kit, info, work, elf, native, out, model):
    step(5)
    app = os.path.join(out, info["title_id"])
    if os.path.exists(app):
        for keep in ("eboot.bin", "image.bin"):   # (a folder built before: its executable and image are replaced)
            if os.path.exists(os.path.join(app, keep)):
                os.remove(os.path.join(app, keep))
    os.makedirs(app, exist_ok=True)
    stub_dir = os.path.join(kit, "sdk", "target", "lib")
    new = os.path.join(work, "eboot.elf.new")
    run([native, "link", "--in", elf, "--out", new, "--stub-dir", stub_dir,
         "--stub", os.path.join(kit, "lib", "stubs", "libSceAgc.so"), "--stub", os.path.join(kit, "lib", "stubs", "libSceAgcDriver.so"),
         "--module-sdk", "0x02000009", "--companion-sdk", "0x08050001", "--file-name", "eboot.elf"], what="the console's executable")
    run([native, "self", "--sign", "--in", new, "--out", os.path.join(app, "eboot.bin"), "--magic", "0x1D3D154F"], what="signing")
    run([native, "self", "--inspect", "--file", os.path.join(app, "eboot.bin")], what="the check of eboot.bin")
    shutil.copytree(os.path.join(kit, "title"), app, dirs_exist_ok=True)
    shutil.copy2(os.path.join(work, "lift", "sa", "image", "image.bin"), os.path.join(app, "image.bin"))
    # ---- the console's profile ----
    profile = MODELS[model]
    param = os.path.join(app, "sce_sys", "param.json")
    j = json.load(open(param, encoding="utf-8"))
    j["attribute3"] = profile["attribute3"]
    with open(param, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(j, indent=2) + "\n")
    with open(os.path.join(app, "longshot_env.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# Longshot on the PS5: the switches of the profile this folder was built for (%s).\n"
                "# NAME=VALUE lines; a line starting with # is a note.\n" % profile["name"])
        f.write("".join("%s=%s\n" % kv for kv in sorted(profile["env"].items())))
    say("profile: %s (attribute3 %#x%s)" % (profile["name"], profile["attribute3"],
                                           "".join("; %s=%s" % kv for kv in sorted(profile["env"].items()))))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(app) for f in fs)
    say("title: %s (%d MB)" % (app, size >> 20))
    return app


def check(a):
    """--check: the builder's own parts on this PC, with no game: the kit, the tools, the lifter's packages and its helper
    library, and one small file compiled for the console. What the release workflow runs on each system's package."""
    kit = find_kit(a.kit)
    info = json.load(open(os.path.join(kit, "kit.json")))
    say("kit: %s (%s), %d objects" % (info["longshot"], info["title_id"], len(info["objects"])))
    missing = [n for n in info["objects"] if not os.path.exists(os.path.join(kit, "obj", n))]
    if missing:
        raise Stop("the kit lacks %d of its objects (%s)" % (len(missing), missing[0]))
    clang, lld, nm, native = (find_tool(n, kit, a.tools) for n in ("clang", "ld.lld", "llvm-nm", "ps5-native-tool"))
    for tool, args in ((clang, ["--version"]), (lld, ["--version"]), (nm, ["--version"])):
        r = subprocess.run([tool] + args, capture_output=True, text=True)
        if r.returncode:
            raise Stop("%s does not run (exit %d): %s" % (tool, r.returncode, (r.stdout + r.stderr)[-300:]))
        say("%s: %s" % (os.path.basename(tool), (r.stdout + r.stderr).splitlines()[0]))
    r = subprocess.run([native], capture_output=True, text=True)
    if "ps5-native-tool" not in r.stdout + r.stderr:
        raise Stop("%s does not run: %s" % (native, (r.stdout + r.stderr)[-300:]))
    say("ps5-native-tool: runs")
    src, env = python_env(kit, a.work or ".")
    code = ("import sys, os; k = sys.argv[1]; sys.path[:0] = [os.path.join(k, 'nx2'), os.path.join(k, 'third_party', 'a64-kit', 'lift'), "
            "os.path.join(k, 'third_party', 'a64-kit', 'tools')]\n"
            "import numpy, capstone, lz4, zstandard, unicorn\n"
            "import a64emit\n"
            "text = a64emit.translate([(0x10000, 0x91000420)])[0][0]\n"
            "assert 'c->x[0]' in text, text\n"
            "print('lifter: its packages and its helper library load; ADD X0, X1, #1 ->', text.strip())\n"
            # (the game file's reader, as a build starts it: its imports, and the library a ZBIC-compressed main is unpacked
            # with, which must be there ready: a player has no compiler to build it with)
            "import nup, unpack, zbic\n"
            "assert os.path.exists(zbic.DLL), 'the ZBIC library is not in the kit: ' + zbic.DLL\n"
            "assert zbic.lib().zbic_content_size(b'ZBIC', 4) < 0\n"
            # (and reads what it writes: ps5/builder/zbic_check.c. A library that did not stopped every Linux build of v0.1.1)
            "r = zbic.lib().zbic_selftest()\n"
            "assert r == 0, 'the ZBIC library does not unpack its own frames (zbic_selftest %d): ' % r + zbic.DLL\n"
            "print('game file reader: loads, and its ZBIC library unpacks what it packs')\n")
    run([sys.executable, "-I", "-c", code, src], env=env, what="the lifter's parts")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "check.c")
        with open(c, "w") as f:
            f.write("#include <stdint.h>\n#include <string.h>\nuint64_t check(const char *s) { return strlen(s) + UINT64_C(1); }\n")
        place = lambda x: x.replace("{kit}", kit)
        flags = [place(x) for x in info["target_flags"]] + ["-march=znver2", "-O1"]
        r = subprocess.run([clang] + flags + ["-c", c, "-o", os.path.join(tmp, "check.o")], capture_output=True, text=True)
        if r.returncode:
            raise Stop("clang did not compile for the console: %s" % (r.stdout + r.stderr)[-600:])
        out = subprocess.run([nm, os.path.join(tmp, "check.o")], capture_output=True, text=True).stdout
        if " T check" not in out:
            raise Stop("the compiled check has no function: %s" % out)
    say("clang: compiles for the console (x86_64-sie-ps5, the kit's SDK)")
    say("@@done check")


def shader_lists(kit, work, game, app):
    """(ps5/builder/shader_lists.py) the game's shader list and the pipeline state list in the title folder: the console's first
    start builds the pipelines before play, in a launch of its own, so play does not compile them (each compile leaves memory
    the base console runs out of, issues #4 and #24). Made from the .nup alone: a folder given instead has no romfs to read"""
    step(6)
    if not os.path.isfile(game):
        say("note: no .nup given, so no shader lists: the game compiles its shaders as it plays")
        return
    src, env = python_env(kit, work)
    run([sys.executable, "-I", "-u", os.path.join(HERE, "shader_lists.py"), "--src", src, "--nup", game, "--work", work,
         "--out", app], env=env, what="listing the game's shaders")


def build(a):
    kit = find_kit(a.kit)
    info = json.load(open(os.path.join(kit, "kit.json")))
    clang, lld, nm, native = (find_tool(n, kit, a.tools) for n in ("clang", "ld.lld", "llvm-nm", "ps5-native-tool"))
    out = os.path.abspath(a.out)
    work = os.path.abspath(a.work or os.path.join(out, "longshot-ps5-work"))
    os.makedirs(work, exist_ok=True)
    free = shutil.disk_usage(work).free / 2**30
    say("PS5 Longshot Plus Builder: kit %s (%s); work folder %s (%.0f GB free)" % (info["longshot"], info["title_id"], work, free))
    if free < 6:
        raise Stop("the work folder's disk has %.1f GB free: the build wants 6 GB" % free)
    gb = available_gb()
    if gb is not None and gb < 8:
        say("note: %.1f GB of memory is free; the translation alone holds about 6 GB. Close other programs if it stops." % gb)
    exefs = read_game(kit, work, a.game)
    gen = lift(kit, work, exefs, a.lift_jobs)
    objects = compile_game(kit, info, work, gen, clang, a.jobs or default_jobs())
    elf = link(kit, info, work, objects, lld, nm)
    app = title(kit, info, work, elf, native, out, a.model)
    shader_lists(kit, work, a.game, app)
    say("@@done " + app)
    return app


def main():
    ap = argparse.ArgumentParser(description="PS5 Longshot Plus Builder: the PS5 title folder from your own game file")
    ap.add_argument("--check", action="store_true", help="check the builder's own parts on this PC (no game is needed), and stop")
    ap.add_argument("--game", help="the game: a .nup file, or a folder with its exefs")
    ap.add_argument("--out", help="where the title folder goes")
    ap.add_argument("--model", choices=sorted(MODELS),
                    help="the console the folder is for: base (PS5 and PS5 Slim) or pro (PS5 Pro)")
    ap.add_argument("--kit")
    ap.add_argument("--tools")
    ap.add_argument("--work", help="the build's own files (default: <out>/longshot-ps5-work); kept, so a second build is short")
    ap.add_argument("--jobs", type=int, default=0, help="compiles at once (default: by the cores and the memory free)")
    ap.add_argument("--lift-jobs", type=int, default=0, help="the translation's processes (default: the lifter's own rule)")
    a = ap.parse_args()
    if not a.check and not (a.game and a.out and a.model):
        ap.error("--game, --out and --model are needed (or --check)")
    try:
        check(a) if a.check else build(a)
    except Stop as e:
        say("@@error " + str(e).replace("\n", " | "))
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
