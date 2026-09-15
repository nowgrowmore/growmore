import { describe, expect, it } from "vitest";
import { rankCandidates, sanitizeCandidates } from "./mcx-options-candidates";
import type { MCXOptionsCandidate } from "./types";

describe("sanitizeCandidates", () => {
  it("passes a well-formed list through unchanged", () => {
    const raw = [{ strike: 6000, delta: -0.3, oi: 500, ltp: 42.5 }];
    expect(sanitizeCandidates(raw)).toEqual(raw);
  });

  it("distinguishes null (entry never attempted) from [] (nothing cleared filters)", () => {
    // The page renders these two very differently -- "—" vs "none cleared
    // filters" -- and they mean genuinely different things in the engine.
    expect(sanitizeCandidates(null)).toBeNull();
    expect(sanitizeCandidates([])).toEqual([]);
  });

  it("drops entries with a missing or non-numeric strike/delta/oi", () => {
    // `candidates_considered` is JSONB written by the bot and read back with
    // no validation. `c.delta.toFixed(2)` on any of these threw during
    // render, which -- with no error boundary under app/ -- blanked the whole
    // app shell rather than one table cell.
    const raw = [
      { strike: 6000, delta: -0.3, oi: 500, ltp: 42.5 },
      { strike: 5900, delta: null, oi: 100, ltp: 30 },
      { strike: 5800, delta: -0.2, oi: "lots", ltp: 20 },
      { delta: -0.2, oi: 10, ltp: 20 },
      null,
      "not an object",
    ];
    expect(sanitizeCandidates(raw as never)).toEqual([
      { strike: 6000, delta: -0.3, oi: 500, ltp: 42.5 },
    ]);
  });

  it("never fabricates a zero delta for a broken entry", () => {
    // A fabricated 0 would misrepresent the decision this table exists to
    // explain, so a broken row is dropped rather than coerced.
    const [only] = sanitizeCandidates([{ strike: 1, delta: "x", oi: 1, ltp: 1 }] as never) ?? [];
    expect(only).toBeUndefined();
  });

  it("defaults only ltp to 0, since it is display-only", () => {
    expect(sanitizeCandidates([{ strike: 6000, delta: -0.3, oi: 500 }] as never)).toEqual([
      { strike: 6000, delta: -0.3, oi: 500, ltp: 0 },
    ]);
  });

  it("accepts numeric strings, which is how numerics can arrive", () => {
    expect(sanitizeCandidates([{ strike: "6000", delta: "-0.3", oi: "500", ltp: "42.5" }] as never))
      .toEqual([{ strike: 6000, delta: -0.3, oi: 500, ltp: 42.5 }]);
  });
});

function candidate(strike: number, delta: number, oi: number): MCXOptionsCandidate {
  return { strike, delta, oi, ltp: 1 };
}

describe("rankCandidates", () => {
  it("keeps the entries closest to the target delta, sorted by strike", () => {
    const candidates = [
      candidate(5800, -0.10, 500),
      candidate(5900, -0.29, 500),
      candidate(6000, -0.31, 500),
      candidate(6100, -0.80, 500),
    ];

    const shown = rankCandidates(candidates, {
      selectedStrike: null,
      targetDelta: 0.3,
      limit: 2,
    });

    expect(shown.map((c) => c.strike)).toEqual([5900, 6000]);
  });

  it("always includes the picked strike, even when it falls outside the cap", () => {
    const candidates = [
      candidate(5800, -0.29, 500),
      candidate(5900, -0.30, 500),
      candidate(6000, -0.90, 500),
    ];

    const shown = rankCandidates(candidates, {
      selectedStrike: 6000,
      targetDelta: 0.3,
      limit: 2,
    });

    expect(shown.map((c) => c.strike)).toContain(6000);
  });

  it("stops at the first OI floor that leaves anything at all", () => {
    // MCX options can have very sparse OI far from the money. The floors are
    // tried strictest-first (10, 1, 0) and the FIRST one with any survivor
    // wins -- so an oi=1 strike is shown while its oi=0 neighbour is not.
    const thin = [candidate(5900, -0.3, 0), candidate(6000, -0.31, 1)];

    const shown = rankCandidates(thin, { selectedStrike: null, targetDelta: 0.3, limit: 10 });

    expect(shown.map((c) => c.strike)).toEqual([6000]);
  });

  it("falls all the way through to a zero floor rather than showing nothing", () => {
    const allZeroOi = [candidate(5900, -0.3, 0), candidate(6000, -0.31, 0)];

    const shown = rankCandidates(allZeroOi, {
      selectedStrike: null,
      targetDelta: 0.3,
      limit: 10,
    });

    expect(shown).toHaveLength(2);
  });

  it("prefers the liquid subset when one exists", () => {
    const mixed = [candidate(5900, -0.30, 0), candidate(6000, -0.50, 5000)];

    const shown = rankCandidates(mixed, { selectedStrike: null, targetDelta: 0.3, limit: 10 });

    expect(shown.map((c) => c.strike)).toEqual([6000]);
  });

  it("falls back to ranking by absolute delta when no target delta was set", () => {
    // A skipped cycle records candidates without ever choosing a target.
    const candidates = [candidate(5800, -0.7, 500), candidate(5900, -0.1, 500)];

    const shown = rankCandidates(candidates, {
      selectedStrike: null,
      targetDelta: null,
      limit: 1,
    });

    expect(shown.map((c) => c.strike)).toEqual([5900]);
  });
});
