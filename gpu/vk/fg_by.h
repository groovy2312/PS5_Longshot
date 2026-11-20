// (PS5) The Frame generation by row on the console: which of its generators makes a set's frames. Nothing of Vulkan here:
// the tests/test_ps5_framegen_*.py compile it alone.
#pragma once

namespace mkwvk::fg_by {

// (the row's values, the options file's frame_generation_by. 0 and 1 are the PC's two. The console adds its own and its
// row has no Auto: beside the Frame generation row's a player read the two as one. A file's 0 is kDefault there)
enum : int { kAuto = 0, kBuiltIn = 1, kFsr3 = 2, kMihawk = 3, kLossless = 4 };
#ifdef __PROSPERO__
inline constexpr int kLeast = kBuiltIn, kMost = kLossless, kDefault = kBuiltIn;
#else
inline constexpr int kLeast = kAuto, kMost = kBuiltIn, kDefault = kAuto;
#endif
// (2 is FSR 3.1 FG's on this row in upstream Longshot since its 983d607b (2026-11-17), which this tree does not have yet.
// The console's own values come after it, so a file written now means the same once the port has upstream's row. Until
// then nothing makes frames at 2: the console's row steps over it, and a 2 from a file or a variable is kDefault)
/// The row's value a step from its neighbour lands on: `by` itself, or the next one that way when it is kFsr3
constexpr int step_over(int by, int dir) { return by == kFsr3 ? by + (dir > 0 ? 1 : -1) : by; }
/// A number from a file or a variable, as a value of this build's row
constexpr int known(int by) { return by == kFsr3 ? kDefault : by; }

constexpr const char* name(int by) {
    return by == kBuiltIn ? "Built-in" : by == kMihawk ? "MihawkGen" : by == kLossless ? "Lossless Scaling" : "Auto";
}

/// Lossless Scaling's chain is asked for: at its own value only (it needs the player's own Lossless.dll)
constexpr bool wants_lossless(int by) { return by == kLossless; }

/// PS5_MihawkGen's network is asked for: at Auto and at its own value, and at Lossless Scaling's while that chain is not
/// there (no Lossless.dll, or it was not made), so a player without the file still has the network's frames.
/// Never both at once: each holds its own images, and the console's memory is the game's first
constexpr bool wants_network(int by, bool lossless_there) {
    return by == kAuto || by == kMihawk || (by == kLossless && !lossless_there);
}

/// Frame generation by the memory left on the console, three steps: 0 the row's generator, 1 the renderer's own (it holds
/// no images of its own: the light one), 2 none (its frames' images are let go). level: as it is; room: the console's free
/// memory; line: what is kept for the game (the textures' reserve). Down to 1 under the line and to 2 under half of it;
/// up from 2 at the line and from 1 at twice the line, a step at a time: what a step up takes again (the frames' images,
/// about a quarter of the line at 3840x2160; a generator's, less) leaves it above the line it would go down at.
/// Up from none at one and a half times the line. A step up is taken only once it was asked kUpHold times running
/// (level_held): in Hyrule Castle the free memory went between 460 and 1130 MiB within seconds as the driver's buffers
/// came and went, and the frames went off and on with it, a hitch each time
inline constexpr int kUpHold = 10;
/// level_next with the wait for a step up: *ups counts the askings in a row that wanted one
constexpr int level_next(int level, unsigned long long room, unsigned long long line);
constexpr int level_held(int level, unsigned long long room, unsigned long long line, int* ups) {
    const int next = level_next(level, room, line);
    if (next >= level) return *ups = 0, next;   // (down, or as it is: at once)
    return ++*ups >= kUpHold ? (*ups = 0, next) : level;
}
constexpr int level_next(int level, unsigned long long room, unsigned long long line) {
    if (room < line / 2) return 2;
    if (level >= 2) return room >= line + line / 2 ? 1 : 2;
    if (level == 1) return room >= line * 2 ? 0 : 1;
    return room < line ? 1 : 0;
}

}   // namespace mkwvk::fg_by
