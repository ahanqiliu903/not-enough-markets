// DESK-022 asset and license check (part of `npm run lint`, so `npm run check`):
// 1. Every font/image file in apps/desk is either generated original art from a script in
//    this repo (and, for the app icon, byte-for-byte that script's output), or sits next to a
//    license file (OFL, MIT, Apache, CC0, ...).
// 2. No third-party game names appear in any source, asset or doc here (binary files
//    included, ASCII and UTF-16). The names are stored ROT13 so this file passes its own scan.
//    TICKETS.md is exempt: it states the rule itself.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join, relative, sep } from "node:path";
import { iconFiles } from "./make-icon.mjs";

const root = join(import.meta.dirname, "..");
const SKIP_DIRS = new Set([
  "node_modules",
  "out",
  "dist",
  "release",
  "test-results",
  "playwright-report",
  "coverage",
  ".git",
]);
const EXEMPT_FROM_NAMES = new Set(["TICKETS.md"]);
const ASSET = /\.(woff2?|ttf|otf|eot|png|jpe?g|gif|webp|bmp|ico|icns|svg|avif|cur)$/i;
const LICENSE = /^(OFL|LICENSE|LICENCE|COPYING|NOTICE)([._-].*)?(\.txt|\.md)?$/i;
const LICENSE_TEXT = /Open Font License|MIT License|Apache License|CC0|Creative Commons|Permission is hereby granted/i;

/** Generated original art: path (or directory prefix) -> the script that makes it. */
const GENERATED = [
  { prefix: "build/icon.", by: "scripts/make-icon.mjs" },
  { prefix: "docs/screenshots/", by: "e2e/screenshots.spec.ts" },
];

const rot13 = (s) =>
  s.replace(/[a-z]/g, (c) => String.fromCharCode(((c.charCodeAt(0) - 97 + 13) % 26) + 97));
// Game and studio names that must never appear (and spaced/hyphenated variants).
const BANNED = ["ulcvkry", "zvarpensg", "zbwnat", "fxloybpx", "fxl oybpx", "fxl-oybpx"].map(rot13);

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else yield p;
  }
}

const problems = [];
const expected = iconFiles();
let assets = 0;

for (const file of walk(root)) {
  const rel = relative(root, file).split(sep).join("/");
  const buf = readFileSync(file);

  if (!EXEMPT_FROM_NAMES.has(rel)) {
    // latin1 + NUL stripping also exposes UTF-16 strings (font name tables).
    const text = buf.toString("latin1").replace(/\0/g, "").toLowerCase();
    for (const name of BANNED) {
      if (text.includes(name)) problems.push(`${rel}: contains a third-party game name ("${name}")`);
    }
  }

  if (!ASSET.test(rel)) continue;
  assets++;
  const gen = GENERATED.find((g) => rel.startsWith(g.prefix));
  if (gen !== undefined) {
    if (!existsSync(join(root, gen.by))) problems.push(`${rel}: generator ${gen.by} is missing`);
    const want = expected[rel];
    if (gen.by === "scripts/make-icon.mjs") {
      if (want === undefined) problems.push(`${rel}: not produced by ${gen.by}`);
      else if (!want.equals(buf)) {
        problems.push(`${rel}: differs from ${gen.by} output (run: node ${gen.by})`);
      }
    }
    continue;
  }
  const dir = dirname(file);
  const licenses = readdirSync(dir).filter((n) => LICENSE.test(n));
  const ok = licenses.some((n) => LICENSE_TEXT.test(readFileSync(join(dir, n), "utf8")));
  if (!ok) {
    problems.push(
      `${rel}: no license file next to it (${basename(dir)}/OFL.txt, LICENSE, ...) and not generated original art`,
    );
  }
}

for (const rel of Object.keys(expected)) {
  if (!existsSync(join(root, rel))) problems.push(`${rel}: missing (run: node scripts/make-icon.mjs)`);
}

if (problems.length > 0) {
  console.error("check-assets failed:\n" + problems.join("\n"));
  process.exit(1);
}
console.log(`check-assets: ok (${assets} assets)`);
