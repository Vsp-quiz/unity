"""Pure-Python ASTC LDR decoder (2D, all block sizes, partitions, dual-plane).
Exact unquantisation tables from ARM astc-encoder; HDR endpoint modes -> magenta.
Returns RGBA uint8 numpy array (h, w, 4)."""
import numpy as np

from _astc_tables import COLOR_UQ, WEIGHT_UQ

QLEVELS = [2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 192, 256]
# (bits, trits, quints) for each level
_BTQ = {2: (1, 0, 0), 3: (0, 1, 0), 4: (2, 0, 0), 5: (0, 0, 1), 6: (1, 1, 0), 8: (3, 0, 0), 10: (1, 0, 1),
        12: (2, 1, 0), 16: (4, 0, 0), 20: (2, 0, 1), 24: (3, 1, 0), 32: (5, 0, 0), 40: (3, 0, 1),
        48: (4, 1, 0), 64: (6, 0, 0), 80: (4, 0, 1), 96: (5, 1, 0), 128: (7, 0, 0), 160: (5, 0, 1),
        192: (6, 1, 0), 256: (8, 0, 0)}
WEIGHT_LEVELS = [2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32]


def ise_bits(n, level):
    b, t, q = _BTQ[level]
    return n * b + ((8 * n + 4) // 5 if t else 0) + ((7 * n + 2) // 3 if q else 0)


def _rd(v, pos, n):
    return (v >> pos) & ((1 << n) - 1) if n > 0 else 0


def _trits(T):
    if ((T >> 2) & 7) == 7:
        C = (((T >> 5) & 7) << 2) | (T & 3)
        t4 = t3 = 2
    else:
        C = T & 0x1F
        if ((T >> 5) & 3) == 3:
            t4, t3 = 2, (T >> 7) & 1
        else:
            t4, t3 = (T >> 7) & 1, (T >> 5) & 3
    if (C & 3) == 3:
        t2, t1, t0 = 2, (C >> 4) & 1, (((C >> 3) & 1) << 1) | (((C >> 2) & 1) & (~(C >> 3) & 1))
    elif ((C >> 2) & 3) == 3:
        t2, t1, t0 = 2, 2, C & 3
    else:
        t2, t1, t0 = (C >> 4) & 1, (C >> 2) & 3, (((C >> 1) & 1) << 1) | ((C & 1) & (~(C >> 1) & 1))
    return [t0, t1, t2, t3, t4]


def _quints(Q):
    if ((Q >> 1) & 3) == 3 and ((Q >> 5) & 3) == 0:
        q2 = ((Q & 1) << 2) | (((Q >> 4) & 1) & (~Q & 1)) << 1 | (((Q >> 3) & 1) & (~Q & 1))
        return [4, 4, q2]
    if ((Q >> 1) & 3) == 3:
        q2 = 4
        C = (((Q >> 3) & 3) << 3) | ((~(Q >> 5) & 3) << 1) | (Q & 1)
    else:
        q2 = (Q >> 5) & 3
        C = Q & 0x1F
    if (C & 7) == 5:
        q1, q0 = 4, (C >> 3) & 3
    else:
        q1, q0 = (C >> 3) & 3, C & 7
    return [q0, q1, q2]


def ise_decode(stream, pos, n, level):
    """Read n ISE values (quant index 0..level-1) from int `stream` starting at bit pos."""
    b, t, q = _BTQ[level]
    out = []
    if not t and not q:
        for _ in range(n):
            out.append(_rd(stream, pos, b))
            pos += b
        return out
    if t:
        cnt, tb, chunks = 5, 8, (2, 2, 1, 2, 1)
    else:
        cnt, tb, chunks = 3, 7, (3, 2, 2)
    while len(out) < n:
        m, hi, sh = [], 0, 0
        k = min(cnt, n - len(out))
        for i in range(k):
            m.append(_rd(stream, pos, b))
            pos += b
            c = chunks[i]
            hi |= _rd(stream, pos, c) << sh
            pos += c
            sh += c
        dec = _trits(hi) if t else _quints(hi)
        for i in range(k):
            out.append(dec[i] * (1 << b) + m[i])
    return out


def _hash52(p):
    M = 0xFFFFFFFF
    p ^= p >> 15
    p = (p - (p << 17)) & M
    p = (p + (p << 7)) & M
    p = (p + (p << 4)) & M
    p ^= p >> 5
    p = (p + (p << 16)) & M
    p ^= p >> 7
    p ^= p >> 3
    p = (p ^ (p << 6)) & M
    p ^= p >> 17
    return p


_part_cache = {}


def partition_map(seed, n, bw, bh):
    key = (seed, n, bw, bh)
    r = _part_cache.get(key)
    if r is not None:
        return r
    small = bw * bh < 31
    s = seed + (n - 1) * 1024
    rnum = _hash52(s)
    sd = [(rnum >> (4 * i)) & 0xF for i in range(8)]
    sd += [(rnum >> 18) & 0xF, (rnum >> 22) & 0xF, (rnum >> 26) & 0xF, (((rnum >> 30) | (rnum << 2)) & 0xF)]
    sd = [x * x for x in sd]
    if s & 1:
        sh1 = 4 if s & 2 else 5
        sh2 = 6 if n == 3 else 5
    else:
        sh1 = 6 if n == 3 else 5
        sh2 = 4 if s & 2 else 5
    sh3 = sh1 if s & 0x10 else sh2
    sd = [sd[0] >> sh1, sd[1] >> sh2, sd[2] >> sh1, sd[3] >> sh2, sd[4] >> sh1, sd[5] >> sh2, sd[6] >> sh1,
          sd[7] >> sh2, sd[8] >> sh3, sd[9] >> sh3, sd[10] >> sh3, sd[11] >> sh3]
    res = []
    for y in range(bh):
        for x in range(bw):
            xx, yy = (x << 1, y << 1) if small else (x, y)
            a = (sd[0] * xx + sd[1] * yy + (rnum >> 14)) & 0x3F
            b = (sd[2] * xx + sd[3] * yy + (rnum >> 10)) & 0x3F
            c = (sd[4] * xx + sd[5] * yy + (rnum >> 6)) & 0x3F
            d = (sd[6] * xx + sd[7] * yy + (rnum >> 2)) & 0x3F
            if n < 4:
                d = 0
            if n < 3:
                c = 0
            if a >= b and a >= c and a >= d:
                res.append(0)
            elif b >= c and b >= d:
                res.append(1)
            elif c >= d:
                res.append(2)
            else:
                res.append(3)
    _part_cache[key] = res
    return res


def _block_mode(bm):
    """-> (wx, wy, dual, weight_level_index) or None"""
    R0 = (bm >> 4) & 1
    H = (bm >> 9) & 1
    D = (bm >> 10) & 1
    A = (bm >> 5) & 3
    if bm & 3:
        R = R0 | ((bm & 3) << 1)
        B = (bm >> 7) & 3
        k = (bm >> 2) & 3
        if k == 0:
            wx, wy = B + 4, A + 2
        elif k == 1:
            wx, wy = B + 8, A + 2
        elif k == 2:
            wx, wy = A + 2, B + 8
        else:
            B &= 1
            if bm & 0x100:
                wx, wy = B + 2, A + 2
            else:
                wx, wy = A + 2, B + 6
    else:
        R = R0 | (((bm >> 2) & 3) << 1)
        if ((bm >> 2) & 3) == 0:
            return None
        B = (bm >> 9) & 3
        k = (bm >> 7) & 3
        if k == 0:
            wx, wy = 12, A + 2
        elif k == 1:
            wx, wy = A + 2, 12
        elif k == 2:
            wx, wy, D, H = A + 6, B + 6, 0, 0
        else:
            if A == 0:
                wx, wy = 6, 10
            elif A == 1:
                wx, wy = 10, 6
            else:
                return None
    qi = (R - 2) + 6 * H
    if qi < 0 or qi > 11:
        return None
    return wx, wy, D, qi


def _clamp(v):
    return 0 if v < 0 else 255 if v > 255 else v


def _bts(a, b):
    b >>= 1
    b |= a & 0x80
    a >>= 1
    a &= 0x3F
    if a & 0x20:
        a -= 0x40
    return a, b


def _bc(r, g, b, a):
    return ((r + b) >> 1, (g + b) >> 1, b, a)


def _endpoints(cem, v):
    if cem == 0:
        return (v[0],) * 3 + (255,), (v[1],) * 3 + (255,)
    if cem == 1:
        l0 = (v[0] >> 2) | (v[1] & 0xC0)
        l1 = min(255, l0 + (v[1] & 0x3F))
        return (l0,) * 3 + (255,), (l1,) * 3 + (255,)
    if cem == 4:
        return (v[0],) * 3 + (v[2],), (v[1],) * 3 + (v[3],)
    if cem == 5:
        o, b = _bts(v[1], v[0])
        ao, ab = _bts(v[3], v[2])
        return (b,) * 3 + (ab,), (_clamp(b + o),) * 3 + (_clamp(ab + ao),)
    if cem == 6:
        s = v[3]
        return ((v[0] * s) >> 8, (v[1] * s) >> 8, (v[2] * s) >> 8, 255), (v[0], v[1], v[2], 255)
    if cem == 10:
        s = v[3]
        return ((v[0] * s) >> 8, (v[1] * s) >> 8, (v[2] * s) >> 8, v[4]), (v[0], v[1], v[2], v[5])
    if cem in (8, 12):
        a0, a1 = (v[6], v[7]) if cem == 12 else (255, 255)
        if v[1] + v[3] + v[5] >= v[0] + v[2] + v[4]:
            return (v[0], v[2], v[4], a0), (v[1], v[3], v[5], a1)
        return _bc(v[1], v[3], v[5], a1), _bc(v[0], v[2], v[4], a0)
    if cem in (9, 13):
        ro, rb = _bts(v[1], v[0])
        go, gb = _bts(v[3], v[2])
        bo, bb = _bts(v[5], v[4])
        a0 = a1 = 255
        if cem == 13:
            ao, ab = _bts(v[7], v[6])
            a0, a1 = ab, _clamp(ab + ao)
        e0 = (rb, gb, bb, a0)
        e1 = (rb + ro, gb + go, bb + bo, a1)
        if ro + go + bo >= 0:
            return e0, tuple(_clamp(c) for c in e1)
        e0b, e1b = _bc(*e1), _bc(*e0)
        return tuple(_clamp(c) for c in e0b), tuple(_clamp(c) for c in e1b)
    return None  # HDR modes


MAGENTA = [(255, 0, 255, 255)] * 2


def _decode_block(data, bw, bh):
    v = int.from_bytes(data, "little")
    bm = v & 0x7FF
    px = bw * bh
    if (bm & 0x1FF) == 0x1FC:                         # void extent
        if bm & 0x200:
            return [(255, 0, 255, 255)] * px
        c = ((v >> 64) & 0xFFFF) >> 8, ((v >> 80) & 0xFFFF) >> 8, ((v >> 96) & 0xFFFF) >> 8, ((v >> 112) & 0xFFFF) >> 8
        return [c] * px
    bmi = _block_mode(bm)
    if bmi is None:
        return [(255, 0, 255, 255)] * px
    wx, wy, dual, qi = bmi
    wlevel = WEIGHT_LEVELS[qi]
    nweights = wx * wy * (2 if dual else 1)
    if nweights > 64:
        return [(255, 0, 255, 255)] * px
    wbits = ise_bits(nweights, wlevel)
    if wbits < 24 or wbits > 96:
        return [(255, 0, 255, 255)] * px
    nparts = ((v >> 11) & 3) + 1
    high = 0
    if nparts == 1:
        cems = [(v >> 13) & 0xF]
        cstart = 17
        seed = 0
    else:
        seed = (v >> 13) & 0x3FF
        hsz = 3 * nparts - 4
        sel = (v >> 23) & 3
        if sel == 0:
            cems = [(v >> 25) & 0xF] * nparts
            cstart = 29
        else:
            hpos = 128 - wbits - hsz
            enc = ((v >> 23) & 0x3F) | (_rd(v, hpos, hsz) << 6)
            base = (enc & 3) - 1
            cems = []
            for i in range(nparts):
                c = (enc >> 2 >> i) & 1
                m = (enc >> (2 + nparts + 2 * i)) & 3
                cems.append(((base + c) << 2) | m)
            cstart = 29
            high = hsz
    ccs = 0
    dual_bits = 2 if dual else 0
    if dual:
        ccs = _rd(v, 128 - wbits - high - 2, 2)
    nvals = sum(((c >> 2) + 1) * 2 for c in cems)
    avail = 128 - wbits - high - dual_bits - cstart
    clevel = None
    for lv in reversed(QLEVELS):
        if ise_bits(nvals, lv) <= avail:
            clevel = lv
            break
    if clevel is None or nvals > 18:
        return [(255, 0, 255, 255)] * px
    raw = ise_decode(v, cstart, nvals, clevel)
    tbl = COLOR_UQ[clevel]
    cv = [tbl[q] for q in raw]
    eps, k = [], 0
    for c in cems:
        n = ((c >> 2) + 1) * 2
        e = _endpoints(c, cv[k:k + n])
        k += n
        eps.append(e if e is not None else MAGENTA)
    # weights: block bits reversed

    # bit-reverse the whole 128-bit value
    rv = int(bin(v)[2:].zfill(128)[::-1], 2)
    wraw = ise_decode(rv, 0, nweights, wlevel)
    wt = WEIGHT_UQ[wlevel]
    ww = [wt[q] for q in wraw]
    # infill
    Ds = (1024 + bw // 2) // (bw - 1) if bw > 1 else 0
    Dt = (1024 + bh // 2) // (bh - 1) if bh > 1 else 0
    pmap = partition_map(seed, nparts, bw, bh) if nparts > 1 else None
    plane = 2 if dual else 1

    def weight_at(s, t, pl):
        gs = (Ds * s * (wx - 1) + 32) >> 6
        gt = (Dt * t * (wy - 1) + 32) >> 6
        js, fs, jt, ft = gs >> 4, gs & 15, gt >> 4, gt & 15
        i0 = js + jt * wx

        def g(i):
            i = min(i, wx * wy - 1)
            return ww[i * plane + pl]
        w11 = (fs * ft + 8) >> 4
        w10 = ft - w11
        w01 = fs - w11
        w00 = 16 - fs - ft + w11
        return (g(i0) * w00 + g(i0 + 1) * w01 + g(i0 + wx) * w10 + g(i0 + wx + 1) * w11 + 8) >> 4

    out = []
    for y in range(bh):
        for x in range(bw):
            p = pmap[y * bw + x] if pmap else 0
            e0, e1 = eps[p]
            w0 = weight_at(x, y, 0)
            w1 = weight_at(x, y, 1) if dual else w0
            px_ = []
            for ch in range(4):
                w = w1 if (dual and ch == ccs) else w0
                c0, c1 = (e0[ch] << 8) | e0[ch], (e1[ch] << 8) | e1[ch]
                px_.append((((c0 * (64 - w) + c1 * w + 32) >> 6) >> 8) & 0xFF)
            out.append(tuple(px_))
    return out


def decode(data, width, height, bw, bh):
    data = bytes(data)
    nbx, nby = (width + bw - 1) // bw, (height + bh - 1) // bh
    img = np.zeros((nby * bh, nbx * bw, 4), np.uint8)
    cache = {}
    for by in range(nby):
        for bx in range(nbx):
            off = (by * nbx + bx) * 16
            blk = data[off:off + 16]
            if len(blk) < 16:
                continue
            px = cache.get(blk)
            if px is None:
                px = np.array(_decode_block(blk, bw, bh), np.uint8).reshape(bh, bw, 4)
                if len(cache) < 4096:
                    cache[blk] = px
            img[by * bh:(by + 1) * bh, bx * bw:(bx + 1) * bw] = px
    return img[:height, :width]
