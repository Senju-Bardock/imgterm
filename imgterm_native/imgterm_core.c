/*
 * imgterm_core.c  -  native engine for imgterm
 *
 * Plain C99, no dependencies (libc + libm only). It is loaded from Python with
 * ctypes, so there is no Python C-API in here: every function takes flat byte
 * buffers (RGB = 3 bytes per pixel, row after row) and returns a status code.
 *
 * The code is a line-by-line port of the pure Python implementation in
 * imgterm.py and it is meant to give the SAME output (same pixels, same
 * escape sequences). Floating point maths is done in double precision and in the
 * same order as the Python code, so build WITHOUT -ffast-math and with
 * -ffp-contract=off (build_native.py does both).
 *
 *   PNG    unfilter + color conversion (zlib inflate stays in Python)
 *   JPEG   Huffman scans (baseline + progressive), IDCT, upsampling, YCbCr
 *   GIF    LZW and frame composition
 *   resize gamma-correct (linear light) area / bilinear resize
 *   blocks quadrant characters renderer
 *   sixel  median-cut quantizer + sixel encoder
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#  define EXPORT __declspec(dllexport)
#elif defined(__GNUC__)
#  define EXPORT __attribute__((visibility("default")))
#else
#  define EXPORT
#endif

#define IMGTERM_ABI 2

EXPORT int imgterm_abi(void) { return IMGTERM_ABI; }
EXPORT void imgterm_free(void *p) { free(p); }

/* ------------------------------------------------------------------------- */
/* growable output buffer                                                    */
/* ------------------------------------------------------------------------- */
typedef struct {
    uint8_t *p;
    size_t n, cap;
    int err;
} Buf;

static void buf_need(Buf *b, size_t k) {
    if (b->err) return;
    if (b->n + k <= b->cap) return;
    size_t nc = b->cap ? b->cap : 4096;
    while (nc < b->n + k) nc *= 2;
    uint8_t *q = (uint8_t *)realloc(b->p, nc);
    if (!q) { b->err = 1; return; }
    b->p = q;
    b->cap = nc;
}

static void buf_put(Buf *b, const void *s, size_t k) {
    buf_need(b, k);
    if (b->err) return;
    memcpy(b->p + b->n, s, k);
    b->n += k;
}

static void buf_byte(Buf *b, uint8_t c) {
    buf_need(b, 1);
    if (b->err) return;
    b->p[b->n++] = c;
}

static void buf_int(Buf *b, long v) {
    char tmp[24];
    int n = 0;
    if (v == 0) tmp[n++] = '0';
    while (v > 0) { tmp[n++] = (char)('0' + v % 10); v /= 10; }
    buf_need(b, (size_t)n);
    if (b->err) return;
    while (n) b->p[b->n++] = (uint8_t)tmp[--n];
}

/* ------------------------------------------------------------------------- */
/* 1. PNG: unfilter + convert every scanline to RGB                          */
/* ------------------------------------------------------------------------- */
static const int ADAM7[7][4] = {{0, 0, 8, 8}, {4, 0, 8, 8}, {0, 4, 4, 8}, {2, 0, 4, 4},
                                {0, 2, 2, 4}, {1, 0, 2, 2}, {0, 1, 1, 2}};

static inline int sample_at(const uint8_t *line, int i, int depth) {
    if (depth == 8) return line[i];
    if (depth == 16) return line[2 * i];            /* keep the high byte */
    int per = 8 / depth;
    int k = i % per;
    return (line[i / per] >> (8 - depth * (k + 1))) & ((1 << depth) - 1);
}

static inline void blend(uint8_t *d, int r, int g, int b, int a, const int *bg) {
    if (a != 255) {
        r = (r * a + bg[0] * (255 - a)) / 255;
        g = (g * a + bg[1] * (255 - a)) / 255;
        b = (b * a + bg[2] * (255 - a)) / 255;
    }
    d[0] = (uint8_t)r; d[1] = (uint8_t)g; d[2] = (uint8_t)b;
}

static void row_to_rgb(const uint8_t *line, int pw, int color, int depth,
                       const uint8_t *pal, int trns_mode, const int *trns, const int *bg,
                       uint8_t *row, int xs, int dx) {
    int scale = depth < 8 ? 255 / ((1 << depth) - 1) : 1;
    uint8_t *d = row + 3 * (size_t)xs;
    size_t step = 3 * (size_t)dx;
    if (color == 6 && depth == 8) {
        for (int i = 0; i < pw; i++, d += step) blend(d, line[4 * i], line[4 * i + 1], line[4 * i + 2], line[4 * i + 3], bg);
        return;
    }
    if (color == 2 && depth == 8 && !trns_mode) {
        for (int i = 0; i < pw; i++, d += step) { d[0] = line[3 * i]; d[1] = line[3 * i + 1]; d[2] = line[3 * i + 2]; }
        return;
    }
    for (int i = 0; i < pw; i++, d += step) {
        int r, g, b, a = 255;
        switch (color) {
        case 0: {
            int v = sample_at(line, i, depth);
            if (trns_mode && v == trns[0]) a = 0;
            r = g = b = v * scale;
            break;
        }
        case 2:
            r = sample_at(line, 3 * i, depth);
            g = sample_at(line, 3 * i + 1, depth);
            b = sample_at(line, 3 * i + 2, depth);
            if (trns_mode && r == trns[0] && g == trns[1] && b == trns[2]) a = 0;
            break;
        case 3: {
            int idx = sample_at(line, i, depth);
            r = pal[3 * idx]; g = pal[3 * idx + 1]; b = pal[3 * idx + 2];
            break;
        }
        case 4:
            r = g = b = sample_at(line, 2 * i, depth);
            a = sample_at(line, 2 * i + 1, depth);
            break;
        default:
            r = sample_at(line, 4 * i, depth);
            g = sample_at(line, 4 * i + 1, depth);
            b = sample_at(line, 4 * i + 2, depth);
            a = sample_at(line, 4 * i + 3, depth);
            break;
        }
        blend(d, r, g, b, a, bg);
    }
}

/*
 * raw      inflated IDAT data
 * pal      768 bytes, already blended with the background (color type 3)
 * trns_mode 0 = none, 1 = color key in trns[0..2] (gray uses trns[0])
 * out      w*h*3 bytes
 * returns 0, or -1 when the data is too short
 */
