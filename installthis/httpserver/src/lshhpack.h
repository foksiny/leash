/* lshhpack.h — HPACK (RFC 7541) codec shared by the Leash HTTP/2 client
 * (lshhttp.c) and server (lshhttpd.c).
 *
 * Everything here is `static` so both translation units can include it
 * without link conflicts. All allocation is plain malloc/free; the
 * callers hand GC-managed strings to Leash. Tables validated against the
 * RFC 7541 Appendix C vectors (C.4/C.5/C.6, Huffman included).
 *
 * Naming: hpk_ prefix. Out-params that allocate are marked; callers free.
 */

#ifndef LSH_HPACK_H
#define LSH_HPACK_H

#include <stddef.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Static table (RFC 7541 Appendix A), 1-based; index 0 is unused.      */
/* ------------------------------------------------------------------ */

typedef struct { const char *name; const char *value; } hpk_static_ent;

static const hpk_static_ent HPK_STATIC[62] = {
    {"", ""},
    {":authority", ""},
    {":method", "GET"},
    {":method", "POST"},
    {":path", "/"},
    {":path", "/index.html"},
    {":scheme", "http"},
    {":scheme", "https"},
    {":status", "200"},
    {":status", "204"},
    {":status", "206"},
    {":status", "304"},
    {":status", "400"},
    {":status", "404"},
    {":status", "500"},
    {"accept-charset", ""},
    {"accept-encoding", "gzip, deflate"},
    {"accept-language", ""},
    {"accept-ranges", ""},
    {"accept", ""},
    {"access-control-allow-origin", ""},
    {"age", ""},
    {"allow", ""},
    {"authorization", ""},
    {"cache-control", ""},
    {"content-disposition", ""},
    {"content-encoding", ""},
    {"content-language", ""},
    {"content-length", ""},
    {"content-location", ""},
    {"content-range", ""},
    {"content-type", ""},
    {"cookie", ""},
    {"date", ""},
    {"etag", ""},
    {"expect", ""},
    {"expires", ""},
    {"from", ""},
    {"host", ""},
    {"if-match", ""},
    {"if-modified-since", ""},
    {"if-none-match", ""},
    {"if-range", ""},
    {"if-unmodified-since", ""},
    {"last-modified", ""},
    {"link", ""},
    {"location", ""},
    {"max-forwards", ""},
    {"proxy-authenticate", ""},
    {"proxy-authorization", ""},
    {"range", ""},
    {"referer", ""},
    {"refresh", ""},
    {"retry-after", ""},
    {"server", ""},
    {"set-cookie", ""},
    {"strict-transport-security", ""},
    {"transfer-encoding", ""},
    {"user-agent", ""},
    {"vary", ""},
    {"via", ""},
    {"www-authenticate", ""}
};

/* ------------------------------------------------------------------ */
/* Huffman table (RFC 7541 Appendix B): {code, nbits} per symbol.      */
/* Generated from python-hpack ground truth; validated via App. C.     */
/* ------------------------------------------------------------------ */

typedef struct { unsigned int code; unsigned char nbits; } hpk_huff_sym;

