/** A horizontal number-line: colored zones, labeled reference levels, and a
 * marker for the current value -- the same shape covers RSI (0-100 with
 * oversold/overbought zones), MACD/SMA (two markers on a price line), a
 * Donchian channel or Bollinger band (a price range with a current-price
 * marker), ADX (0-100 with a trending/ranging split), and VWAP+CPR (a price
 * range with several reference lines). One component, several strategies'
 * worth of "where am I relative to the threshold" made visual instead of
 * just prose.
 */
export interface GaugeZone {
  from: number;
  to: number;
  color: string;
  label?: string;
}

export interface GaugeReferenceLine {
  value: number;
  label: string;
}

export interface GaugeMarker {
  value: number;
  label: string;
  color?: string;
}

export interface LevelGaugeProps {
  min: number;
  max: number;
  zones?: GaugeZone[];
  referenceLines?: GaugeReferenceLine[];
  markers: GaugeMarker[];
  formatValue?: (v: number) => string;
}

function pct(value: number, min: number, max: number): number {
  if (max === min) return 50;
  const clamped = Math.min(max, Math.max(min, value));
  return ((clamped - min) / (max - min)) * 100;
}

const defaultFormat = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(2));

export function LevelGauge({
  min,
  max,
  zones = [],
  referenceLines = [],
  markers,
  formatValue = defaultFormat,
}: LevelGaugeProps) {
  return (
    <div className="w-full py-4">
      {/* Marker callouts sit above the track, positioned by value */}
      <div className="relative h-6">
        {markers.map((m, i) => (
          <div
            key={i}
            className="absolute -translate-x-1/2 whitespace-nowrap text-xs font-medium tabular-nums"
            style={{ left: `${pct(m.value, min, max)}%`, color: m.color ?? "var(--text-primary)" }}
          >
            {m.label}: {formatValue(m.value)}
          </div>
        ))}
      </div>

      {/* The track itself: zone backgrounds + marker ticks */}
      <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-[color:var(--gridline)]">
        {zones.map((z, i) => (
          <div
            key={i}
            className="absolute top-0 h-full"
            style={{
              left: `${pct(z.from, min, max)}%`,
              width: `${pct(z.to, min, max) - pct(z.from, min, max)}%`,
              backgroundColor: z.color,
              opacity: 0.35,
            }}
          />
        ))}
        {markers.map((m, i) => (
          <div
            key={i}
            className="absolute top-0 h-full w-0.5 -translate-x-1/2"
            style={{ left: `${pct(m.value, min, max)}%`, backgroundColor: m.color ?? "var(--text-primary)" }}
          />
        ))}
      </div>

      {/* Reference-line labels sit below the track, positioned by value */}
      <div className="relative mt-1 h-8">
        {referenceLines.map((r, i) => (
          <div
            key={i}
            className="absolute flex -translate-x-1/2 flex-col items-center text-[10px] text-[color:var(--text-muted)]"
            style={{ left: `${pct(r.value, min, max)}%` }}
          >
            <div className="h-1.5 w-px bg-[color:var(--text-muted)]" />
            <span className="whitespace-nowrap">
              {r.label} ({formatValue(r.value)})
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
