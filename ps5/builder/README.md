# Longshot PS5 Builder

A desktop tool: a player chooses their own game file and gets the PS5 title folder (`PPSA99198`) to copy to
`/data/homebrew` on the console. Nothing has to be installed: the package carries its own Python, compiler and kit.

| File | What it is |
|---|---|
| `build_kit.sh` | The kit from a fresh clone on Linux, with no game: fetches and builds RADV, FFmpeg, the SDK and the rest at pinned revisions, compiles the runtime and the renderer (`ps5/build.py --kit`), runs `make_kit.py`. |
| `make_kit.py` | Lays the kit out in `dist/kit`: everything of the title that is not made from the game, compiled already. No game content. |
| `pipeline.py` | The build: the game's program read out of the `.nup`, translated to C, compiled, linked with the kit, signed, laid out. Python only, the same on every system. `--game --out --model base|pro [--jobs]`; `--check` checks the builder's own parts with no game. |
| `app.py` | The window (PyQt6): game file, output folder, the console's model, Build, the five steps, a progress bar, the log, Stop, and Send to PS5 over FTP. Runs `pipeline.py` as a process. `--self-test` opens it and runs the check. |
| `package.py` | The packages, both made on Linux: `--system linux-x64` an AppImage, `--system windows-x64` a portable folder in a zip (cross-built). python-build-standalone, llvm-mingw's LLVM (its clang has the PS5 target), `ps5-native-tool` and the lifter's helper library for the system, the kit. Every download is checked by SHA-256. |
| `launcher.c` | Windows: `LongshotPS5Builder.exe`, the portable folder's start. No installer. |

```
bash ps5/builder/build_kit.sh                                  # the kit (30 to 60 minutes the first time)
python3 -I ps5/builder/package.py --system linux-x64           # dist/builder/LongshotPS5Builder-<version>-x86_64.AppImage
python3 -I ps5/builder/package.py --system windows-x64         # dist/builder/LongshotPS5Builder-<version>-windows-x64.zip
```

## Releases

`.github/workflows/builder-release.yml` runs when a release is **published** on GitHub: it builds the kit
(ubuntu-24.04), makes both packages (ubuntu-22.04), starts each on its own system (`--check` and the window's
`--self-test` on ubuntu-22.04 and windows-latest), and only then attaches the four files to the release:

    LongshotPS5Builder-<tag>-x86_64.AppImage        (+ .sha256)
    LongshotPS5Builder-<tag>-windows-x64.zip        (+ .sha256)

To cut one: `gh release create ps5-v0.1.0 --title "..." --notes "..."` (or the Releases page). The tag names the
files. "Run workflow" on the Actions page does everything but the upload and keeps the packages as the run's
artifacts: a rehearsal. No game file is used anywhere in it.

The Windows package is a folder: unpack the zip anywhere and start `LongshotPS5Builder.exe`. It is not one file: the
compiler, Python and the kit are 880 MB unpacked, and a single self-unpacking program would unpack them at every start.

## The console's profile

The folder is built for one model (`pipeline.py` `MODELS`), chosen in the window or with `--model`:

| | `attribute3` in `param.json` | `longshot_env.txt` |
|---|---|---|
| PS5 Pro (`pro`) | `0x8540040`: High CPU Frequency, 13,232 MiB | nothing: the title's own defaults (textures of at most 1024 texels) |
| PS5 / PS5 Slim (`base`) | `0x140040`: a base-generation process, 12,016 MiB | `RX_VK_TEX_MAX_DIM=512` |

The base profile runs on a base PS5. Its memory was measured on a Pro, which runs the title in the base console's mode
with that value (ps5/README.md). The base folder, sent to the Pro, started as a base-generation process (12,016 MiB) and played the
forest test for its 230 s with 2.0 GB free at the least and no fault. A start as a player has it (no test switches)
shows the title reading the folder's `longshot_env.txt`; the 512-texel line's effect in play was not measured in that
start (a test run's own switches file takes the place of `longshot_env.txt`, and a player's file may not script input).

## State (2026-11-16)

- **The kit from a fresh clone** (`build_kit.sh`, no game) has the same runtime objects as a whole build's, but for the
  version stamp. An AppImage made from it built a title from the game's `exefs`; that title has not run on the console
  yet (the earlier packages' titles did: Kokiri Forest at 32 to 34 fps, Pro and base profile).
- **Linux**: the AppImage (278 MB) passes `--check` and `--self-test` here, runs a whole build from the read-only image
  with nothing installed, and its lifter needs no compiler.
- **Windows**: the zip (279 MB) is cross-built here. Under Wine its Python, compiler, linker, signing tool and helper
  library run, one game file compiles to the same object as on Linux, and a link and signing from the same objects gives
  the same `eboot.bin` byte for byte. Wine cannot load numpy or Qt (its own gaps), so the lifter and the window have
  **not** run on Windows: the workflow's `check-windows` job is their first real run.
- **The workflow ran on GitHub** on 2026-11-16: a rehearsal, then the release **v0.1.0**, every job passing, the Windows
  check on windows-latest among them (the lifter's parts and the window started there for the first time). The
  published AppImage, downloaded again, matches its checksum and passes `--check`. The released kit was compiled on the
  runner (clang 18): a title built with it has not run on a console, and a whole build has not run on a Windows PC.
- **Reading a `.nup`**: v0.1.0 could not. The game's main program is ZBIC-compressed in a real dump, and the library
  that unpacks it (`third_party/zbic`) was built on first use, by a compiler a player does not have: the build stopped
  at its first step, on Windows and on Linux. Every test until then had given the folder of program files unpacked
  earlier. Since v0.1.1 the library comes ready in each package and `--check` asks for it; the real game file's program,
  read from the console's copy by the packaged Linux and Windows builders (the Windows one under Wine), comes out the
  same as the files every build was made from.
- **v0.1.1 and v0.1.2 on Linux could not unpack the game's program**: "ZBIC: decompression failed (-2 of 63201744
  bytes)". Their `zbic.so` was compiled by the release runner's GCC 11, and that build wrote ZBIC frames and read none
  (the same sources by GCC 14 or clang read them; the Windows library, compiled by the pinned LLVM, was right). The fix
  for v0.1.0 had been tried with a library made on this PC, not with the published AppImage. Since v0.1.3 both systems'
  library is compiled by the pinned LLVM and has a self-test (`zbic_check.c`) that `--check` runs, so the release's own
  check jobs fail on a library that does not read what it writes. The other helper, `liba64emit.so`, was compared on
  400,000 of the game's instructions: the runner's GCC 11 build and this PC's give the same C.
- **macOS**: not packaged.