EXPORT int png_to_rgb(const uint8_t *raw, size_t raw_len, int w, int h, int depth, int color,
                      int interlaced, const uint8_t *pal, int trns_mode, const int *trns,
                      const int *bg, uint8_t *out) {
    int channels = color == 0 ? 1 : color == 2 ? 3 : color == 3 ? 1 : color == 4 ? 2 : 4;
    int bits = channels * depth;
    int bpp = bits / 8 < 1 ? 1 : bits / 8;
    size_t maxstride = ((size_t)w * bits + 7) / 8;
    uint8_t *bufs = (uint8_t *)calloc(2 * (maxstride + 16), 1);
    if (!bufs) return -1;
    uint8_t *prev = bufs, *cur = bufs + maxstride + 16;
    size_t pos = 0;
    int rc = 0;

    for (int pass = 0; pass < (interlaced ? 7 : 1); pass++) {
        int xs = 0, ys = 0, dx = 1, dy = 1;
        if (interlaced) { xs = ADAM7[pass][0]; ys = ADAM7[pass][1]; dx = ADAM7[pass][2]; dy = ADAM7[pass][3]; }
        int pw = (w - xs + dx - 1) / dx, ph = (h - ys + dy - 1) / dy;
        if (pw <= 0 || ph <= 0) continue;
        size_t stride = ((size_t)pw * bits + 7) / 8;
        memset(prev, 0, stride);
        for (int j = 0; j < ph; j++) {
            if (pos + 1 + stride > raw_len) { rc = -1; goto done; }
            int ft = raw[pos];
            memcpy(cur, raw + pos + 1, stride);
            pos += 1 + stride;
            switch (ft) {
            case 1:
                for (size_t i = bpp; i < stride; i++) cur[i] = (uint8_t)(cur[i] + cur[i - bpp]);
                break;
            case 2:
                for (size_t i = 0; i < stride; i++) cur[i] = (uint8_t)(cur[i] + prev[i]);
                break;
            case 3:
                for (size_t i = 0; i < stride; i++) {
                    int left = i >= (size_t)bpp ? cur[i - bpp] : 0;
                    cur[i] = (uint8_t)(cur[i] + ((left + prev[i]) >> 1));
                }
                break;
            case 4:
                for (size_t i = 0; i < stride; i++) {
                    int a = i >= (size_t)bpp ? cur[i - bpp] : 0;
                    int b = prev[i];
                    int c = i >= (size_t)bpp ? prev[i - bpp] : 0;
                    int p = a + b - c, pa = abs(p - a), pb = abs(p - b), pc = abs(p - c);
                    int pr = (pa <= pb && pa <= pc) ? a : (pb <= pc) ? b : c;
                    cur[i] = (uint8_t)(cur[i] + pr);
                }
                break;
            default: break;
            }
            row_to_rgb(cur, pw, color, depth, pal, trns_mode, trns, bg,
                       out + (size_t)(ys + j * dy) * w * 3, xs, dx);
            uint8_t *t = prev; prev = cur; cur = t;
        }
    }
done:
    free(bufs);
    return rc;
}

/* ------------------------------------------------------------------------- */
/* 2. Resize in linear light (area average when shrinking, bilinear otherwise) */
/* ------------------------------------------------------------------------- */
static double TO_LINEAR[256];
static uint8_t FROM_LINEAR[4096];

/* Tables are computed by Python (same numbers as the pure Python path). */
EXPORT void resize_init(const double *to_linear, const uint8_t *from_linear) {
    memcpy(TO_LINEAR, to_linear, sizeof TO_LINEAR);
    memcpy(FROM_LINEAR, from_linear, sizeof FROM_LINEAR);
}

typedef struct { int *off; int *src; double *w; } Taps;

static int taps_make(Taps *t, int src_n, int dst_n) {
    size_t cap = (size_t)src_n + 2 * (size_t)dst_n + 16;
    t->off = (int *)malloc(sizeof(int) * ((size_t)dst_n + 1));
    t->src = (int *)malloc(sizeof(int) * cap);
    t->w = (double *)malloc(sizeof(double) * cap);
    if (!t->off || !t->src || !t->w) return -1;
    double ratio = (double)src_n / (double)dst_n;
    int n = 0;
    for (int d = 0; d < dst_n; d++) {
        t->off[d] = n;
        if (ratio >= 1) {
            double a = d * ratio, b = (d + 1) * ratio;
            int start = n;
            int hi = (int)ceil(b);
            if (hi > src_n) hi = src_n;
            for (int i = (int)a; i < hi; i++) {
                double lo_ = a > i ? a : i;
                double hi_ = b < i + 1 ? b : i + 1;
                double w = hi_ - lo_;
                if (w > 1e-9) { t->src[n] = i; t->w[n] = w / ratio; n++; }
            }
            if (n == start) {
                int i = (int)a;
                if (i > src_n - 1) i = src_n - 1;
                t->src[n] = i; t->w[n] = 1.0; n++;
            }
        } else {
            double c = (d + 0.5) * ratio - 0.5;
            int i0 = (int)floor(c);
            double f = c - i0;
            int lo = i0 < 0 ? 0 : i0 > src_n - 1 ? src_n - 1 : i0;
            int hi = i0 + 1 < 0 ? 0 : i0 + 1 > src_n - 1 ? src_n - 1 : i0 + 1;
            if (lo != hi && f > 1e-9) {
                t->src[n] = lo; t->w[n] = 1 - f; n++;
                t->src[n] = hi; t->w[n] = f; n++;
            } else { t->src[n] = lo; t->w[n] = 1.0; n++; }
        }
    }
    t->off[dst_n] = n;
    return 0;
}

static void taps_free(Taps *t) { free(t->off); free(t->src); free(t->w); }

/* in: w*h*3 sRGB bytes -> out: new_w*new_h*3 sRGB bytes */
EXPORT int resize_rgb(const uint8_t *in, int w, int h, int new_w, int new_h, uint8_t *out) {
    Taps ty = {0}, tx = {0};
    double *tmp = NULL;
    int rc = -1;
    if (taps_make(&ty, h, new_h) || taps_make(&tx, w, new_w)) goto done;
    size_t rowlen = (size_t)w * 3;
    tmp = (double *)malloc(sizeof(double) * rowlen * (size_t)new_h);
    if (!tmp) goto done;

    /* vertical pass (same tap order as the Python code) */
    for (int y = 0; y < new_h; y++) {
        double *dst = tmp + rowlen * y;
        int k0 = ty.off[y], k1 = ty.off[y + 1];
        const uint8_t *s = in + rowlen * ty.src[k0];
        double wt = ty.w[k0];
        if (k1 - k0 == 1 && wt == 1.0) {
            for (size_t i = 0; i < rowlen; i++) dst[i] = TO_LINEAR[s[i]];
            continue;
        }
        for (size_t i = 0; i < rowlen; i++) dst[i] = wt * TO_LINEAR[s[i]];
        for (int k = k0 + 1; k < k1; k++) {
            s = in + rowlen * ty.src[k];
            wt = ty.w[k];
            for (size_t i = 0; i < rowlen; i++) dst[i] = dst[i] + wt * TO_LINEAR[s[i]];
        }
    }
    /* horizontal pass + back to sRGB */
    for (int y = 0; y < new_h; y++) {
        const double *row = tmp + rowlen * y;
        uint8_t *o = out + (size_t)y * new_w * 3;
        for (int x = 0; x < new_w; x++) {
            int k0 = tx.off[x], k1 = tx.off[x + 1];
            double acc[3];
            const double *s = row + 3 * (size_t)tx.src[k0];
            double wt = tx.w[k0];
            if (k1 - k0 == 1 && wt == 1.0) { acc[0] = s[0]; acc[1] = s[1]; acc[2] = s[2]; }
            else { acc[0] = wt * s[0]; acc[1] = wt * s[1]; acc[2] = wt * s[2]; }
            for (int k = k0 + 1; k < k1; k++) {
                s = row + 3 * (size_t)tx.src[k];
                wt = tx.w[k];
                acc[0] = acc[0] + wt * s[0];
                acc[1] = acc[1] + wt * s[1];
                acc[2] = acc[2] + wt * s[2];
            }
            for (int c = 0; c < 3; c++) {
                int t = (int)(acc[c] * 4095 + 0.5);
                if (t > 4095) t = 4095;
                if (t < 0) t = 0;
                o[3 * x + c] = FROM_LINEAR[t];
            }
        }
    }
    rc = 0;
done:
    taps_free(&ty); taps_free(&tx); free(tmp);
    return rc;
}

