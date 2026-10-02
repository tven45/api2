/*
 * keccak_pow.c — 23-round Keccak-256 PoW solver for DeepSeekHashV1
 * No AVX2, no SIMD — portable C99, compiles on any x86/ARM Linux
 *
 * Build: gcc -O3 -shared -fPIC -o keccak_pow.so keccak_pow.c
 */
#include <stdint.h>
#include <string.h>
#include <stdio.h>

/* Keccak-f[1600] round constants (all 24, but we skip RC[0]) */
static const uint64_t RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL,
    0x800000000000808aULL, 0x8000000080008000ULL,
    0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL,
    0x000000000000008aULL, 0x0000000000000088ULL,
    0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL,
    0x8000000000008089ULL, 0x8000000000008003ULL,
    0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL,
    0x8000000080008081ULL, 0x8000000000008080ULL,
    0x0000000080000001ULL, 0x8000000080008008ULL
};

static const int ROT[25] = {
     0, 36,  3, 41, 18,
     1, 44, 10, 45,  2,
    62,  6, 43, 15, 61,
    28, 55, 25, 21, 56,
    27, 20, 39,  8, 14
};

static const int PI[25] = {
     0, 10, 20,  5, 15,
     1, 11, 21,  6, 16,
     2, 12, 22,  7, 17,
     3, 13, 23,  8, 18,
     4, 14, 24,  9, 19
};

#define ROL64(x, n) (((x) << (n)) | ((x) >> (64-(n))))

/* Keccak-f[1600] — rounds 1..23 (skip round 0, matching DeepSeekHashV1) */
static void keccak_f_23(uint64_t A[25]) {
    uint64_t B[25], C[5], D[5];
    for (int r = 1; r < 24; r++) {
        /* θ */
        for (int x = 0; x < 5; x++)
            C[x] = A[x]^A[x+5]^A[x+10]^A[x+15]^A[x+20];
        for (int x = 0; x < 5; x++)
            D[x] = C[(x+4)%5] ^ ROL64(C[(x+1)%5], 1);
        for (int i = 0; i < 25; i++) A[i] ^= D[i%5];
        /* ρ + π */
        for (int i = 0; i < 25; i++) B[PI[i]] = ROL64(A[i], ROT[i]);
        /* χ */
        for (int i = 0; i < 25; i++)
            A[i] = B[i] ^ ((~B[(i/5)*5+(i%5+1)%5]) & B[(i/5)*5+(i%5+2)%5]);
        /* ι */
        A[0] ^= RC[r];
    }
}

/* Compute 23-round Keccak-256 */
static void keccak256_23(const uint8_t *in, size_t inlen, uint8_t out[32]) {
    const size_t RATE = 136;   /* 1088-bit rate for 256-bit capacity */
    uint64_t state[25];
    memset(state, 0, sizeof(state));

    /* Absorb full blocks */
    size_t off = 0;
    while (off + RATE <= inlen) {
        uint64_t tmp;
        for (int i = 0; i < (int)(RATE/8); i++) {
            memcpy(&tmp, in + off + i*8, 8);
            state[i] ^= tmp;
        }
        keccak_f_23(state);
        off += RATE;
    }

    /* Final block with Keccak padding (0x06 ... 0x80) */
    uint8_t blk[136];
    memset(blk, 0, RATE);
    size_t rem = inlen - off;
    if (rem) memcpy(blk, in + off, rem);
    blk[rem]      = 0x06;
    blk[RATE - 1] ^= 0x80;

    uint64_t tmp;
    for (int i = 0; i < (int)(RATE/8); i++) {
        memcpy(&tmp, blk + i*8, 8);
        state[i] ^= tmp;
    }
    keccak_f_23(state);

    /* Squeeze first 32 bytes */
    memcpy(out, state, 32);
}

/* Hex string → bytes (challenge is 64 hex chars = 32 bytes) */
static int hex2bytes(const char *hex, uint8_t *bytes, int n) {
    for (int i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(hex + 2*i, "%02x", &v) != 1) return -1;
        bytes[i] = (uint8_t)v;
    }
    return 0;
}

/*
 * solve_pow_c(base, base_len, challenge_hex, difficulty)
 * Returns nonce >= 0 on success, -1 if not found within difficulty.
 * Exported symbol — called from Python via ctypes.
 */
long long solve_pow_c(const char *base, int base_len,
                      const char *challenge_hex, int difficulty) {
    uint8_t target[32];
    if (hex2bytes(challenge_hex, target, 32) != 0) return -1;

    uint8_t input[600];
    uint8_t hash[32];
    char ns[24];

    for (long long nonce = 0; nonce < (long long)difficulty; nonce++) {
        int nlen = snprintf(ns, sizeof(ns), "%lld", nonce);
        memcpy(input, base, (size_t)base_len);
        memcpy(input + base_len, ns, (size_t)nlen);
        keccak256_23(input, (size_t)(base_len + nlen), hash);
        if (memcmp(hash, target, 32) == 0) return nonce;
    }
    return -1;
}