static const hpk_huff_sym HPK_HUFF[256] = {
    {0x1ff8,13}, {0x7fffd8,23}, {0xfffffe2,28}, {0xfffffe3,28},
    {0xfffffe4,28}, {0xfffffe5,28}, {0xfffffe6,28}, {0xfffffe7,28},
    {0xfffffe8,28}, {0xffffea,24}, {0x3ffffffc,30}, {0xfffffe9,28},
    {0xfffffea,28}, {0x3ffffffd,30}, {0xfffffeb,28}, {0xfffffec,28},
    {0xfffffed,28}, {0xfffffee,28}, {0xfffffef,28}, {0xffffff0,28},
    {0xffffff1,28}, {0xffffff2,28}, {0x3ffffffe,30}, {0xffffff3,28},
    {0xffffff4,28}, {0xffffff5,28}, {0xffffff6,28}, {0xffffff7,28},
    {0xffffff8,28}, {0xffffff9,28}, {0xffffffa,28}, {0xffffffb,28},
    {0x14,6}, {0x3f8,10}, {0x3f9,10}, {0xffa,12},
    {0x1ff9,13}, {0x15,6}, {0xf8,8}, {0x7fa,11},
    {0x3fa,10}, {0x3fb,10}, {0xf9,8}, {0x7fb,11},
    {0xfa,8}, {0x16,6}, {0x17,6}, {0x18,6},
    {0x0,5}, {0x1,5}, {0x2,5}, {0x19,6},
    {0x1a,6}, {0x1b,6}, {0x1c,6}, {0x1d,6},
    {0x1e,6}, {0x1f,6}, {0x5c,7}, {0xfb,8},
    {0x7ffc,15}, {0x20,6}, {0xffb,12}, {0x3fc,10},
    {0x1ffa,13}, {0x21,6}, {0x5d,7}, {0x5e,7},
    {0x5f,7}, {0x60,7}, {0x61,7}, {0x62,7},
    {0x63,7}, {0x64,7}, {0x65,7}, {0x66,7},
    {0x67,7}, {0x68,7}, {0x69,7}, {0x6a,7},
    {0x6b,7}, {0x6c,7}, {0x6d,7}, {0x6e,7},
    {0x6f,7}, {0x70,7}, {0x71,7}, {0x72,7},
    {0xfc,8}, {0x73,7}, {0xfd,8}, {0x1ffb,13},
    {0x7fff0,19}, {0x1ffc,13}, {0x3ffc,14}, {0x22,6},
    {0x7ffd,15}, {0x3,5}, {0x23,6}, {0x4,5},
    {0x24,6}, {0x5,5}, {0x25,6}, {0x26,6},
    {0x27,6}, {0x6,5}, {0x74,7}, {0x75,7},
    {0x28,6}, {0x29,6}, {0x2a,6}, {0x7,5},
    {0x2b,6}, {0x76,7}, {0x2c,6}, {0x8,5},
    {0x9,5}, {0x2d,6}, {0x77,7}, {0x78,7},
    {0x79,7}, {0x7a,7}, {0x7b,7}, {0x7ffe,15},
    {0x7fc,11}, {0x3ffd,14}, {0x1ffd,13}, {0xffffffc,28},
    {0xfffe6,20}, {0x3fffd2,22}, {0xfffe7,20}, {0xfffe8,20},
    {0x3fffd3,22}, {0x3fffd4,22}, {0x3fffd5,22}, {0x7fffd9,23},
    {0x3fffd6,22}, {0x7fffda,23}, {0x7fffdb,23}, {0x7fffdc,23},
    {0x7fffdd,23}, {0x7fffde,23}, {0xffffeb,24}, {0x7fffdf,23},
    {0xffffec,24}, {0xffffed,24}, {0x3fffd7,22}, {0x7fffe0,23},
    {0xffffee,24}, {0x7fffe1,23}, {0x7fffe2,23}, {0x7fffe3,23},
    {0x7fffe4,23}, {0x1fffdc,21}, {0x3fffd8,22}, {0x7fffe5,23},
    {0x3fffd9,22}, {0x7fffe6,23}, {0x7fffe7,23}, {0xffffef,24},
    {0x3fffda,22}, {0x1fffdd,21}, {0xfffe9,20}, {0x3fffdb,22},
    {0x3fffdc,22}, {0x7fffe8,23}, {0x7fffe9,23}, {0x1fffde,21},
    {0x7fffea,23}, {0x3fffdd,22}, {0x3fffde,22}, {0xfffff0,24},
    {0x1fffdf,21}, {0x3fffdf,22}, {0x7fffeb,23}, {0x7fffec,23},
    {0x1fffe0,21}, {0x1fffe1,21}, {0x3fffe0,22}, {0x1fffe2,21},
    {0x7fffed,23}, {0x3fffe1,22}, {0x7fffee,23}, {0x7fffef,23},
    {0xfffea,20}, {0x3fffe2,22}, {0x3fffe3,22}, {0x3fffe4,22},
    {0x7ffff0,23}, {0x3fffe5,22}, {0x3fffe6,22}, {0x7ffff1,23},
    {0x3ffffe0,26}, {0x3ffffe1,26}, {0xfffeb,20}, {0x7fff1,19},
    {0x3fffe7,22}, {0x7ffff2,23}, {0x3fffe8,22}, {0x1ffffec,25},
    {0x3ffffe2,26}, {0x3ffffe3,26}, {0x3ffffe4,26}, {0x7ffffde,27},
    {0x7ffffdf,27}, {0x3ffffe5,26}, {0xfffff1,24}, {0x1ffffed,25},
    {0x7fff2,19}, {0x1fffe3,21}, {0x3ffffe6,26}, {0x7ffffe0,27},
    {0x7ffffe1,27}, {0x3ffffe7,26}, {0x7ffffe2,27}, {0xfffff2,24},
    {0x1fffe4,21}, {0x1fffe5,21}, {0x3ffffe8,26}, {0x3ffffe9,26},
    {0xffffffd,28}, {0x7ffffe3,27}, {0x7ffffe4,27}, {0x7ffffe5,27},
    {0xfffec,20}, {0xfffff3,24}, {0xfffed,20}, {0x1fffe6,21},
    {0x3fffe9,22}, {0x1fffe7,21}, {0x1fffe8,21}, {0x7ffff3,23},
    {0x3fffea,22}, {0x3fffeb,22}, {0x1ffffee,25}, {0x1ffffef,25},
    {0xfffff4,24}, {0xfffff5,24}, {0x3ffffea,26}, {0x7ffff4,23},
    {0x3ffffeb,26}, {0x7ffffe6,27}, {0x3ffffec,26}, {0x3ffffed,26},
    {0x7ffffe7,27}, {0x7ffffe8,27}, {0x7ffffe9,27}, {0x7ffffea,27},
    {0x7ffffeb,27}, {0xffffffe,28}, {0x7ffffec,27}, {0x7ffffed,27},
    {0x7ffffee,27}, {0x7ffffef,27}, {0x7fffff0,27}, {0x3ffffee,26},
};