/* ------------------------------------------------------------------------- */
/* 3. "blocks" renderer: best-fit quadrant glyph per character cell          */
/* ------------------------------------------------------------------------- */
static const struct { int mask; const char *glyph; } PARTS[8] = {
    {0, " "}, {1, "\xe2\x96\x98"}, {2, "\xe2\x96\x9d"}, {4, "\xe2\x96\x96"},
    {8, "\xe2\x96\x97"}, {3, "\xe2\x96\x80"}, {5, "\xe2\x96\x8c"}, {6, "\xe2\x96\x9e"}};

static void group(const uint8_t *const *px, int mask, int want, int *mean, double *err) {
    int n = 0, sr = 0, sg = 0, sb = 0, sq = 0;
    for (int i = 0; i < 4; i++) {
        if (((mask >> i) & 1) != want) continue;
        int r = px[i][0], g = px[i][1], b = px[i][2];
        n++; sr += r; sg += g; sb += b; sq += r * r + g * g + b * b;
    }
    mean[0] = (int)((double)sr / n + 0.5);
    mean[1] = (int)((double)sg / n + 0.5);
    mean[2] = (int)((double)sb / n + 0.5);
    *err = (double)sq - (double)(sr * sr + sg * sg + sb * sb) / n;
}

static const int LEVELS[6] = {0, 95, 135, 175, 215, 255};

static int to_256(int r, int g, int b) {
    int v[3] = {r, g, b}, ci[3];
    for (int c = 0; c < 3; c++) {
        int best = 0, bd = abs(LEVELS[0] - v[c]);
        for (int k = 1; k < 6; k++) { int d = abs(LEVELS[k] - v[c]); if (d < bd) { bd = d; best = k; } }
        ci[c] = best;
    }
    int cr = LEVELS[ci[0]], cg = LEVELS[ci[1]], cb = LEVELS[ci[2]];
    int cube = 16 + 36 * ci[0] + 6 * ci[1] + ci[2];
    int gi = (r + g + b) / 3 - 3;
    if (gi < 0) gi = 0;
    gi /= 10;
    if (gi > 23) gi = 23;
    int gv = 8 + 10 * gi;
    int dc = (r - cr) * (r - cr) + (g - cg) * (g - cg) + (b - cb) * (b - cb);
    int dg = (r - gv) * (r - gv) + (g - gv) * (g - gv) + (b - gv) * (b - gv);
    return dc <= dg ? cube : 232 + gi;
}

static void put_sgr(Buf *o, int base, const int *rgb, int truecolor) {
    buf_put(o, "\x1b[", 2);
    buf_int(o, base);
    if (truecolor) {
        buf_put(o, ";2;", 3);
        buf_int(o, rgb[0]); buf_byte(o, ';'); buf_int(o, rgb[1]); buf_byte(o, ';'); buf_int(o, rgb[2]);
    } else {
        buf_put(o, ";5;", 3);
        buf_int(o, to_256(rgb[0], rgb[1], rgb[2]));
    }
    buf_byte(o, 'm');
}

/* img: w*h*3 (w and h even). Returns a malloc'ed UTF-8 string (free with imgterm_free). */
EXPORT int render_blocks(const uint8_t *img, int w, int h, int truecolor, uint8_t **out, size_t *out_len) {
    Buf o = {0};
    for (int y = 0; y < h; y += 2) {
        const uint8_t *top = img + (size_t)y * w * 3, *bot = img + (size_t)(y + 1) * w * 3;
        int cur_fg[3] = {-1, -1, -1}, cur_bg[3] = {-1, -1, -1};
        for (int x = 0; x < w; x += 2) {
            const uint8_t *px[4] = {top + 3 * x, top + 3 * (x + 1), bot + 3 * x, bot + 3 * (x + 1)};
            int has_fg = 0, fg[3] = {0, 0, 0}, bg[3];
            const char *glyph;
            if (!memcmp(px[0], px[1], 3) && !memcmp(px[0], px[2], 3) && !memcmp(px[0], px[3], 3)) {
                glyph = " ";
                bg[0] = px[0][0]; bg[1] = px[0][1]; bg[2] = px[0][2];
            } else {
                double best = 0;
                int first = 1;
                glyph = " ";
                for (int p = 0; p < 8; p++) {
                    int f[3] = {0, 0, 0}, b[3];
                    double err, e1, e2;
                    int hf;
                    if (PARTS[p].mask == 0) { hf = 0; group(px, 0, 0, b, &err); }
                    else {
                        hf = 1;
                        group(px, PARTS[p].mask, 1, f, &e1);
                        group(px, PARTS[p].mask, 0, b, &e2);
                        err = e1 + e2;
                    }
                    if (first || err < best) {
                        first = 0; best = err; glyph = PARTS[p].glyph; has_fg = hf;
                        memcpy(fg, f, sizeof f); memcpy(bg, b, sizeof b);
                    }
                }
            }
            if (has_fg && memcmp(fg, cur_fg, sizeof fg)) { put_sgr(&o, 38, fg, truecolor); memcpy(cur_fg, fg, sizeof fg); }
            if (memcmp(bg, cur_bg, sizeof bg)) { put_sgr(&o, 48, bg, truecolor); memcpy(cur_bg, bg, sizeof bg); }
            buf_put(&o, glyph, strlen(glyph));
        }
        buf_put(&o, "\x1b[0m", 4);
        if (y + 2 < h) buf_byte(&o, '\n');
    }
    if (o.err) { free(o.p); return -1; }
    *out = o.p; *out_len = o.n;
    return 0;
}

