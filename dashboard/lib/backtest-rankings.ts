import { toNumber } from "./format";
import type { BacktestRun } from "./types";

/** Same guardrails `docs/backtest-results.md` and `bot/growmore_bot/backtest/
 * run_all.py`'s `rank_results` use: a result needs at least this many closed
 * trades to be statistically meaningful, and a max drawdown at/under this
 * to be worth ranking at all -- a "great" number built on 3 trades or a 90%
 * drawdown isn't a real result, so rankings exclude it outright rather than
 * quietly showing it at #1.
 */
export const DEFAULT_MIN_TRADE_COUNT = 15;
export const DEFAULT_MAX_DRAWDOWN_PCT = 50;

export function passesGuardrails(
  run: BacktestRun,
  options: { minTradeCount?: number; maxDrawdownPct?: number } = {}
): boolean {
  const minTradeCount = options.minTradeCount ?? DEFAULT_MIN_TRADE_COUNT;
  const maxDrawdownPct = options.maxDrawdownPct ?? DEFAULT_MAX_DRAWDOWN_PCT;
  return toNumber(run.trade_count) >= minTradeCount && toNumber(run.max_drawdown_pct) <= maxDrawdownPct;
}

/** Metrics where a HIGHER number is better -- descending sort. max_drawdown_pct
 * is deliberately excluded here: lower is better for that one. */
export type HigherIsBetterKey = "cagr_pct" | "sharpe_ratio" | "profit_factor" | "win_rate_pct" | "trade_count";
export type RankableKey = HigherIsBetterKey | "max_drawdown_pct";

function profitFactorValue(run: BacktestRun): number {
  // null means "infinite" (zero losing trades) -- see formatProfitFactor's
  // identical convention. Treated as the best possible value for ranking,
  // not the worst (a real bug fixed elsewhere in this project when this was
  // once mapped to 0 instead).
  return run.profit_factor === null ? Infinity : toNumber(run.profit_factor);
}

function valueFor(run: BacktestRun, key: RankableKey): number {
  return key === "profit_factor" ? profitFactorValue(run) : toNumber(run[key]);
}

/** Top `limit` runs by a single criterion, among runs passing the standard
 * guardrails. `max_drawdown_pct` ranks ascending (lower is better); every
 * other key ranks descending.
 */
export function rankByCriterion(
  runs: BacktestRun[],
  key: RankableKey,
  limit = 10,
  guardrailOptions?: { minTradeCount?: number; maxDrawdownPct?: number }
): BacktestRun[] {
  const direction = key === "max_drawdown_pct" ? 1 : -1;
  return runs
    .filter((r) => passesGuardrails(r, guardrailOptions))
    .sort((a, b) => direction * (valueFor(a, key) - valueFor(b, key)))
    .slice(0, limit);
}

export type Criterion = "composite" | RankableKey;

export const RANKING_CRITERIA: { value: Criterion; label: string }[] = [
  { value: "composite", label: "Overall pick" },
  { value: "cagr_pct", label: "CAGR" },
  { value: "sharpe_ratio", label: "Sharpe" },
  { value: "profit_factor", label: "Profit factor" },
  { value: "win_rate_pct", label: "Win rate" },
  { value: "max_drawdown_pct", label: "Max drawdown" },
];

/** Overall pick: average percentile rank across CAGR and Sharpe, among runs
 * passing the guardrails -- a run has to be genuinely good on BOTH growth
 * and risk-adjusted quality to rank highly, not just spike one metric.
 * Percentile rank (not a raw z-score) is used so one extreme outlier run
 * doesn't distort every other run's composite score.
 */
export function rankByComposite(runs: BacktestRun[], limit = 10): BacktestRun[] {
  const eligible = runs.filter((r) => passesGuardrails(r));
  if (eligible.length === 0) return [];

  const percentileRanks = (key: "cagr_pct" | "sharpe_ratio"): Map<BacktestRun, number> => {
    const sorted = [...eligible].sort((a, b) => toNumber(a[key]) - toNumber(b[key]));
    const ranks = new Map<BacktestRun, number>();
    sorted.forEach((run, i) => {
      ranks.set(run, sorted.length === 1 ? 1 : i / (sorted.length - 1));
    });
    return ranks;
  };

  const cagrRanks = percentileRanks("cagr_pct");
  const sharpeRanks = percentileRanks("sharpe_ratio");

  return eligible
    .map((run) => ({ run, score: ((cagrRanks.get(run) ?? 0) + (sharpeRanks.get(run) ?? 0)) / 2 }))
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ run }) => run);
}

/** The most recent backtest run for a given (strategy, instrument) pair --
 * the same pairing a bot_config trades against. `undefined` when this
 * config has never been backtested. */
export function findMatchingBacktestRun(
  config: { strategy_id: string; instrument_id: string },
  runs: BacktestRun[]
): BacktestRun | undefined {
  const matches = runs.filter(
    (r) => r.strategy_id === config.strategy_id && r.instrument_id === config.instrument_id
  );
  if (matches.length === 0) return undefined;
  return matches.reduce((latest, r) =>
    new Date(r.started_at).getTime() > new Date(latest.started_at).getTime() ? r : latest
  );
}

export interface RankPosition {
  criterion: Criterion;
  label: string;
  rank: number; // 1-based
}

/** Where a specific backtest run stands among ALL guardrail-passing runs,
 * for each ranking criterion -- only includes a criterion where the run
 * places in the top `limit` (default 10), i.e. it would actually be visible
 * in the Backtests page's Rankings view for that criterion. */
export function findRankPositions(
  run: BacktestRun | undefined,
  allRuns: BacktestRun[],
  limit = 10
): RankPosition[] {
  if (!run) return [];
  const positions: RankPosition[] = [];
  for (const { value, label } of RANKING_CRITERIA) {
    const ranked =
      value === "composite"
        ? rankByComposite(allRuns, allRuns.length)
        : rankByCriterion(allRuns, value, allRuns.length);
    const idx = ranked.findIndex((r) => r.id === run.id);
    if (idx !== -1 && idx < limit) {
      positions.push({ criterion: value, label, rank: idx + 1 });
    }
  }
  return positions;
}