/* ------------------------------------------------------------------ */
/* Huffman decode: caller-supplied tree (no shared state, thread-safe).*/
/* ------------------------------------------------------------------ */

typedef struct { int child[2]; int sym; } hpk_hnode; /* sym>=0: leaf */

/* Build the decode tree into `t` (needs >= 600 nodes). Returns count. */
static int hpk_huff_build(hpk_hnode *t) {
    t[0].child[0] = t[0].child[1] = -1;
    t[0].sym = -1;
    int nn = 1;
    for (int s = 0; s < 256; s++) {
        unsigned int code = HPK_HUFF[s].code;
        int nbits = HPK_HUFF[s].nbits;
        int node = 0;
        for (int b = nbits - 1; b >= 0; b--) {
            int bit = (int)((code >> (unsigned)b) & 1u);
            if (t[node].child[bit] < 0) {
                t[node].child[bit] = nn;
                t[nn].child[0] = t[nn].child[1] = -1;
                t[nn].sym = -1;
                nn++;
            }
            node = t[node].child[bit];
        }
        t[node].sym = s;
    }
    return nn;
}

/* Decode huffman bytes; returns out length or -1. Trailing padding must
 * be 1..7 one-bits (EOS prefix, RFC 7541 section 5.2). */
static int hpk_huff_decode(const hpk_hnode *t, const unsigned char *in,
                           size_t n, char *out, size_t outcap) {
    size_t olen = 0;
    int node = 0;
    int bits = 0;
    unsigned int frag = 0;
    for (size_t i = 0; i < n; i++) {
        for (int b = 7; b >= 0; b--) {
            int bit = (in[i] >> b) & 1;
            node = t[node].child[bit];
            if (node < 0) return -1;
            frag = (frag << 1) | (unsigned)bit;
            bits++;
            if (t[node].sym >= 0) {
                if (olen + 1 > outcap) return -1;
                out[olen++] = (char)t[node].sym;
                node = 0;
                bits = 0;
                frag = 0;
            }
        }
    }
    if (node != 0) {
        if (bits < 1 || bits > 7) return -1;
        if (frag != (unsigned)((1u << bits) - 1u)) return -1;
    }
    return (int)olen;
}