/* ------------------------------------------------------------------------- */
/* 4. Sixel: median-cut quantizer (same algorithm as the Python version)     */
/* ------------------------------------------------------------------------- */
typedef struct { int key; int count; long long r, g, b; } Item;   /* r,g,b are sums */
typedef struct { long long neg; int serial; int axis; int start, len; } HeapEnt;

static int less(const HeapEnt *a, const HeapEnt *b) {
    return a->neg < b->neg || (a->neg == b->neg && a->serial < b->serial);
}

static void siftdown(HeapEnt *h, int startpos, int pos) {
    HeapEnt item = h[pos];
    while (pos > startpos) {
        int parent = (pos - 1) >> 1;
        if (less(&item, &h[parent])) { h[pos] = h[parent]; pos = parent; continue; }
        break;
    }
    h[pos] = item;
}

static void siftup(HeapEnt *h, int endpos, int pos) {
    int startpos = pos;
    HeapEnt item = h[pos];
    int child = 2 * pos + 1;
    while (child < endpos) {
        int right = child + 1;
        if (right < endpos && !less(&h[child], &h[right])) child = right;
        h[pos] = h[child];
        pos = child;
        child = 2 * pos + 1;
    }
    h[pos] = item;
    siftdown(h, startpos, pos);
}

static void heap_push(HeapEnt *h, int *n, HeapEnt e) { h[*n] = e; (*n)++; siftdown(h, 0, *n - 1); }

static void heap_pop(HeapEnt *h, int *n) {
    HeapEnt last = h[--(*n)];
    if (*n) { h[0] = last; siftup(h, *n, 0); }
}

static int axis_val(const Item *it, int axis) {
    return axis == 0 ? (it->key >> 12) & 63 : axis == 1 ? (it->key >> 6) & 63 : it->key & 63;
}

/* stable merge sort of perm[start .. start+len) by the value of one axis */
static void msort(int *perm, int *tmp, const Item *items, int len, int axis) {
    if (len < 2) return;
    int mid = len / 2;
    msort(perm, tmp, items, mid, axis);
    msort(perm + mid, tmp, items, len - mid, axis);
    int i = 0, j = mid, k = 0;
    while (i < mid && j < len) {
        if (axis_val(&items[perm[j]], axis) < axis_val(&items[perm[i]], axis)) tmp[k++] = perm[j++];
        else tmp[k++] = perm[i++];
    }
    while (i < mid) tmp[k++] = perm[i++];
    while (j < len) tmp[k++] = perm[j++];
    memcpy(perm, tmp, sizeof(int) * len);
}

