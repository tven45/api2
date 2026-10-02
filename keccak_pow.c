/*
 * keccak_pow.c — 23-round Keccak-256 PoW solver for DeepSeekHashV1
 * Uses verified sequential rho+pi from tiny_sha3 reference
 * No AVX2, no SIMD — portable C99
 *
 * Build: gcc -O3 -shared -fPIC -o keccak_pow.so keccak_pow.c
 */
#include <stdint.h>
#include <string.h>
#include <stdio.h>

/* RC constants for all 24 rounds (we use indices 1..23, skip 0) */
static const uint64_t RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL,
    0x800000000000808AULL, 0x8000000080008000ULL,
    0x000000000000808BULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL,
    0x000000000000008AULL, 0x0000000000000088ULL,
    0x0000000080008009ULL, 0x000000008000000AULL,
    0x000000008000808BULL, 0x800000000000008BULL,
    0x8000000000008089ULL, 0x8000000000008003ULL,
    0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800AULL, 0x800000008000000AULL,
    0x8000000080008081ULL, 0x8000000000008080ULL,
    0x0000000080000001ULL, 0x8000000080008008ULL
};

/* Verified from tiny_sha3 reference implementation */
static const int ROTC[24] = {
     1,  3,  6, 10, 15, 21, 28, 36,
    45, 55,  2, 14, 27, 41, 56,  8,
    25, 43, 62, 18, 39, 61, 20, 44
};
static const int PILN[24] = {
    10,  7, 11, 17, 18,  3,  5, 16,
     8, 21, 24,  4, 15, 23, 19, 13,
    12,  2, 20, 14, 22,  9,  6,  1
};

#define ROL64(x, n) (((x) << (n)) | ((x) >> (64-(n))))

/* Keccak-f[1600] with 23 rounds (1..23), state is A[y*5+x] flat */
static void keccak_f_23(uint64_t A[25]) {
    uint64_t C[5], D[5], t, bc;

    for (int r = 1; r < 24; r++) {
        /* θ (theta) */
        for (int x = 0; x < 5; x++)
            C[x] = A[x] ^ A[x+5] ^ A[x+10] ^ A[x+15] ^ A[x+20];
        for (int x = 0; x < 5; x++) {
            D[x] = C[(x+4)%5] ^ ROL64(C[(x+1)%5], 1);
            for (int y = 0; y < 25; y += 5)
                A[y + x] ^= D[x];
        }
        /* ρ + π (sequential, verified against tiny_sha3) */
        t = A[1];
        for (int i = 0; i < 24; i++) {
            int j = PILN[i];
            bc = A[j];
            A[j] = ROL64(t, ROTC[i]);
            t = bc;
        }
        /* χ (chi) */
        for (int y = 0; y < 25; y += 5) {
            for (int x = 0; x < 5; x++) C[x] = A[y + x];
            for (int x = 0; x < 5; x++)
                A[y+x] = C[x] ^ ((~C[(x+1)%5]) & C[(x+2)%5]);
        }
        /* ι (iota) */
        A[0] ^= RC[r];
    }
}

/* 23-round Keccak-256 with SHA3-style 0x06 padding */
static void keccak256_23(const uint8_t *in, size_t inlen, uint8_t out[32]) {
    const size_t RATE = 136;
    uint64_t state[25];
    memset(state, 0, sizeof(state));

    /* Absorb full rate blocks */
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

    /* Final padded block: 0x06 at end of data, 0x80 at end of rate */
    uint8_t blk[136];
    memset(blk, 0, RATE);
    size_t rem = inlen - off;
    if (rem) memcpy(blk, in + off, rem);
    blk[rem]      ^= 0x06;
    blk[RATE - 1] ^= 0x80;

    uint64_t tmp;
    for (int i = 0; i < (int)(RATE/8); i++) {
        memcpy(&tmp, blk + i*8, 8);
        state[i] ^= tmp;
    }
    keccak_f_23(state);

    /* Squeeze: first 32 bytes */
    memcpy(out, state, 32);
}

/* Hex string → bytes */
static int hex2bytes(const char *hex, uint8_t *bytes, int n) {
    for (int i = 0; i < n; i++) {
        unsigned v = 0;
        if (sscanf(hex + 2*i, "%02x", &v) != 1) return -1;
        bytes[i] = (uint8_t)v;
    }
    return 0;
}

/*
 * solve_pow_c(base, base_len, challenge_hex, difficulty)
 * Returns first nonce in [0, difficulty) that matches challenge, or -1.
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
