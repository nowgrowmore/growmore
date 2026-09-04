import { describe, expect, it } from "vitest";
import { toCsv, toCsvField } from "./csv";

describe("toCsvField", () => {
  it("passes through a plain value unquoted", () => {
    expect(toCsvField("ALUMINI")).toBe("ALUMINI");
    expect(toCsvField(42)).toBe("42");
  });

  it("renders null/undefined as an empty field", () => {
    expect(toCsvField(null)).toBe("");
    expect(toCsvField(undefined)).toBe("");
  });

  it("quotes and escapes a value containing a comma", () => {
    expect(toCsvField("MCX, Comm")).toBe('"MCX, Comm"');
  });

  it("doubles embedded quotes", () => {
    expect(toCsvField('he said "hi"')).toBe('"he said ""hi"""');
  });

  it("quotes a value containing a newline", () => {
    expect(toCsvField("line1\nline2")).toBe('"line1\nline2"');
  });

  it("JSON-stringifies an object value", () => {
    expect(toCsvField({ a: 1 })).toBe('"{""a"":1}"');
  });
});

describe("toCsv", () => {
  it("builds a header row plus one row per record", () => {
    const rows = [
      { symbol: "ALUMINI", qty: 1 },
      { symbol: "GOLDM", qty: 2 },
    ];
    const csv = toCsv(rows, [
      { header: "Symbol", value: (r) => r.symbol },
      { header: "Qty", value: (r) => r.qty },
    ]);
    expect(csv).toBe("Symbol,Qty\r\nALUMINI,1\r\nGOLDM,2");
  });

  it("renders just the header for an empty row list", () => {
    const csv = toCsv([] as { symbol: string }[], [{ header: "Symbol", value: (r) => r.symbol }]);
    expect(csv).toBe("Symbol");
  });
});