static HeapEnt make_box(const Item *items, const int *perm, int start, int len, int *serial) {
    long long count = 0;
    int best_axis = 0, best_range = 0;
    for (int i = 0; i < len; i++) count += items[perm[start + i]].count;
    for (int axis = 0; axis < 3; axis++) {
        int lo = 99, hi = -1;
        for (int i = 0; i < len; i++) {
            int v = axis_val(&items[perm[start + i]], axis);
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        if (hi - lo > best_range) { best_axis = axis; best_range = hi - lo; }
    }
    HeapEnt e;
    e.neg = -(count * best_range);
    e.serial = (*serial)++;
    e.axis = best_axis; e.start = start; e.len = len;
    return e;
}

/* Quantize w*h RGB pixels to <= 256 colors. pal: 768 bytes, idx: w*h bytes. Returns palette size. */
static int quantize(const uint8_t *img, size_t npix, uint8_t *pal, uint8_t *idx) {
    const int NB = 1 << 18;
    uint32_t *cnt = (uint32_t *)calloc(NB, sizeof(uint32_t));
    uint64_t *sums = (uint64_t *)calloc((size_t)NB * 3, sizeof(uint64_t));
    uint8_t *listed = (uint8_t *)calloc(NB, 1);
    uint8_t *lookup = (uint8_t *)malloc(NB);
    int nitems = 0;
    Item *items = NULL;
    int *perm = NULL, *tmp = NULL;
    int ncol = -1;
    if (!cnt || !sums || !listed || !lookup) goto done;
    for (size_t i = 0; i < npix; i++) {
        int r = img[3 * i], g = img[3 * i + 1], b = img[3 * i + 2];
        int key = ((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2);
        if (cnt[key] == 0) nitems++;
        cnt[key]++;
        sums[3 * (size_t)key] += r; sums[3 * (size_t)key + 1] += g; sums[3 * (size_t)key + 2] += b;
    }
    /* insertion order = first appearance in raster order, like a Python dict */
    items = (Item *)malloc(sizeof(Item) * nitems);
    perm = (int *)malloc(sizeof(int) * nitems);
    tmp = (int *)malloc(sizeof(int) * nitems);
    if (!items || !perm || !tmp) goto done;
    {
        int n = 0;
        for (size_t i = 0; i < npix; i++) {
            int key = ((img[3 * i] >> 2) << 12) | ((img[3 * i + 1] >> 2) << 6) | (img[3 * i + 2] >> 2);
            if (!listed[key]) {
                listed[key] = 1;
                items[n].key = key;
                items[n].count = (int)cnt[key];
                items[n].r = (long long)sums[3 * (size_t)key];
                items[n].g = (long long)sums[3 * (size_t)key + 1];
                items[n].b = (long long)sums[3 * (size_t)key + 2];
                perm[n] = n;
                n++;
            }
        }
    }
    HeapEnt heap[257];
    int hn = 0, serial = 0;
    heap_push(heap, &hn, make_box(items, perm, 0, nitems, &serial));
    while (hn < 256) {
        HeapEnt top = heap[0];
        if (top.neg == 0) break;
        heap_pop(heap, &hn);
        msort(perm + top.start, tmp, items, top.len, top.axis);
        long long total = 0;
        for (int i = 0; i < top.len; i++) total += items[perm[top.start + i]].count;
        long long acc = 0;
        int cut = 1;
        for (int n = 0; n < top.len; n++) {
            acc += items[perm[top.start + n]].count;
            if (2 * acc >= total) {
                cut = n + 1;
                if (cut < 1) cut = 1;
                if (cut > top.len - 1) cut = top.len - 1;
                break;
            }
        }
        heap_push(heap, &hn, make_box(items, perm, top.start, cut, &serial));
        heap_push(heap, &hn, make_box(items, perm, top.start + cut, top.len - cut, &serial));
    }
    for (int index = 0; index < hn; index++) {
        long long n = 0, r = 0, g = 0, b = 0;
        for (int i = 0; i < heap[index].len; i++) {
            const Item *it = &items[perm[heap[index].start + i]];
            n += it->count; r += it->r; g += it->g; b += it->b;
            lookup[it->key] = (uint8_t)index;
        }
        pal[3 * index] = (uint8_t)(r / n); pal[3 * index + 1] = (uint8_t)(g / n); pal[3 * index + 2] = (uint8_t)(b / n);
    }
    for (size_t i = 0; i < npix; i++)
        idx[i] = lookup[((img[3 * i] >> 2) << 12) | ((img[3 * i + 1] >> 2) << 6) | (img[3 * i + 2] >> 2)];
    ncol = hn;
done:
    free(cnt); free(sums); free(listed); free(lookup); free(items); free(perm); free(tmp);
    return ncol;
}

static void rle(Buf *o, const uint8_t *a, int n) {
    int i = 0;
    while (i < n) {
        int j = i;
        while (j < n && a[j] == a[i]) j++;
        int run = j - i;
        char ch = (char)(63 + a[i]);
        if (run > 3) { buf_byte(o, '!'); buf_int(o, run); buf_byte(o, (uint8_t)ch); }
        else for (int k = 0; k < run; k++) buf_byte(o, (uint8_t)ch);
        i = j;
    }
}

EXPORT int render_sixel(const uint8_t *img, int w, int h, uint8_t **out, size_t *out_len) {
    uint8_t pal[768];
    uint8_t *idx = (uint8_t *)malloc((size_t)w * h);
    uint8_t *cols = (uint8_t *)malloc((size_t)256 * w);
    Buf o = {0};
    int rc = -1;
    if (!idx || !cols) goto done;
    int ncol = quantize(img, (size_t)w * h, pal, idx);
    if (ncol < 0) goto done;
    buf_put(&o, "\x1bP0;1;0q\"1;1;", strlen("\x1bP0;1;0q\"1;1;"));
    buf_int(&o, w); buf_byte(&o, ';'); buf_int(&o, h);
    for (int i = 0; i < ncol; i++) {
        buf_byte(&o, '#'); buf_int(&o, i); buf_put(&o, ";2;", 3);
        for (int c = 0; c < 3; c++) {
            buf_int(&o, (200L * pal[3 * i + c] + 255) / 510);
            if (c < 2) buf_byte(&o, ';');
        }
    }
    for (int y0 = 0; y0 < h; y0 += 6) {
        int order[256], used[256] = {0}, nord = 0;
        int rows = h - y0 < 6 ? h - y0 : 6;
        for (int dy = 0; dy < rows; dy++) {
            const uint8_t *src = idx + (size_t)(y0 + dy) * w;
            for (int x = 0; x < w; x++) {
                int ci = src[x];
                if (!used[ci]) { used[ci] = 1; order[nord++] = ci; memset(cols + (size_t)ci * w, 0, w); }
                cols[(size_t)ci * w + x] |= (uint8_t)(1 << dy);
            }
        }
        for (int k = 0; k < nord; k++) {
            int ci = order[k];
            const uint8_t *col = cols + (size_t)ci * w;
            int n = w;
            while (n > 0 && col[n - 1] == 0) n--;
            if (k) buf_byte(&o, '$');
            buf_byte(&o, '#'); buf_int(&o, ci);
            /* the column holds bit masks 0..63 */
            rle(&o, col, n);
        }
        buf_byte(&o, '-');
    }
    buf_put(&o, "\x1b\\", 2);
    if (o.err) goto done;
    *out = o.p; *out_len = o.n; o.p = NULL;
    rc = 0;
done:
    free(idx); free(cols); free(o.p);
    return rc;
}

/* ------------------------------------------------------------------------- */
/* 5. GIF: LZW + frame composition                                           */
/* ------------------------------------------------------------------------- */
/*
 * Decodes LZW data into 'out' (at most 'limit' bytes). Returns the number of bytes
 * produced, or -1 when the stream is corrupt.
 */
EXPORT long gif_lzw(const uint8_t *data, size_t n, int min_code, uint8_t *out, size_t limit) {
    int clear = 1 << min_code, eoi = clear + 1, first_size = min_code + 1;
    static const int MAXC = 4096;
    int16_t prefix[4096];
    uint8_t suffix[4096], first[4096];
    uint16_t length[4096];
    uint8_t tmp[4097];
    for (int i = 0; i < clear; i++) { prefix[i] = -1; suffix[i] = (uint8_t)i; first[i] = (uint8_t)i; length[i] = 1; }
    int size = first_size, nxt = eoi + 1, prev = -1;
    unsigned mask = (1u << size) - 1;
    uint64_t buf = 0;
    int bits = 0;
    size_t produced = 0;
    for (size_t pos = 0; pos < n; pos++) {
        buf |= (uint64_t)data[pos] << bits;
        bits += 8;
        while (bits >= size) {
            int code = (int)(buf & mask);
            buf >>= size; bits -= size;
            if (code == clear) { size = first_size; mask = (1u << size) - 1; nxt = eoi + 1; prev = -1; continue; }
            if (code == eoi) return (long)produced;
            int emit;
            int extra = 0;
            if (prev < 0) {
                if (code >= clear) return -1;
                emit = code;
            } else if (code < nxt) {
                emit = code;
            } else if (code == nxt) {
                emit = prev; extra = 1;                       /* KwKwK: prev + prev[0] */
            } else return -1;
            int len = length[emit] + extra;
            uint8_t *dst = (produced + (size_t)len <= limit) ? out + produced : tmp;
            {
                int p = emit;
                for (int i = length[emit] - 1; i >= 0; i--) { dst[i] = suffix[p]; p = prefix[p]; }
                if (extra) dst[len - 1] = first[emit];
            }
            if (dst == tmp) { size_t room = limit - produced; memcpy(out + produced, tmp, room); produced = limit; }
            else produced += (size_t)len;
            if (prev >= 0 && nxt < MAXC) {
                prefix[nxt] = (int16_t)prev;
                suffix[nxt] = extra ? first[prev] : first[emit];
                first[nxt] = first[prev];
                length[nxt] = (uint16_t)(length[prev] + 1);
                nxt++;
                if (nxt == (1 << size) && size < 12) { size++; mask = (1u << size) - 1; }
            }
            prev = extra ? nxt - 1 : emit;
            if (produced >= limit) return (long)produced;
        }
    }
    return (long)produced;
}

/* Paint one rectangle of an RGB canvas with one color (disposal method 2). */
EXPORT void gif_fill(uint8_t *canvas, int W, int H, int x0, int y0, int x1, int y1, int r, int g, int b) {
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > W) x1 = W;
    if (y1 > H) y1 = H;
    for (int y = y0; y < y1; y++) {
        uint8_t *p = canvas + ((size_t)y * W + x0) * 3;
        for (int x = x0; x < x1; x++, p += 3) { p[0] = (uint8_t)r; p[1] = (uint8_t)g; p[2] = (uint8_t)b; }
    }
}

/* Draw one decoded frame (palette indexes) on the canvas. trans < 0 = no transparency. */
EXPORT void gif_draw(uint8_t *canvas, int W, int H, const uint8_t *idx, int left, int top, int fw, int fh,
                     int interlaced, const uint8_t *pal, int trans) {
    int x0 = left, x1 = left + fw < W ? left + fw : W;
    if (x1 <= x0) return;
    int span = x1 - x0;
    int k = 0;
    int passes = interlaced ? 4 : 1;
    static const int start[4] = {0, 4, 2, 1}, step[4] = {8, 8, 4, 2};
    for (int ps = 0; ps < passes; ps++) {
        int s = interlaced ? start[ps] : 0, st = interlaced ? step[ps] : 1;
        for (int y = s; y < fh; y += st, k++) {
            int cy = top + y;
            if (cy >= H) continue;
            const uint8_t *line = idx + (size_t)k * fw;
            uint8_t *dst = canvas + ((size_t)cy * W + x0) * 3;
            for (int x = 0; x < span; x++, dst += 3) {
                int i = line[x];
                if (i == trans) continue;
                dst[0] = pal[3 * i]; dst[1] = pal[3 * i + 1]; dst[2] = pal[3 * i + 2];
            }
        }
    }
}

/* ------------------------------------------------------------------------- */
/* 6. JPEG                                                                   */
/* ------------------------------------------------------------------------- */
static const uint8_t ZZ[64] = {0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48,
                               41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15,
                               23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63};

/* 16-bit lookup: table[next 16 bits] = (code length << 8) | symbol. 0 on success. */
EXPORT int jpeg_huffman_table(const uint8_t *counts, const uint8_t *symbols, int nsym, uint16_t *table) {
    memset(table, 0, 65536 * sizeof(uint16_t));
    int code = 0, k = 0;
    for (int length = 1; length <= 16; length++) {
        for (int i = 0; i < counts[length - 1]; i++) {
            if (k >= nsym) return -1;
            int lo = code << (16 - length), hi = (code + 1) << (16 - length);
            if (hi > 65536) return -1;
            uint16_t e = (uint16_t)((length << 8) | symbols[k]);
            for (int j = lo; j < hi; j++) table[j] = e;
            code++; k++;
        }
        code <<= 1;
    }
    return 0;
}

typedef struct {
    int h, v, bw, bh, bwm, bhm;
    int16_t *coef;
    int pred;
    const uint16_t *dc, *ac;
} JComp;

typedef struct {
    const uint8_t *data;
    size_t len;
    size_t p;
    uint64_t b;
    int c;
    int eobrun;
    int err;
} BitSt;

static inline uint32_t next32(const BitSt *s, size_t p) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v = (v << 8) | (p + i < s->len ? s->data[p + i] : 0);
    return v;
}

