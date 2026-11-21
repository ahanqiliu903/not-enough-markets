// @vitest-environment node
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { Diagnostics } from "@shared/diagnostics";
import { diagnosticsText, redactDeep } from "../diagnostics/report";
import { Logger } from "./logger";
import { maskId, Redactor } from "./redact";

const FAKE_ID = "1FAKEsheetIdForNemDeskTestsOnly_0000000000a";
const tmps: string[] = [];

function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-log-"));
  tmps.push(d);
  return d;
}

afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

function redactor(): Redactor {
  const r = new Redactor();
  r.addProvider(() => [
    { value: "/Users/alice/Library/Application Support/NEM Desk", placeholder: "<userData>" },
    { value: "/Users/alice", placeholder: "~" },
    { value: "alice", placeholder: "<user>" },
    { value: "/opt/keys/deploy_ed25519", placeholder: "<key-path>" },
    { value: "nem.example.org", placeholder: "<host>" },
  ]);
  return r;
}

describe("Redactor", () => {
  it("replaces registered values, longest first", () => {
    const r = redactor();
    expect(r.redact("/Users/alice/Library/Application Support/NEM Desk/logs/main.log")).toBe("<userData>/logs/main.log");
    expect(r.redact("/Users/alice/nem-cache")).toBe("~/nem-cache");
    expect(r.redact("ssh -i /opt/keys/deploy_ed25519 alice@nem.example.org")).toBe("ssh -i <key-path> <user>@<host>");
  });

  // assembled at runtime so the repo's detect-private-key hook doesn't flag this fake
  const FAKE_KEY = ["-----BEGIN OPENSSH", "PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH",
    "PRIVATE KEY-----"].join(" ");

  it("catches unregistered sheet links, tokens, keys, addresses and paths", () => {
    const r = new Redactor();
    const out = r.redact(
      [
        `https://docs.google.com/spreadsheets/d/${FAKE_ID}/edit#gid=0`,
        FAKE_ID,
        FAKE_KEY,
        "bob@vps.example.net",
        "203.0.113.7",
        "/Users/bob/x",
        "/home/carol/y",
        "C:\\Users\\dave\\z",
        "/somewhere/.ssh/id_rsa",
        "?api_key=abc123&x=1",
      ].join(" "),
    );
    for (const leak of [FAKE_ID, "b3BlbnNzaC1", "bob", "vps.example.net", "203.0.113.7", "carol", "dave", "id_rsa", "abc123"]) {
      expect(out).not.toContain(leak);
    }
    expect(out).toContain("/spreadsheets/d/<sheet-id>/edit");
    expect(out).toContain("<private-key>");
  });

  it("leaves ordinary text alone", () => {
    const r = new Redactor();
    const s = "sheets: manual sync: 3/3 tab(s) ok; KXBTC15M-26NOV201515-15 contract 1.x";
    expect(r.redact(s)).toBe(s);
  });

  it("masks IDs for display", () => {
    expect(maskId(FAKE_ID)).toBe("1FAK…000a");
    expect(maskId("short")).toBe("…");
  });
});

describe("Logger", () => {
  it("writes redacted lines and keeps recent ones in memory", () => {
    const dir = tmp();
    const l = new Logger({ file: join(dir, "logs", "main.log"), redactor: redactor(), now: () => new Date("2026-11-20T12:00:00Z") });
    l.info("vps", "sync failed for alice@nem.example.org\nPermission denied");
    l.error("sheets", `bad https://docs.google.com/spreadsheets/d/${FAKE_ID}/gviz/tq`);
    const text = readFileSync(join(dir, "logs", "main.log"), "utf8");
    expect(text).toBe(
      "2026-11-20T12:00:00.000Z INFO  [vps] sync failed for <user>@<host> | Permission denied\n" +
        "2026-11-20T12:00:00.000Z ERROR [sheets] bad https://docs.google.com/spreadsheets/d/<sheet-id>/gviz/tq\n",
    );
    expect(l.recent()).toHaveLength(2);
    expect(text).not.toContain(FAKE_ID);
  });

  it("rotates at maxBytes and keeps `keep` old files", () => {
    const dir = tmp();
    const file = join(dir, "main.log");
    const l = new Logger({ file, maxBytes: 200, keep: 2, recent: 5 });
    for (let i = 0; i < 40; i++) l.info("t", `line ${i} ${"x".repeat(40)}`);
    expect(existsSync(`${file}.1`)).toBe(true);
    expect(existsSync(`${file}.2`)).toBe(true);
    expect(existsSync(`${file}.3`)).toBe(false);
    expect(readFileSync(file, "utf8").length).toBeLessThanOrEqual(200);
    expect(readFileSync(file, "utf8")).toContain("line 39");
    expect(l.recent()).toHaveLength(5);
  });

  it("never throws when the file can't be written", () => {
    const l = new Logger({ file: "/dev/null/not-a-dir/main.log" });
    expect(() => l.warn("t", "still fine")).not.toThrow();
    expect(l.recent()).toHaveLength(1);
  });
});

describe("diagnostics report", () => {
  const raw: Diagnostics = {
    generatedAt: "2026-11-20T12:00:00.000Z",
    app: { version: "0.1.0", electron: "45.2.3", chrome: "140", node: "24", platform: "darwin", arch: "arm64", packaged: true },
    contract: { expected: "1.x", dbSchemaVersions: [3] },
    userData: "/Users/alice/Library/Application Support/NEM Desk",
    logFile: "/Users/alice/Library/Application Support/NEM Desk/logs/main.log",
    sources: [
      {
        id: "vps",
        label: "VPS alice@nem.example.org",
        kind: "vps",
        path: "/Users/alice/Library/Application Support/NEM Desk/vps-cache",
        active: true,
        db: "/Users/alice/Library/Application Support/NEM Desk/vps-cache/nem.db",
        lastLoadAt: "2026-11-20T11:59:00.000Z",
        errorCount: 0,
        errors: [],
      },
    ],
    db: { path: "/Users/alice/nem-cache/nem.db", schemaVersion: 3, sizeBytes: 1024, series: 3, feeds: 1, newestTs: null },
    sheets: { configured: true, portfolios: ["fixture"], intervalMinutes: 5, lastSync: `failed for ${FAKE_ID}` },
    vps: { configured: true, intervalMinutes: null, lastSync: null },
    recentLog: ["2026-11-20T12:00:00.000Z INFO  [app] start"],
  };

  it("redacts every string and renders text with versions, sources and the log", () => {
    const d = redactDeep(raw, redactor());
    const text = diagnosticsText(d);
    for (const leak of ["alice", "nem.example.org", FAKE_ID, "/Users/"]) expect(text).not.toContain(leak);
    expect(text).toContain("app: 0.1.0");
    expect(text).toContain("contract: 1.x");
    expect(text).toContain("* VPS <user>@<host> [vps] <userData>/vps-cache");
    expect(text).toContain("db: ~/nem-cache/nem.db");
    expect(text).toContain("[app] start");
  });
});