/* Huffman-encoded length of `s` (bits, without EOS pad). */
static size_t hpk_huff_bits(const char *s, size_t n) {
    size_t bits = 0;
    for (size_t i = 0; i < n; i++) bits += HPK_HUFF[(unsigned char)s[i]].nbits;
    return bits;
}

/* Append Huffman encoding of `s` to `dst` (`*off` byte offset, `*acc`/`*nbits`
 * carry partial-byte state). Flush with hpk_huff_flush. */
static void hpk_huff_enc(const char *s, size_t n, unsigned char *dst,
                         size_t *off, unsigned int *acc, int *nbits) {
    for (size_t i = 0; i < n; i++) {
        unsigned int code = HPK_HUFF[(unsigned char)s[i]].code;
        int nb = HPK_HUFF[(unsigned char)s[i]].nbits;
        *acc = (*acc << nb) | code;
        *nbits += nb;
        while (*nbits >= 8) {
            *nbits -= 8;
            dst[(*off)++] = (unsigned char)((*acc >> *nbits) & 0xffu);
        }
    }
}

static void hpk_huff_flush(unsigned char *dst, size_t *off,
                           unsigned int *acc, int *nbits) {
    if (*nbits > 0) {
        unsigned int pad = (unsigned)((1u << (8 - *nbits)) - 1u);
        dst[(*off)++] = (unsigned char)(((*acc << (8 - *nbits)) | pad) & 0xffu);
        *nbits = 0;
        *acc = 0;
    }
}

/* ------------------------------------------------------------------ */
/* Integer codec (RFC 7541 §5.1).                                      */
/* ------------------------------------------------------------------ */

typedef struct { unsigned char *p; size_t len; size_t cap; } hpk_buf;

static int hpk_bput(hpk_buf *b, const void *data, size_t n) {
    if (b->len + n > b->cap) {
        size_t nc = b->cap ? b->cap * 2 : 256;
        while (nc < b->len + n) nc *= 2;
        unsigned char *np = (unsigned char *)realloc(b->p, nc);
        if (!np) return 0;
        b->p = np;
        b->cap = nc;
    }
    memcpy(b->p + b->len, data, n);
    b->len += n;
    return 1;
}

/* Encode `v` OR-ed onto a prefix byte already holding the top bits. */
static int hpk_put_int(hpk_buf *b, unsigned int v, unsigned char first,
                       int nbits) {
    unsigned int max = (unsigned)((1u << nbits) - 1u);
    unsigned char c;
    if (v < max) {
        c = (unsigned char)(first | v);
        return hpk_bput(b, &c, 1);
    }
    c = (unsigned char)(first | max);
    if (!hpk_bput(b, &c, 1)) return 0;
    v -= max;
    while (v >= 128) {
        c = (unsigned char)((v & 127) | 128);
        if (!hpk_bput(b, &c, 1)) return 0;
        v >>= 7;
    }
    c = (unsigned char)v;
    return hpk_bput(b, &c, 1);
}

/* Decode from p[*pos]; advances *pos. Returns 0 on truncation. */
static int hpk_get_int(const unsigned char *p, size_t n, size_t *pos,
                       int nbits, unsigned int *out) {
    if (*pos >= n) return 0;
    unsigned int max = (unsigned)((1u << nbits) - 1u);
    unsigned int v = p[*pos] & max;
    (*pos)++;
    if (v < max) {
        *out = v;
        return 1;
    }
    unsigned int m = 0;
    for (;;) {
        if (*pos >= n) return 0;
        unsigned char c = p[(*pos)++];
        v += (unsigned int)(c & 127) << m;
        m += 7;
        if (!(c & 128)) break;
        if (m > 28) return 0; /* overflow guard */
    }
    *out = v;
    return 1;
}

