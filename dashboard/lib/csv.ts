/** Minimal CSV serialization -- just what the trade log/audit log exports
 * need. Quotes a field only when it contains a comma, quote, or newline
 * (RFC 4180's minimal-quoting convention), doubling any embedded quotes. */
export function toCsvField(value: unknown): string {
  if (value === null || value === undefined) return "";
  const str = typeof value === "object" ? JSON.stringify(value) : String(value);
  if (/[",\n]/.test(str)) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

export interface CsvColumn<T> {
  header: string;
  value: (row: T) => unknown;
}

export function toCsv<T>(rows: T[], columns: CsvColumn<T>[]): string {
  const headerLine = columns.map((c) => toCsvField(c.header)).join(",");
  const lines = rows.map((row) => columns.map((c) => toCsvField(c.value(row))).join(","));
  return [headerLine, ...lines].join("\r\n");
}
