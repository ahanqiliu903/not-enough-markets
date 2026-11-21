// Fails if a hex color literal appears in src/ or e2e/ outside src/renderer/theme.ts.
// theme.test.ts is also allowed: it pins the token values to the spec and checks contrast.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const root = join(import.meta.dirname, "..");
const allowed = new Set([
  join("src", "renderer", "theme.ts"),
  join("src", "renderer", "theme.test.ts"),
]);
const exts = /\.(ts|tsx|js|jsx|mjs|cjs|css|html|svg)$/;
// #rgb, #rgba, #rrggbb, #rrggbbaa not followed by more word characters.
const hex = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![0-9a-zA-Z_-])/g;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (exts.test(name)) yield p;
  }
}

const hits = [];
for (const dir of ["src", "e2e"]) {
  for (const file of walk(join(root, dir))) {
    const rel = relative(root, file);
    if (allowed.has(rel)) continue;
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        // Ignore numeric HTML entities like &#123; and URL fragments like href="#main".
        for (const m of line.matchAll(hex)) {
          const before = line.slice(Math.max(0, m.index - 1), m.index);
          if (before === "&") continue;
          hits.push(`${rel.split(sep).join("/")}:${i + 1}: ${m[0]}`);
        }
      });
  }
}

if (hits.length > 0) {
  console.error("Hex colors are only allowed in src/renderer/theme.ts:\n" + hits.join("\n"));
  process.exit(1);
}
console.log("check-no-hex: ok");