/* ------------------------------------------------------------------ */
/* String codec: plain or Huffman, whichever is shorter.                */
/* ------------------------------------------------------------------ */

static int hpk_put_str(hpk_buf *b, const char *s, size_t len) {
    size_t hbits = hpk_huff_bits(s, len);
    size_t hlen = (hbits + 7) / 8;
    if (len > 0 && hlen < len) {
        if (!hpk_put_int(b, (unsigned int)hlen, 0x80, 7)) return 0;
        size_t need = b->len + hlen;
        if (need > b->cap) {
            unsigned char *np = (unsigned char *)realloc(b->p, need);
            if (!np) return 0;
            b->p = np;
            b->cap = need;
        }
        size_t off = b->len;
        unsigned int acc = 0;
        int nbits = 0;
        hpk_huff_enc(s, len, b->p, &off, &acc, &nbits);
        hpk_huff_flush(b->p, &off, &acc, &nbits);
        if (off - b->len != hlen) return 0; /* cannot happen */
        b->len = off;
        return 1;
    }
    if (!hpk_put_int(b, (unsigned int)len, 0x00, 7)) return 0;
    if (len == 0) return 1;
    return hpk_bput(b, s, len);
}

/* Decode one string; mallocs *out (NUL-terminated, len in *olen). */
static int hpk_get_str(const unsigned char *p, size_t n, size_t *pos,
                       const hpk_hnode *ht, char **out, size_t *olen) {
    if (*pos >= n) return 0;
    int huff = (p[*pos] & 0x80) != 0;
    unsigned int len = 0;
    if (!hpk_get_int(p, n, pos, 7, &len)) return 0;
    if (*pos + len > n) return 0;
    /* Huffman can expand (shortest code is 5 bits): 2x is ample. */
    size_t cap = huff ? len * 2 + 1 : len + 1;
    char *s = (char *)malloc(cap);
    if (!s) return 0;
    if (huff) {
        int dl = hpk_huff_decode(ht, p + *pos, len, s, cap);
        if (dl < 0) {
            free(s);
            return 0;
        }
        s[dl] = '\0';
        *olen = (size_t)dl;
    } else {
        memcpy(s, p + *pos, len);
        s[len] = '\0';
        *olen = len;
    }
    *pos += len;
    *out = s;
    return 1;
}

/* ------------------------------------------------------------------ */
/* Dynamic table (decode side). Newest entry is index 62.               */
/* ------------------------------------------------------------------ */

#define HPK_DYN_MAX_DEFAULT 4096

typedef struct { char *name; char *value; size_t size; } hpk_dynent;

typedef struct {
    hpk_dynent *e; /* oldest first */
    int n;
    int cap;
    size_t max;
    size_t cur;
} hpk_dyn;

static void hpk_dyn_init(hpk_dyn *d, size_t max) {
    d->e = NULL;
    d->n = 0;
    d->cap = 0;
    d->max = max;
    d->cur = 0;
}

static void hpk_dyn_free(hpk_dyn *d) {
    for (int i = 0; i < d->n; i++) {
        free(d->e[i].name);
        free(d->e[i].value);
    }
    free(d->e);
    d->e = NULL;
    d->n = d->cap = 0;
    d->cur = 0;
}

static void hpk_dyn_evict(hpk_dyn *d) {
    while (d->n > 0 && d->cur > d->max) {
        d->cur -= d->e[0].size;
        free(d->e[0].name);
        free(d->e[0].value);
        memmove(d->e, d->e + 1, (size_t)(d->n - 1) * sizeof(hpk_dynent));
        d->n--;
    }
}

