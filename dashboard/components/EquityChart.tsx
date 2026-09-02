"use client";

import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";

export interface EquityChartPoint {
  ts: string;
  equity: number;
}

interface EquityChartProps {
  points: EquityChartPoint[];
  /** ISO strings are shortened to this for the x-axis; full value shows in the tooltip. */
  dateFormatter?: (iso: string) => string;
}

const defaultDateFormatter = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

/**
 * Equity curve for a single backtest run. One series -> no legend needed
 * (the chart title names it); styled from the CSS custom properties in
 * app/globals.css so it tracks the dataviz reference palette in both
 * themes. NOTE for a human reviewer: this was built without the dataviz
 * skill's interactive validator pass — worth a follow-up review before this
 * ships broadly (see README "Known follow-ups").
 */
export function EquityChart({ points, dateFormatter = defaultDateFormatter }: EquityChartProps) {
  if (points.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-[color:var(--text-muted)]">
        No equity curve data for this run.
      </div>
    );
  }

  return (
    <div className="h-64 w-full" role="img" aria-label="Equity curve over the backtest period">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--gridline)" strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="ts"
            tickFormatter={dateFormatter}
            stroke="var(--baseline)"
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <YAxis
            stroke="var(--baseline)"
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            width={64}
          />
          <Tooltip
            labelFormatter={(iso: string) => new Date(iso).toLocaleString()}
            formatter={(value: number) => [value.toLocaleString(), "Equity"]}
            contentStyle={{
              background: "var(--surface-1)",
              border: "1px solid var(--border-hairline)",
              color: "var(--text-primary)",
              fontSize: 12,
            }}
          />
          <Line
            type="monotone"
            dataKey="equity"
            stroke="var(--series-1)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
