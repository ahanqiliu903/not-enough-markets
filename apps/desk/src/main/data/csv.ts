import { parse } from "csv-parse/sync";
import { CONTRACT_MAJOR, EXPECTED_VERSION, type DataError } from "@shared/data";
import { errorField, errorMessage, tableSchema, validator, type SchemaName } from "./schemas";

/**
 * CSV -> contract rows, with the coercion rules of `tests/contract/test_contract.py`:
 * empty = null (when the schema allows null), integer/number/boolean by the schema's declared type,
 * and unparseable text stays a string so validation reports it.
 */

const INT_RE = /^\s*[+-]?\d+\s*$/;
const FLOAT_RE = /^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*$/;

/** Python's float() also takes these; keep parity so the same files validate the same way. */
const FLOAT_WORDS: Record<string, number> = {
  inf: Infinity,
  "+inf": Infinity,
  "-inf": -Infinity,
  infinity: Infinity,
  "+infinity": Infinity,
  "-infinity": -Infinity,
  nan: NaN,
  "+nan": NaN,
  "-nan": NaN,
};

export function coerce(value: string, declared: string | string[] | undefined): unknown {
  const types = declared === undefined ? ["string"] : Array.isArray(declared) ? declared : [declared];
  if (value === "" && types.includes("null")) return null;
  // the engine writes "true"/"false"; Sheets may turn them into TRUE/FALSE
  if (types.includes("boolean")) {
    const v = value.trim().toLowerCase();
    if (v === "true" || v === "false") return v === "true";
  }
  if (types.includes("integer")) return INT_RE.test(value) ? Number.parseInt(value, 10) : value;
  if (types.includes("number")) {
    if (FLOAT_RE.test(value)) return Number(value);
    const word = FLOAT_WORDS[value.trim().toLowerCase()];
    return word ?? value;
  }
  return value;
}

/** Errors listed per file before the rest are summarized in one line. */
export const MAX_ERRORS_PER_FILE = 50;

export interface Table<T> {
  /** Null when the file is blocked by any error: nothing from it is shown. */
  rows: T[] | null;
  /** Cell text as written by the engine, aligned with `rows` (used for CSV export). */
  raw: string[][];
  header: string[];
  errors: DataError[];
}

export function majorOf(version: unknown): number | null {
  if (typeof version !== "string") return null;
  const m = /^(\d+)\.\d+$/.exec(version);
  return m?.[1] === undefined ? null : Number(m[1]);
}

export function versionError(file: string, found: unknown, row?: number): DataError {
  return {
    kind: "version",
    file,
    ...(row !== undefined ? { row } : {}),
    field: "schema_version",
    message: `${row !== undefined ? `row ${row}: ` : ""}schema_version ${String(found)}, expected ${EXPECTED_VERSION}`,
    expected: EXPECTED_VERSION,
    found: String(found),
  };
}

/** Parses and validates one contract CSV. Never throws. */
export function parseTable<T>(text: string, file: string, schemaName: SchemaName): Table<T> {
  const schema = tableSchema(schemaName);
  const errors: DataError[] = [];
  let more = 0;
  const push = (e: DataError): void => {
    if (errors.length < MAX_ERRORS_PER_FILE) errors.push(e);
    else more++;
  };

  let records: string[][];
  try {
    records = parse(text, { bom: true, skip_empty_lines: true, relax_column_count: true });
  } catch (err) {
    return { rows: null, raw: [], header: [], errors: [{ kind: "parse", file, message: `not valid CSV: ${String(err)}` }] };
  }
  const header = records[0] ?? [];
  const body = records.slice(1);
  if (header.length === 0) {
    return { rows: null, raw: [], header, errors: [{ kind: "parse", file, message: "file is empty (no header row)" }] };
  }

  // Columns added in a minor (`x-since`) read as null when an older 1.x file lacks them.
  const added = schema.required.filter((c) => !header.includes(c) && schema.properties[c]?.["x-since"] !== undefined);
  const missing = schema.required.filter((c) => !header.includes(c) && !added.includes(c));
  if (missing.length > 0) {
    return {
      rows: null,
      raw: [],
      header,
      errors: [
        {
          kind: "missing_column",
          file,
          field: missing.join(", "),
          message: `missing columns [${missing.map((c) => `'${c}'`).join(", ")}]`,
          expected: EXPECTED_VERSION,
        },
      ],
    };
  }

  const types = header.map((h) => schema.properties[h]?.type);
  // A row may stop before trailing `x-since` columns (spreadsheet tools drop trailing empty
  // cells, and those columns are nullable): the missing cells read as empty.
  let minCells = header.length;
  while (minCells > 0 && schema.properties[header[minCells - 1] ?? ""]?.["x-since"] !== undefined) minCells--;
  const validate = validator(schemaName);
  const rows: Record<string, unknown>[] = [];
  const seen = new Map<string, number>();
  let firstDuplicate: [number, number] | null = null;
  let duplicates = 0;

  for (let i = 0; i < body.length; i++) {
    const cells = body[i] ?? [];
    const rowNo = i + 1;
    if (cells.length < minCells || cells.length > header.length) {
      push({
        kind: "parse",
        file,
        row: rowNo,
        message: `row ${rowNo}: ${cells.length} fields, header has ${header.length}`,
      });
      rows.push({});
      continue;
    }
    const row: Record<string, unknown> = {};
    for (let c = 0; c < header.length; c++) {
      row[header[c] ?? ""] = coerce(cells[c] ?? "", types[c]);
    }
    for (const c of added) row[c] = null;
    rows.push(row);
    if (majorOf(row["schema_version"]) !== CONTRACT_MAJOR) {
      push(versionError(file, row["schema_version"], rowNo));
    } else if (!validate(row)) {
      for (const e of validate.errors ?? []) {
        push({ kind: "invalid", file, row: rowNo, field: errorField(e), message: `row ${rowNo}: ${errorMessage(e)}` });
      }
    }
    const key = JSON.stringify(row);
    const prev = seen.get(key);
    if (prev === undefined) seen.set(key, rowNo);
    else {
      duplicates++;
      firstDuplicate ??= [prev, rowNo];
    }
  }

  if (firstDuplicate !== null) {
    const [a, b] = firstDuplicate;
    errors.unshift({
      kind: "duplicate",
      file,
      row: b,
      field: "(row)",
      message: `duplicate rows: ${duplicates} row(s) repeat an earlier row (first: row ${b} repeats row ${a})`,
    });
  }
  if (more > 0) {
    errors.push({ kind: "invalid", file, message: `and ${more} more problem(s) in this file` });
  }
  if (errors.length > 0) return { rows: null, raw: [], header, errors };
  return { rows: rows as T[], raw: body, header, errors };
}