/* Insert (takes ownership of name/value); evicts as needed. */
static int hpk_dyn_insert(hpk_dyn *d, char *name, char *value) {
    size_t sz = 32 + strlen(name) + strlen(value);
    if (sz > d->max) {
        free(name);
        free(value);
        d->cur = 0;
        /* Entry too big: table is drained per RFC 7541 §4.2. */
        for (int i = 0; i < d->n; i++) {
            free(d->e[i].name);
            free(d->e[i].value);
        }
        d->n = 0;
        return 1;
    }
    if (d->n + 1 > d->cap) {
        int nc = d->cap ? d->cap * 2 : 16;
        hpk_dynent *ne = (hpk_dynent *)realloc(d->e, (size_t)nc * sizeof(hpk_dynent));
        if (!ne) {
            free(name);
            free(value);
            return 0;
        }
        d->e = ne;
        d->cap = nc;
    }
    d->e[d->n].name = name;
    d->e[d->n].value = value;
    d->e[d->n].size = sz;
    d->n++;
    d->cur += sz;
    hpk_dyn_evict(d);
    return 1;
}

/* Global index (62-based) to entry; NULL when out of range. */
static hpk_dynent *hpk_dyn_at(hpk_dyn *d, unsigned int idx) {
    if (idx < 62) return NULL;
    unsigned int k = idx - 62; /* 0 == newest */
    if ((int)k >= d->n) return NULL;
    return &d->e[d->n - 1 - (int)k];
}

/* ------------------------------------------------------------------ */
/* Header lists.                                                       */
/* ------------------------------------------------------------------ */

typedef struct { char *name; char *value; } hpk_hdr;

typedef struct { hpk_hdr *v; int n; int cap; } hpk_hdrs;

static void hpk_hdrs_init(hpk_hdrs *h) {
    h->v = NULL;
    h->n = 0;
    h->cap = 0;
}

static void hpk_hdrs_free(hpk_hdrs *h) {
    for (int i = 0; i < h->n; i++) {
        free(h->v[i].name);
        free(h->v[i].value);
    }
    free(h->v);
    h->v = NULL;
    h->n = h->cap = 0;
}

static int hpk_hdrs_add(hpk_hdrs *h, char *name, char *value) {
    if (h->n + 1 > h->cap) {
        int nc = h->cap ? h->cap * 2 : 8;
        hpk_hdr *nv = (hpk_hdr *)realloc(h->v, (size_t)nc * sizeof(hpk_hdr));
        if (!nv) {
            free(name);
            free(value);
            return 0;
        }
        h->v = nv;
        h->cap = nc;
    }
    h->v[h->n].name = name;
    h->v[h->n].value = value;
    h->n++;
    return 1;
}

/* Decode a header block. `dyn` updated for incremental entries.
 * Returns 0 on malformed input (caller sends COMPRESSION_ERROR). */
