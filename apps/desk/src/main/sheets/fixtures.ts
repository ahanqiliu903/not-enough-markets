/**
 * Test-only transport: serves `<dir>/<tab name>.csv` for `sheet=<tab name>` instead of
 * Google. Enabled by `NEM_DESK_SHEETS_FIXTURES=<dir>` in an unpackaged build (e2e). Like
 * the real gviz endpoint, an unknown tab answers with the first tab (by file name). A file
 * named `_status` holding a number answers every request with that HTTP status.
 */
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { assertAllowedUrl, type SheetsFetch, type SheetsResponse } from "./client";

function response(status: number, body: string, type = "text/csv; charset=utf-8"): SheetsResponse {
  return {
    status,
    headers: { get: (n) => (n.toLowerCase() === "content-type" ? type : null) },
    text: () => Promise.resolve(body),
  };
}

export function fixtureSheetsFetch(dir: string): SheetsFetch {
  return async (href) => {
    const url = assertAllowedUrl(href);
    try {
      const forced = Number((await readFile(join(dir, "_status"), "utf8")).trim());
      if (Number.isInteger(forced) && forced > 0) return response(forced, "", "text/html");
    } catch {
      // no forced status
    }
    const tab = url.searchParams.get("sheet") ?? "";
    const files = (await readdir(dir)).filter((f) => f.endsWith(".csv")).sort();
    const name = files.includes(`${tab}.csv`) ? `${tab}.csv` : files[0];
    if (name === undefined || /[/\\]/.test(tab)) return response(404, "", "text/html");
    return response(200, await readFile(join(dir, name), "utf8"));
  };
}
