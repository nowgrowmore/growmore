import type { MCXOptionsCandidate, MCXOptionsCandidateRaw } from "./types";

// `mcx_options_selections.candidates_considered` is a JSONB blob written by
// the bot and read back with no validation of any kind. The dashboard used to
// call `c.delta.toFixed(2)` and `c.oi.toLocaleString()` straight on its
// entries, so one null/missing/wrong-typed field threw during render -- and
// because there is no route-level error boundary under app/, that took out
// the whole app shell rather than one table cell. Found by independent code
// review, 2026-09-15.
//
// This module is the single place that turns the raw blob into numbers the UI
// can trust, and the single place that knows how to rank candidates by
// relevance to the decision. Both the server (trimming the payload before it
// crosses the RSC wire) and the client (choosing what to show) use it, so the
// two can never disagree about which candidates mattered.

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Every entry that is a usable candidate, with its fields coerced to real
 * numbers. Entries missing `strike`/`delta`/`oi` (or carrying junk in them)
 * are DROPPED rather than rendered as zeros -- a fabricated 0 delta would
 * misrepresent the decision, which is the one thing this table exists to
 * explain. */
export function sanitizeCandidates(
  raw: MCXOptionsCandidateRaw[] | null | undefined
): MCXOptionsCandidate[] | null {
  if (raw === null || raw === undefined) return null;
  if (!Array.isArray(raw)) return null;
  const out: MCXOptionsCandidate[] = [];
  for (const entry of raw) {
    if (entry === null || typeof entry !== "object") continue;
    const strike = finiteNumber(entry.strike);
    const delta = finiteNumber(entry.delta);
    const oi = finiteNumber(entry.oi);
    if (strike === null || delta === null || oi === null) continue;
    out.push({ strike, delta, oi, ltp: finiteNumber(entry.ltp) ?? 0 });
  }
  return out;
}

//: Liquidity floors to try, loosest first as a fallback when the market's too
//: thin for the stricter ones to leave anything -- MCX options can have very
//: sparse OI far from the money, so a fixed non-relaxing threshold would
//: sometimes show nothing at all. This is display-only noise reduction (the
//: engine's real gate is `config.min_open_interest`), so a 144-candidate dump
//: doesn't bury the strikes that actually mattered to the decision.
export const OI_DISPLAY_FLOORS = [10, 1, 0];

/** The `limit` most decision-relevant candidates: the liquid-enough subset
 * (loosening the OI floor until something survives), ranked by closeness to
 * the delta the engine was actually targeting -- the real selection
 * criterion, not strike order -- with the picked strike always included even
 * if it fell outside the cap. Returned sorted by strike, which is how a
 * reader scans a chain. */
export function rankCandidates(
  candidates: MCXOptionsCandidate[],
  { selectedStrike, targetDelta, limit }: {
    selectedStrike: number | null;
    targetDelta: number | null;
    limit: number;
  }
): MCXOptionsCandidate[] {
  let liquid = candidates;
  for (const floor of OI_DISPLAY_FLOORS) {
    const survivors = candidates.filter((c) => c.oi >= floor);
    if (survivors.length > 0) {
      liquid = survivors;
      break;
    }
  }

  const ranked = [...liquid].sort((a, b) => {
    if (targetDelta === null) return Math.abs(a.delta) - Math.abs(b.delta);
    return (
      Math.abs(Math.abs(a.delta) - targetDelta) - Math.abs(Math.abs(b.delta) - targetDelta)
    );
  });

  const shown = ranked.slice(0, limit);
  if (selectedStrike !== null && !shown.some((c) => c.strike === selectedStrike)) {
    const picked = candidates.find((c) => c.strike === selectedStrike);
    if (picked) shown.push(picked);
  }
  return shown.sort((a, b) => a.strike - b.strike);
}
