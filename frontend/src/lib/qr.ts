/**
 * A small QR code encoder (byte mode, error correction M, versions 1 to 10), so the 2FA setup
 * can show a scannable code without a dependency. Based on the standard algorithm: Reed-Solomon
 * over GF(256), the eight masks and the format/version words.
 *
 * `qrMatrix` returns rows of booleans (true = dark), or null when the text is too long
 * (about 213 bytes): the caller then shows the secret to type in by hand.
 */

// Error correction M, versions 1..10 (index 0 unused).
const ECC_PER_BLOCK = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26];
const BLOCKS = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5];
const MAX_VERSION = 10;

function rawModules(ver: number): number {
  let n = (16 * ver + 128) * ver + 64;
  if (ver >= 2) {
    const a = Math.floor(ver / 7) + 2;
    n -= (25 * a - 10) * a - 55;
    if (ver >= 7) n -= 36;
  }
  return n;
}

const eccLen = (ver: number) => ECC_PER_BLOCK[ver] ?? 0;
const blockCount = (ver: number) => BLOCKS[ver] ?? 1;

const dataCodewords = (ver: number) =>
  Math.floor(rawModules(ver) / 8) - eccLen(ver) * blockCount(ver);

function gfMul(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z;
}

function rsDivisor(degree: number): number[] {
  const r = new Array<number>(degree).fill(0);
  r[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < r.length; j++) {
      r[j] = gfMul(r[j]!, root);
      if (j + 1 < r.length) r[j] = r[j]! ^ r[j + 1]!;
    }
    root = gfMul(root, 2);
  }
  return r;
}

function rsRemainder(data: number[], divisor: number[]): number[] {
  const r = new Array<number>(divisor.length).fill(0);
  for (const b of data) {
    const factor = b ^ (r.shift() as number);
    r.push(0);
    divisor.forEach((c, i) => {
      r[i] = r[i]! ^ gfMul(c, factor);
    });
  }
  return r;
}

function encodeData(bytes: number[], ver: number): number[] {
  const bits: number[] = [];
  const put = (v: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((v >>> i) & 1);
  };
  put(0b0100, 4);
  put(bytes.length, ver < 10 ? 8 : 16);
  for (const b of bytes) put(b, 8);
  const cap = dataCodewords(ver) * 8;
  put(0, Math.min(4, cap - bits.length));
  while (bits.length % 8) bits.push(0);
  const out: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = (v << 1) | (bits[i + j] ?? 0);
    out.push(v);
  }
  for (let pad = 0xec; out.length < dataCodewords(ver); pad ^= 0xec ^ 0x11) out.push(pad);
  return out;
}

function addEccAndInterleave(data: number[], ver: number): number[] {
  const nBlocks = blockCount(ver);
  const eccPer = eccLen(ver);
  const raw = Math.floor(rawModules(ver) / 8);
  const nShort = nBlocks - (raw % nBlocks);
  const shortLen = Math.floor(raw / nBlocks);
  const divisor = rsDivisor(eccPer);
  const blocks: number[][] = [];
  for (let i = 0, k = 0; i < nBlocks; i++) {
    const len = shortLen - eccPer + (i < nShort ? 0 : 1);
    const dat = data.slice(k, k + len);
    k += len;
    const ecc = rsRemainder(dat, divisor);
    if (i < nShort) dat.push(0);
    blocks.push(dat.concat(ecc));
  }
  const out: number[] = [];
  for (let i = 0; i < (blocks[0]?.length ?? 0); i++) {
    blocks.forEach((b, j) => {
      if (i !== shortLen - eccPer || j >= nShort) out.push(b[i] ?? 0);
    });
  }
  return out;
}

const at = (g: boolean[][], x: number, y: number) => g[y]?.[x] === true;
const put = (g: boolean[][], x: number, y: number, v: boolean) => {
  const row = g[y];
  if (row) row[x] = v;
};

const bit = (x: number, i: number) => ((x >>> i) & 1) !== 0;

const MASKS: ((x: number, y: number) => boolean)[] = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

