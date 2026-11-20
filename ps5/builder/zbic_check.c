/* PS5 Longshot Plus Builder: zbic_selftest, built into the ZBIC library each package carries (ps5/builder/package.py).

   The library unpacks the game's main program (nx2/zbic.py). v0.1.1's and v0.1.2's Linux library, compiled by GCC 11 on
   the release's runner, wrote ZBIC frames well and read none: "ZBIC: decompression failed (-2 of 63201744 bytes)" at a
   build's first step, while the same sources compiled by GCC 14 or clang read them. So the library proves itself:
   pipeline.py --check calls this, and the release workflow runs --check on each package before it is published.

   Returns 0 when text made here, compressed to a ZBIC frame and unpacked again comes back the same; 1 when the frame
   could not be made, 2 when it did not unpack, 3 when it unpacked to something else. */
#include <stdlib.h>
#include <string.h>
#include "zstd.h"

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif

API int zbic_selftest(void) {
    enum { SIZE = 1 << 20, WORDS = 300 };
    static const int levels[] = {1, 3, 19};
    unsigned char *data = malloc(SIZE), *back = malloc(SIZE), (*words)[8] = malloc(WORDS * 8);
    const size_t cap = ZSTD_compressBound(SIZE);
    unsigned char *packed = malloc(cap);
    unsigned seed = 1;
    int result = 0;
    if (!data || !back || !words || !packed) return 1;
    /* (words of 2 to 8 bytes drawn again and again: literals, matches and sequences, so every entropy table is written) */
#define NEXT() (seed = seed * 1103515245u + 12345u, (seed >> 16) & 0x7fff)
    for (int w = 0; w < WORDS; w++)
        for (int k = 0; k < 8; k++) words[w][k] = (unsigned char)NEXT();
    for (size_t at = 0; at < SIZE;) {
        const unsigned w = NEXT() % WORDS, n = 2 + NEXT() % 7;
        for (unsigned k = 0; k < n && at < SIZE; k++) data[at++] = words[w][k];
    }
    for (size_t i = 0; i < sizeof levels / sizeof *levels && !result; i++) {
        const size_t n = ZSTD_compress(packed, cap, data, SIZE, levels[i]);
        if (ZSTD_isError(n) || memcmp(packed, "ZBIC", 4)) { result = 1; break; }
        memset(back, 0, SIZE);
        const size_t r = ZSTD_decompress(back, SIZE, packed, n);
        if (ZSTD_isError(r) || r != SIZE) result = 2;
        else if (memcmp(back, data, SIZE)) result = 3;
    }
    free(data), free(back), free(words), free(packed);
    return result;
}