#define REFILL()                                                              \
    if (c < 32) {                                                             \
        b = ((b & ((1ULL << c) - 1)) << 32) | next32(st, p);                  \
        c += 32;                                                              \
        p += 4;                                                               \
    }
#define PEEK16(tab) ((tab)[(b >> (c - 16)) & 0xFFFF])

static void j_baseline(BitSt *st, JComp *comp, int off) {
    int16_t *coef = comp->coef;
    const uint16_t *dc = comp->dc, *ac = comp->ac;
    size_t p = st->p;
    uint64_t b = st->b;
    int c = st->c;
    REFILL();
    unsigned e = PEEK16(dc);
    c -= e >> 8;
    int s = e & 255;
    if (s > 16) { st->err = 1; return; }
    if (s) {
        int v = (int)((b >> (c - s)) & ((1ULL << s) - 1));
        c -= s;
        comp->pred += v >= (1 << (s - 1)) ? v : v - (1 << s) + 1;
    }
    coef[off] = (int16_t)comp->pred;
    int k = 1;
    while (k < 64) {
        REFILL();
        e = PEEK16(ac);
        c -= e >> 8;
        int rs = e & 255;
        s = rs & 15;
        if (s == 0) {
            if (rs != 0xF0) break;
            k += 16;
            continue;
        }
        k += rs >> 4;
        if (k > 63) break;
        int v = (int)((b >> (c - s)) & ((1ULL << s) - 1));
        c -= s;
        coef[off + ZZ[k]] = (int16_t)(v >= (1 << (s - 1)) ? v : v - (1 << s) + 1);
        k++;
    }
    st->p = p; st->b = b; st->c = c;
}

static void j_dc_first(BitSt *st, JComp *comp, int off, int al) {
    const uint16_t *dc = comp->dc;
    size_t p = st->p;
    uint64_t b = st->b;
    int c = st->c;
    REFILL();
    unsigned e = PEEK16(dc);
    c -= e >> 8;
    int s = e & 255;
    if (s > 16) { st->err = 1; return; }
    if (s) {
        int v = (int)((b >> (c - s)) & ((1ULL << s) - 1));
        c -= s;
        comp->pred += v >= (1 << (s - 1)) ? v : v - (1 << s) + 1;
    }
    comp->coef[off] = (int16_t)(comp->pred * (1 << al));
    st->p = p; st->b = b; st->c = c;
}

static void j_dc_refine(BitSt *st, JComp *comp, int off, int al) {
    size_t p = st->p;
    uint64_t b = st->b;
    int c = st->c;
    REFILL();
    c -= 1;
    if ((b >> c) & 1) comp->coef[off] |= (int16_t)(1 << al);
    st->p = p; st->b = b; st->c = c;
}

static void j_ac_first(BitSt *st, JComp *comp, int off, int ss, int se, int al) {
    if (st->eobrun > 0) { st->eobrun--; return; }
    int16_t *coef = comp->coef;
    const uint16_t *ac = comp->ac;
    size_t p = st->p;
    uint64_t b = st->b;
    int c = st->c;
    int k = ss;
    while (k <= se) {
        REFILL();
        unsigned e = PEEK16(ac);
        c -= e >> 8;
        int rs = e & 255, s = rs & 15, r = rs >> 4;
        if (s == 0) {
            if (r < 15) {
                int n = 1 << r;
                if (r) { n += (int)((b >> (c - r)) & ((1ULL << r) - 1)); c -= r; }
                st->eobrun = n - 1;
                break;
            }
            k += 16;
            continue;
        }
        k += r;
        if (k > se) break;
        int v = (int)((b >> (c - s)) & ((1ULL << s) - 1));
        c -= s;
        if (v < (1 << (s - 1))) v -= (1 << s) - 1;
        coef[off + ZZ[k]] = (int16_t)(v * (1 << al));
        k++;
    }
    st->p = p; st->b = b; st->c = c;
}