static int hpk_decode_block(const unsigned char *p, size_t n, hpk_dyn *dyn,
                            const hpk_hnode *ht, hpk_hdrs *out) {
    size_t pos = 0;
    while (pos < n) {
        unsigned char b = p[pos];
        if (b & 0x80) {
            /* Indexed header field. */
            unsigned int idx = 0;
            if (!hpk_get_int(p, n, &pos, 7, &idx)) return 0;
            const char *nm = NULL, *vv = NULL;
            char *dn = NULL, *dv = NULL;
            if (idx == 0) return 0;
            if (idx <= 61) {
                nm = HPK_STATIC[idx].name;
                vv = HPK_STATIC[idx].value;
            } else {
                hpk_dynent *e = hpk_dyn_at(dyn, idx);
                if (!e) return 0;
                dn = e->name;
                dv = e->value;
                nm = dn;
                vv = dv;
            }
            char *cn = (char *)malloc(strlen(nm) + 1);
            char *cv = (char *)malloc(strlen(vv) + 1);
            if (!cn || !cv) {
                free(cn);
                free(cv);
                return 0;
            }
            strcpy(cn, nm);
            strcpy(cv, vv);
            if (!hpk_hdrs_add(out, cn, cv)) return 0;
        } else if ((b & 0x40) != 0) {
            /* Literal with incremental indexing. */
            unsigned int idx = 0;
            if (!hpk_get_int(p, n, &pos, 6, &idx)) return 0;
            char *nm = NULL;
            if (idx == 0) {
                size_t nl = 0;
                if (!hpk_get_str(p, n, &pos, ht, &nm, &nl)) return 0;
            } else {
                const char *sn = NULL;
                if (idx <= 61) {
                    sn = HPK_STATIC[idx].name;
                } else {
                    hpk_dynent *e = hpk_dyn_at(dyn, idx);
                    if (!e) return 0;
                    sn = e->name;
                }
                nm = (char *)malloc(strlen(sn) + 1);
                if (!nm) return 0;
                strcpy(nm, sn);
            }
            char *vv = NULL;
            size_t vl = 0;
            if (!hpk_get_str(p, n, &pos, ht, &vv, &vl)) {
                free(nm);
                return 0;
            }
            /* Insert a copy; the list keeps the originals. */
            char *in_ = (char *)malloc(strlen(nm) + 1);
            char *iv = (char *)malloc(strlen(vv) + 1);
            if (!in_ || !iv) {
                free(in_);
                free(iv);
                free(nm);
                free(vv);
                return 0;
            }
            strcpy(in_, nm);
            strcpy(iv, vv);
            if (!hpk_dyn_insert(dyn, in_, iv)) {
                free(nm);
                free(vv);
                return 0;
            }
            if (!hpk_hdrs_add(out, nm, vv)) return 0;
        } else if ((b & 0x20) != 0) {
            /* Dynamic table size update. */
            unsigned int mx = 0;
            if (!hpk_get_int(p, n, &pos, 5, &mx)) return 0;
            if (mx > dyn->max) return 0; /* shrinking only mid-stream */
            dyn->max = mx;
            hpk_dyn_evict(dyn);
        } else {
            /* Literal without indexing / never indexed. */
            unsigned int idx = 0;
            if (!hpk_get_int(p, n, &pos, 4, &idx)) return 0;
            char *nm = NULL;
            if (idx == 0) {
                size_t nl = 0;
                if (!hpk_get_str(p, n, &pos, ht, &nm, &nl)) return 0;
            } else {
                const char *sn = NULL;
                if (idx <= 61) {
                    sn = HPK_STATIC[idx].name;
                } else {
                    hpk_dynent *e = hpk_dyn_at(dyn, idx);
                    if (!e) return 0;
                    sn = e->name;
                }
                nm = (char *)malloc(strlen(sn) + 1);
                if (!nm) return 0;
                strcpy(nm, sn);
            }
            char *vv = NULL;
            size_t vl = 0;
            if (!hpk_get_str(p, n, &pos, ht, &vv, &vl)) {
                free(nm);
                return 0;
            }
            if (!hpk_hdrs_add(out, nm, vv)) return 0;
        }
    }
    return 1;
}

/* Encode one field literal-without-indexing. `name_idx` is a static index
 * for the name (0 = literal name). Values Huffman-encode when shorter. */
static int hpk_enc_field(hpk_buf *b, unsigned int name_idx, const char *name,
                         const char *value) {
    if (name_idx > 0) {
        if (!hpk_put_int(b, name_idx, 0x00, 4)) return 0;
    } else {
        if (!hpk_put_int(b, 0, 0x00, 4)) return 0;
        if (!hpk_put_str(b, name, strlen(name))) return 0;
    }
    return hpk_put_str(b, value, strlen(value));
}

/* Static-table exact (name+value) match, else name-only match.
 * Returns index or 0; *exact set when both match. */
static unsigned int hpk_static_find(const char *name, const char *value,
                                    int *exact) {
    unsigned int name_hit = 0;
    *exact = 0;
    for (unsigned int i = 1; i <= 61; i++) {
        if (strcmp(HPK_STATIC[i].name, name) == 0) {
            if (!name_hit) name_hit = i;
            if (strcmp(HPK_STATIC[i].value, value) == 0) {
                *exact = 1;
                return i;
            }
        }
    }
    return name_hit;
}

#endif /* LSH_HPACK_H */
