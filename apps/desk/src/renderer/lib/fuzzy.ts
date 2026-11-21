/**
 * Tiny fuzzy matcher for the command palette: query characters must appear in order.
 * Higher score is better; consecutive matches and word starts score extra. Returns null
 * when the query doesn't match.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.trim().toLowerCase();
  if (q === "") return 0;
  const t = text.toLowerCase();
  let score = 0;
  let ti = 0;
  let prev = -2;
  for (const ch of q) {
    if (ch === " ") continue;
    const found = t.indexOf(ch, ti);
    if (found < 0) return null;
    score += 1;
    if (found === prev + 1) score += 3;
    const before = found === 0 ? " " : (t[found - 1] ?? " ");
    if (/[\s\-_:/.]/.test(before)) score += 2;
    if (found === 0) score += 2;
    prev = found;
    ti = found + 1;
  }
  // Prefer shorter texts when scores tie.
  return score - t.length * 0.01;
}

export function fuzzyFilter<T>(items: readonly T[], query: string, text: (item: T) => string): T[] {
  const scored: { item: T; score: number; i: number }[] = [];
  items.forEach((item, i) => {
    const s = fuzzyScore(query, text(item));
    if (s !== null) scored.push({ item, score: s, i });
  });
  if (query.trim() === "") return scored.map((s) => s.item);
  return scored.sort((a, b) => b.score - a.score || a.i - b.i).map((s) => s.item);
}
