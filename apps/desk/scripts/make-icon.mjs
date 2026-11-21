// Generates the app icon (build/icon.png, icon.icns, icon.ico) from the 16x16 pixel grid
// below: original art drawn here, colors read from src/renderer/theme.ts tokens. Pure Node
// (zlib), byte-for-byte deterministic, so scripts/check-assets.mjs can verify the committed
// files are exactly this script's output. Run: node scripts/make-icon.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { crc32, deflateSync } from "node:zlib";

const root = join(import.meta.dirname, "..");

// A notched terminal frame holding three rising candles (the "desk" pixel icon, in color).
// "." transparent, F frame, B background, A candle body, W wick.
const GRID = [
  "................",
  "...FFFFFFFFFF...",
  "..FBBBBBBBBBBF..",
  ".FBBBBBBBBBWBBF.",
  ".FBBBBBBBBAAABF.",
  ".FBBBBBWBBAAABF.",
  ".FBBBBAAABAAABF.",
  ".FBBBBAAABAAABF.",
  ".FBWBBAAABBWBBF.",
  ".FBWBBAAABBBBBF.",
  ".FAAABBWBBBBBBF.",
  ".FAAABBBBBBBBBF.",
  ".FBWBBBBBBBBBBF.",
  "..FBBBBBBBBBBF..",
  "...FFFFFFFFFF...",
  "................",
];
const PALETTE_TOKENS = { F: "dim", B: "bg", A: "amber", W: "text" };

/** Token values straight from theme.ts, so the icon can't drift from the app's colors. */
function themeColors() {
  const src = readFileSync(join(root, "src", "renderer", "theme.ts"), "utf8");
  const out = {};
  for (const m of src.matchAll(/\{ name: "([a-z-]+)", value: "#([0-9A-Fa-f]{6})"/g)) {
    const n = Number.parseInt(m[2], 16);
    out[m[1]] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  return out;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

/** RGBA PNG of GRID scaled by nearest neighbour to `size` (a multiple of 16). */
export function iconPng(size) {
  if (size % 16 !== 0) throw new Error(`icon size must be a multiple of 16: ${size}`);
  const colors = themeColors();
  const scale = size / 16;
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    const row = GRID[Math.floor(y / scale)];
    const off = y * (size * 4 + 1);
    raw[off] = 0; // filter: none
    for (let x = 0; x < size; x++) {
      const ch = row[Math.floor(x / scale)];
      if (ch === ".") continue;
      const rgb = colors[PALETTE_TOKENS[ch]];
      if (rgb === undefined) throw new Error(`no theme token for ${ch}`);
      raw.set([...rgb, 255], off + 1 + x * 4);
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr.set([8, 6, 0, 0, 0], 8); // 8-bit RGBA, no interlace
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** Apple icns with PNG payloads (supported since macOS 10.7). */
function icns() {
  const types = [
    ["icp4", 16],
    ["icp5", 32],
    ["ic11", 32],
    ["ic12", 64],
    ["ic07", 128],
    ["ic08", 256],
    ["ic13", 256],
    ["ic09", 512],
    ["ic14", 512],
    ["ic10", 1024],
  ];
  const parts = types.map(([type, size]) => {
    const png = iconPng(size);
    const head = Buffer.alloc(8);
    head.write(type, 0, "ascii");
    head.writeUInt32BE(png.length + 8, 4);
    return Buffer.concat([head, png]);
  });
  const body = Buffer.concat(parts);
  const head = Buffer.alloc(8);
  head.write("icns", 0, "ascii");
  head.writeUInt32BE(body.length + 8, 4);
  return Buffer.concat([head, body]);
}

/** Windows ico with PNG entries (Vista+); electron-builder needs a 256px entry. */
function ico() {
  const sizes = [16, 32, 48, 64, 128, 256];
  const pngs = sizes.map((s) => iconPng(s));
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(sizes.length, 4);
  let offset = 6 + 16 * sizes.length;
  const entries = sizes.map((s, i) => {
    const e = Buffer.alloc(16);
    e.writeUInt8(s === 256 ? 0 : s, 0);
    e.writeUInt8(s === 256 ? 0 : s, 1);
    e.writeUInt16LE(1, 4); // planes
    e.writeUInt16LE(32, 6); // bits per pixel
    e.writeUInt32LE(pngs[i].length, 8);
    e.writeUInt32LE(offset, 12);
    offset += pngs[i].length;
    return e;
  });
  return Buffer.concat([header, ...entries, ...pngs]);
}

/** Every generated file, keyed by its path relative to apps/desk. */
export function iconFiles() {
  return {
    "build/icon.png": iconPng(1024),
    "build/icon.icns": icns(),
    "build/icon.ico": ico(),
  };
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  for (const [rel, data] of Object.entries(iconFiles())) {
    writeFileSync(join(root, rel), data);
    console.log(`wrote ${rel} (${data.length} bytes)`);
  }
}