function build(codewords: number[], ver: number, mask: number): boolean[][] {
  const size = ver * 4 + 17;
  const m: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const fn: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const set = (x: number, y: number, dark: boolean) => {
    put(m, x, y, dark);
    put(fn, x, y, true);
  };

  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  const finder = (cx: number, cy: number) => {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const d = Math.max(Math.abs(dx), Math.abs(dy));
        const x = cx + dx;
        const y = cy + dy;
        if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, d !== 2 && d !== 4);
      }
    }
  };
  finder(3, 3);
  finder(size - 4, 3);
  finder(3, size - 4);

  if (ver >= 2) {
    const n = Math.floor(ver / 7) + 2;
    const step = Math.ceil((ver * 4 + 4) / (n * 2 - 2)) * 2;
    const pos = [6];
    for (let p = size - 7; pos.length < n; p -= step) pos.splice(1, 0, p);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue;
        for (let dy = -2; dy <= 2; dy++) {
          for (let dx = -2; dx <= 2; dx++) {
            set((pos[i] ?? 0) + dx, (pos[j] ?? 0) + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
          }
        }
      }
    }
  }

  // Format bits (error correction M is 0b00) and, from version 7, the version word.
  const formatBits = formatWord(mask);
  for (let i = 0; i <= 5; i++) set(8, i, bit(formatBits, i));
  set(8, 7, bit(formatBits, 6));
  set(8, 8, bit(formatBits, 7));
  set(7, 8, bit(formatBits, 8));
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(formatBits, i));
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(formatBits, i));
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(formatBits, i));
  set(8, size - 8, true);
  if (ver >= 7) {
    let rem = ver;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (ver << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      set(a, b, bit(bits, i));
      set(b, a, bit(bits, i));
    }
  }

  // Data, zig-zagging up and down column pairs from the right edge.
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (!at(fn, x, y) && i < codewords.length * 8) {
          put(m, x, y, bit(codewords[i >>> 3] ?? 0, 7 - (i & 7)));
          i++;
        }
      }
    }
  }

  const f = MASKS[mask] ?? MASKS[0]!;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) if (!at(fn, x, y) && f(x, y)) put(m, x, y, !at(m, x, y));
  }
  return m;
}

/** 15 bit format word for error correction M and the given mask. */
export function formatWord(mask: number): number {
  const data = (0 << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

function penalty(m: boolean[][]): number {
  const n = m.length;
  let score = 0;
  const line = (get: (i: number) => boolean) => {
    let run = 1;
    for (let i = 1; i < n; i++) {
      if (get(i) === get(i - 1)) {
        run++;
        if (run === 5) score += 3;
        else if (run > 5) score += 1;
      } else run = 1;
    }
    // Finder-like 1:1:3:1:1 runs with a light margin.
    for (let i = 0; i + 11 <= n; i++) {
      const s = Array.from({ length: 11 }, (_, k) => (get(i + k) ? "1" : "0")).join("");
      if (s === "10111010000" || s === "00001011101") score += 40;
    }
  };
  for (let a = 0; a < n; a++) {
    line((i) => at(m, i, a));
    line((i) => at(m, a, i));
  }
  let dark = 0;
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (at(m, x, y)) dark++;
      if (
        x + 1 < n &&
        y + 1 < n &&
        at(m, x, y) === at(m, x + 1, y) &&
        at(m, x, y) === at(m, x, y + 1) &&
        at(m, x, y) === at(m, x + 1, y + 1)
      )
        score += 3;
    }
  }
  const total = n * n;
  score += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * 10;
  return score;
}

/** Rows of modules (true = dark) without the quiet zone, or null when the text is too long. */
export function qrMatrix(text: string): boolean[][] | null {
  const bytes = Array.from(new TextEncoder().encode(text));
  let ver = 1;
  const need = (v: number) => 4 + (v < 10 ? 8 : 16) + bytes.length * 8;
  while (ver <= MAX_VERSION && need(ver) > dataCodewords(ver) * 8) ver++;
  if (ver > MAX_VERSION) return null;
  const codewords = addEccAndInterleave(encodeData(bytes, ver), ver);
  let best: boolean[][] | null = null;
  let bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    const m = build(codewords, ver, mask);
    const s = penalty(m);
    if (s < bestScore) {
      best = m;
      bestScore = s;
    }
  }
  return best;
}
