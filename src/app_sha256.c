/*
 * Copyright 2026 ValeEng
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
#include "app_sha256.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* FIPS 180-4, section 4.2.2: the first 32 bits of the fractional parts
 * of the cube roots of the first 64 primes. */
static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

/* One 64-byte block into the running state h[8]. */
static void sha256_block(uint32_t h[8], const unsigned char *p) {
    uint32_t w[64];
    for (int i = 0; i < 16; ++i)
        w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16)
             | ((uint32_t)p[4 * i + 2] << 8) | (uint32_t)p[4 * i + 3];
    for (int i = 16; i < 64; ++i) {
        uint32_t s0 = ROTR(w[i - 15], 7) ^ ROTR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROTR(w[i - 2], 17) ^ ROTR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], b = h[1], c = h[2], d = h[3];
    uint32_t e = h[4], f = h[5], g = h[6], k = h[7];
    for (int i = 0; i < 64; ++i) {
        uint32_t S1 = ROTR(e, 6) ^ ROTR(e, 11) ^ ROTR(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = k + S1 + ch + K[i] + w[i];
        uint32_t S0 = ROTR(a, 2) ^ ROTR(a, 13) ^ ROTR(a, 22);
        uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = S0 + mj;
        k = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d;
    h[4] += e; h[5] += f; h[6] += g; h[7] += k;
}

int spody_sha256_file(const char *path, char hex_out[65],
                      long long *size_out) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return -1;

    uint32_t h[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19 };
    static unsigned char buf[1 << 16];   /* 64 KiB, a multiple of 64 */
    unsigned long long total = 0;
    size_t n;
    size_t tail = 0;          /* bytes not yet hashed, kept at buf[0] */
    while ((n = fread(buf + tail, 1, sizeof buf - tail, fp)) > 0) {
        total += n;
        size_t have = tail + n;
        size_t full = have - (have % 64);
        for (size_t off = 0; off < full; off += 64) sha256_block(h, buf + off);
        tail = have - full;
        memmove(buf, buf + full, tail);
    }
    int failed = ferror(fp);
    fclose(fp);
    if (failed) return -1;

    /* Padding: 0x80, zeros, then the bit length as a 64-bit big-endian
     * integer, in one or two final blocks. */
    unsigned char last[128];
    memcpy(last, buf, tail);
    last[tail] = 0x80;
    size_t len = (tail < 56) ? 64 : 128;
    memset(last + tail + 1, 0, len - tail - 1);
    unsigned long long bits = total * 8ULL;
    for (int i = 0; i < 8; ++i)
        last[len - 1 - i] = (unsigned char)(bits >> (8 * i));
    sha256_block(h, last);
    if (len == 128) sha256_block(h, last + 64);

    for (int i = 0; i < 8; ++i)
        snprintf(hex_out + 8 * i, 9, "%08x", (unsigned)h[i]);
    if (size_out) *size_out = (long long)total;
    return 0;
}