static void j_ac_refine(BitSt *st, JComp *comp, int off, int ss, int se, int al) {
    int16_t *coef = comp->coef;
    const uint16_t *ac = comp->ac;
    int p1 = 1 << al, m1 = -p1;
    size_t p = st->p;
    uint64_t b = st->b;
    int c = st->c;
    int k = ss;
    if (st->eobrun == 0) {
        while (k <= se) {
            REFILL();
            unsigned e = PEEK16(ac);
            c -= e >> 8;
            int rs = e & 255, r = rs >> 4, value = 0;
            if (rs & 15) {
                c -= 1;
                value = ((b >> c) & 1) ? p1 : m1;
            } else if (r != 15) {
                int n = 1 << r;
                if (r) { n += (int)((b >> (c - r)) & ((1ULL << r) - 1)); c -= r; }
                st->eobrun = n;
                break;
            }
            while (k <= se) {
                REFILL();
                int z = off + ZZ[k];
                int v = coef[z];
                if (v) {
                    c -= 1;
                    if (((b >> c) & 1) && !(v & p1)) coef[z] = (int16_t)(v >= 0 ? v + p1 : v + m1);
                } else {
                    r -= 1;
                    if (r < 0) break;
                }
                k++;
            }
            if (value && k <= se) coef[off + ZZ[k]] = (int16_t)value;
            k++;
        }
    }
    if (st->eobrun > 0) {
        while (k <= se) {
            REFILL();
            int z = off + ZZ[k];
            int v = coef[z];
            if (v) {
                c -= 1;
                if (((b >> c) & 1) && !(v & p1)) coef[z] = (int16_t)(v >= 0 ? v + p1 : v + m1);
            }
            k++;
        }
        st->eobrun--;
    }
    st->p = p; st->b = b; st->c = c;
}

/*
 * Entropy-decode one scan. 'data' holds all restart intervals (already unstuffed and
 * concatenated), lens[i] is the size of interval i. 'sel' lists the components of
 * the scan (indexes into comps). Returns 0, or -1 on corrupt input.
 */
EXPORT int jpeg_scan(const uint8_t *data, const int *lens, int nchunks, int mcus_x, int mcus_y,
                     JComp *comps, const int *sel, int nsel, int ri, int progressive,
                     int ss, int se, int ah, int al) {
    if (nsel < 1 || nsel > 4) return -1;
    if (se > 63 || ss > se || ss < 0 || al > 13) return -1;
    JComp *first = &comps[sel[0]];
    int single = nsel == 1;
    for (int i = 0; i < nsel; i++) {
        JComp *sc = &comps[sel[i]];
        if (!progressive) { if (!sc->dc || !sc->ac) return -1; }
        else if (ss == 0) { if (ah == 0 && !sc->dc) return -1; }
        else if (!sc->ac) return -1;
    }
    long total = single ? (long)first->bw * first->bh : (long)mcus_x * mcus_y;
    long interval = ri ? ri : total;
    long mcu = 0;
    size_t base = 0;
    BitSt st;
    for (int ch = 0; ch < nchunks; ch++) {
        if (mcu >= total) break;
        st.data = data + base; st.len = (size_t)lens[ch]; st.p = 0; st.b = 0; st.c = 0; st.eobrun = 0; st.err = 0;
        base += (size_t)lens[ch];
        for (int i = 0; i < nsel; i++) comps[sel[i]].pred = 0;
        long end = mcu + interval < total ? mcu + interval : total;
#define HANDLE(sc, off)                                                                   \
        do {                                                                              \
            if (!progressive) j_baseline(&st, sc, off);                                   \
            else if (ss == 0) { if (ah == 0) j_dc_first(&st, sc, off, al); else j_dc_refine(&st, sc, off, al); } \
            else if (ah == 0) j_ac_first(&st, sc, off, ss, se, al);                       \
            else j_ac_refine(&st, sc, off, ss, se, al);                                   \
        } while (0)
        if (single) {
            while (mcu < end) {
                long row = mcu / first->bw, col = mcu % first->bw;
                HANDLE(first, (int)((row * first->bwm + col) * 64));
                if (st.err) return -1;
                mcu++;
            }
        } else {
            while (mcu < end) {
                int mrow = (int)(mcu / mcus_x), mcol = (int)(mcu % mcus_x);
                for (int i = 0; i < nsel; i++) {
                    JComp *sc = &comps[sel[i]];
                    for (int j = 0; j < sc->v; j++) {
                        int bs = (mrow * sc->v + j) * sc->bwm + mcol * sc->h;
                        for (int ii = 0; ii < sc->h; ii++) HANDLE(sc, (bs + ii) * 64);
                    }
                }
                if (st.err) return -1;
                mcu++;
            }
        }
    }
    return 0;
}

typedef struct { double K[8]; double m2[4]; double m4[16]; } JConst;

static inline void idct8(double *o, double x0, double x1, double x2, double x3, double x4, double x5, double x6,
                         double x7, const double *K) {
    double K1 = K[1], K2 = K[2], K3 = K[3], K4 = K[4], K5 = K[5], K6 = K[6], K7 = K[7];
    double a = x0 * K4;
    double e0 = a + x2 * K2 + x4 * K4 + x6 * K6;
    double e1 = a + x2 * K6 - x4 * K4 - x6 * K2;
    double e2 = a - x2 * K6 - x4 * K4 + x6 * K2;
    double e3 = a - x2 * K2 + x4 * K4 - x6 * K6;
    double o0 = x1 * K1 + x3 * K3 + x5 * K5 + x7 * K7;
    double o1 = x1 * K3 - x3 * K7 - x5 * K1 - x7 * K5;
    double o2 = x1 * K5 - x3 * K1 + x5 * K7 + x7 * K3;
    double o3 = x1 * K7 - x3 * K5 + x5 * K3 - x7 * K1;
    o[0] = e0 + o0; o[1] = e1 + o1; o[2] = e2 + o2; o[3] = e3 + o3;
    o[4] = e3 - o3; o[5] = e2 - o2; o[6] = e1 - o1; o[7] = e0 - o0;
}

static inline uint8_t to_byte(double v) {
    double t = v + 128.5;
    if (t < 0) return 0;
    if (t >= 255) return 255;
    return (uint8_t)(int)t;
}

