import { Ajv2020, type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import backtestRun from "../../../../../contract/schema/backtest_run.schema.json";
import status from "../../../../../contract/schema/status.schema.json";
import strategyRow from "../../../../../contract/schema/strategy_row.schema.json";
import summaryReport from "../../../../../contract/schema/summary_report.schema.json";
import summaryRow from "../../../../../contract/schema/summary_row.schema.json";
import tradeRow from "../../../../../contract/schema/trade_row.schema.json";
import variantMetrics from "../../../../../contract/schema/variant_metrics.schema.json";

/**
 * The contract's JSON Schemas, bundled into main at build time (contract/ is the source of
 * truth; this file only loads it). One Ajv instance so `$ref`s between files resolve.
 */

export type SchemaName =
  | "summary_row.schema.json"
  | "strategy_row.schema.json"
  | "trade_row.schema.json"
  | "summary_report.schema.json"
  | "status.schema.json"
  | "backtest_run.schema.json";

interface PropertySchema {
  type?: string | string[];
}

export interface TableSchema {
  required: string[];
  properties: Record<string, PropertySchema>;
}

const all = [summaryRow, strategyRow, tradeRow, summaryReport, status, backtestRun, variantMetrics];

// strict: false because the contract uses union types with `pattern` (valid 2020-12, but
// Ajv's strict mode would log about it) and we must never reject a valid contract file.
const ajv = new Ajv2020({ allErrors: true, strict: false });
for (const s of all) ajv.addSchema(s);

const validators = new Map<SchemaName, ValidateFunction>();

export function validator(name: SchemaName): ValidateFunction {
  let v = validators.get(name);
  if (v === undefined) {
    v = ajv.getSchema(name);
    if (v === undefined) throw new Error(`schema not bundled: ${name}`);
    validators.set(name, v);
  }
  return v;
}

const tables: Partial<Record<SchemaName, unknown>> = {
  "summary_row.schema.json": summaryRow,
  "strategy_row.schema.json": strategyRow,
  "trade_row.schema.json": tradeRow,
};

export function tableSchema(name: SchemaName): TableSchema {
  const s = tables[name];
  if (s === undefined) throw new Error(`not a table schema: ${name}`);
  return s as TableSchema;
}

/** Field name an Ajv error is about, e.g. "/opened_at" -> "opened_at". */
export function errorField(e: ErrorObject): string {
  const missing = (e.params as { missingProperty?: unknown }).missingProperty;
  const path = e.instancePath.replace(/^\//, "").replaceAll("/", ".");
  if (typeof missing === "string") return path === "" ? missing : `${path}.${missing}`;
  return path === "" ? "(document)" : path;
}

export function errorMessage(e: ErrorObject): string {
  const where = e.instancePath === "" ? "" : `${e.instancePath.replace(/^\//, "").replaceAll("/", ".")} `;
  return `${where}${e.message ?? "is invalid"}`;
}