/* Dequantize + inverse DCT of one block into an n x n square of 'dst' (n = 8, 4, 2, 1). */
static void jblock(const int16_t *blk, const int *qt, int n, uint8_t *dst, int stride, const JConst *kc) {
    int flat = 1;
    for (int i = 1; i < 64; i++) if (blk[i]) { flat = 0; break; }
    if (n == 1 || flat) {
        uint8_t v = to_byte((double)(blk[0] * qt[0]) / 8);
        for (int y = 0; y < n; y++) memset(dst + (size_t)y * stride, v, n);
        return;
    }
    if (n == 8) {
        double tmp[8][8], col[8], res[8];
        for (int v = 0; v < 8; v++) {
            double q[8];
            int any = 0;
            for (int u = 0; u < 8; u++) { q[u] = (double)(blk[8 * v + u] * qt[8 * v + u]); if (u && q[u] != 0) any = 1; }
            if (any) idct8(tmp[v], q[0], q[1], q[2], q[3], q[4], q[5], q[6], q[7], kc->K);
            else for (int x = 0; x < 8; x++) tmp[v][x] = q[0] * kc->K[4];
        }
        for (int x = 0; x < 8; x++) {
            for (int v = 0; v < 8; v++) col[v] = tmp[v][x];
            idct8(res, col[0], col[1], col[2], col[3], col[4], col[5], col[6], col[7], kc->K);
            for (int y = 0; y < 8; y++) dst[(size_t)y * stride + x] = to_byte(res[y]);
        }
        return;
    }
    const double *m = n == 4 ? kc->m4 : kc->m2;
    double f[4][4], t[4][4];
    for (int v = 0; v < n; v++) for (int u = 0; u < n; u++) f[v][u] = (double)(blk[8 * v + u] * qt[8 * v + u]);
    for (int v = 0; v < n; v++)
        for (int x = 0; x < n; x++) {
            double s = m[x * n + 0] * f[v][0];
            for (int u = 1; u < n; u++) s = s + m[x * n + u] * f[v][u];
            t[v][x] = s;
        }
    for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++) {
            double s = m[y * n + 0] * t[0][x];
            for (int v = 1; v < n; v++) s = s + m[y * n + v] * t[v][x];
            dst[(size_t)y * stride + x] = to_byte(s);
        }
}

static inline uint8_t clamp_idx(int v) { return v < 0 ? 0 : v > 255 ? 255 : (uint8_t)v; }

/*
 * IDCT of every component, chroma upsampling and color conversion.
 * qts: ncomp tables of 64 ints (natural order). mode: 0 = one component, 1 = plain RGB, 2 = YCbCr.
 * out: out_w*out_h*3 bytes. Returns 0 or -1.
 */
EXPORT int jpeg_output(const JComp *comps, int ncomp, const int *qts, int n, int out_w, int out_h, int hmax,
                       int vmax, int mode, const JConst *kc, const int *cr_r, const int *cb_b,
                       const int *cb_g, const int *cr_g, uint8_t *out) {
    uint8_t *planes[4] = {0};
    int pw[4], ph[4];
    int rc = -1;
    for (int ci = 0; ci < ncomp; ci++) {
        const JComp *cp = &comps[ci];
        pw[ci] = cp->bwm * n; ph[ci] = cp->bhm * n;
        planes[ci] = (uint8_t *)malloc((size_t)pw[ci] * ph[ci]);
        if (!planes[ci]) goto done;
        for (int br = 0; br < cp->bhm; br++)
            for (int bc = 0; bc < cp->bwm; bc++)
                jblock(cp->coef + ((size_t)br * cp->bwm + bc) * 64, qts + 64 * ci, n,
                       planes[ci] + (size_t)br * n * pw[ci] + (size_t)bc * n, pw[ci], kc);
    }
    for (int y = 0; y < out_h; y++) {
        const uint8_t *src[4];
        int sx_mul[4];
        for (int ci = 0; ci < ncomp; ci++) {
            int sy = y * comps[ci].v / vmax;
            if (sy >= ph[ci]) sy = ph[ci] - 1;
            src[ci] = planes[ci] + (size_t)sy * pw[ci];
            sx_mul[ci] = comps[ci].h;
        }
        uint8_t *o = out + (size_t)y * out_w * 3;
        for (int x = 0; x < out_w; x++, o += 3) {
            int v[4] = {0, 0, 0, 0};
            for (int ci = 0; ci < ncomp; ci++) {
                int sx = x * sx_mul[ci] / hmax;
                if (sx >= pw[ci]) sx = pw[ci] - 1;
                v[ci] = src[ci][sx];
            }
            if (mode == 0) { o[0] = o[1] = o[2] = (uint8_t)v[0]; }
            else if (mode == 1) { o[0] = (uint8_t)v[0]; o[1] = (uint8_t)v[1]; o[2] = (uint8_t)v[2]; }
            else {
                int Y = v[0], cb = v[1], cr = v[2];
                o[0] = clamp_idx(Y + cr_r[cr]);
                o[1] = clamp_idx(Y + cb_g[cb] + cr_g[cr]);
                o[2] = clamp_idx(Y + cb_b[cb]);
            }
        }
    }
    rc = 0;
done:
    for (int ci = 0; ci < 4; ci++) free(planes[ci]);
    return rc;
}

/* ------------------------------------------------------------------------- */
/* 7. helper: "raw PNG" scanlines (filter byte 0 + RGB) for kitty / iterm2   */
/* ------------------------------------------------------------------------- */
EXPORT void png_raw_rows(const uint8_t *rgb, int w, int h, uint8_t *out) {
    size_t stride = (size_t)w * 3;
    for (int y = 0; y < h; y++) {
        out[(size_t)y * (stride + 1)] = 0;
        memcpy(out + (size_t)y * (stride + 1) + 1, rgb + (size_t)y * stride, stride);
    }
}

/* ------------------------------------------------------------------------- */
/* 8. EXIF orientation: move pixels so the picture is shown upright          */
/* ------------------------------------------------------------------------- */
/*
 * o is the EXIF orientation (2..8; 1 would be a plain copy). The output is h x w
 * for 5..8 (turned a quarter) and w x h otherwise. Pure pixel moves, so the result
 * is identical to the pure Python code in _apply_orientation().
 */
EXPORT int orient_rgb(const uint8_t *in, int w, int h, int o, uint8_t *out) {
    if (o < 1 || o > 8) return -1;
    int dw = o >= 5 ? h : w, dh = o >= 5 ? w : h;
    for (int y = 0; y < dh; y++) {
        for (int x = 0; x < dw; x++) {
            int sx, sy;
            switch (o) {
            case 1: sx = x;         sy = y;         break;
            case 2: sx = w - 1 - x; sy = y;         break;
            case 3: sx = w - 1 - x; sy = h - 1 - y; break;
            case 4: sx = x;         sy = h - 1 - y; break;
            case 5: sx = y;         sy = x;         break;
            case 6: sx = y;         sy = h - 1 - x; break;
            case 7: sx = w - 1 - y; sy = h - 1 - x; break;
            default: sx = w - 1 - y; sy = x;        break;
            }
            memcpy(out + ((size_t)y * dw + x) * 3, in + ((size_t)sy * w + sx) * 3, 3);
        }
    }
    return 0;
}
