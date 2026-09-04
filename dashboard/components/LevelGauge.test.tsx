import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LevelGauge } from "./LevelGauge";

describe("LevelGauge", () => {
  it("renders a marker's label and formatted value", () => {
    render(<LevelGauge min={0} max={100} markers={[{ value: 26.94, label: "RSI" }]} />);
    expect(screen.getByText(/RSI: 26.94/)).toBeInTheDocument();
  });

  it("renders reference line labels with their values", () => {
    render(
      <LevelGauge
        min={0}
        max={100}
        markers={[{ value: 50, label: "Current" }]}
        referenceLines={[
          { value: 30, label: "Oversold" },
          { value: 70, label: "Overbought" },
        ]}
      />
    );
    expect(screen.getByText(/Oversold \(30\)/)).toBeInTheDocument();
    expect(screen.getByText(/Overbought \(70\)/)).toBeInTheDocument();
  });

  it("clamps a marker outside the domain rather than overflowing", () => {
    const { container } = render(
      <LevelGauge min={0} max={100} markers={[{ value: 150, label: "Way over" }]} />
    );
    // Should still render without throwing, positioned at the max edge (100%).
    expect(container.querySelector('[style*="left: 100%"]')).toBeTruthy();
  });

  it("renders multiple markers independently (e.g. MACD line vs signal line)", () => {
    render(
      <LevelGauge
        min={-10}
        max={10}
        markers={[
          { value: -2, label: "MACD" },
          { value: 1, label: "Signal" },
        ]}
      />
    );
    expect(screen.getByText(/MACD: -2/)).toBeInTheDocument();
    expect(screen.getByText(/Signal: 1/)).toBeInTheDocument();
  });

  it("handles a degenerate zero-width domain without dividing by zero", () => {
    expect(() =>
      render(<LevelGauge min={5} max={5} markers={[{ value: 5, label: "Only" }]} />)
    ).not.toThrow();
  });

  it("stacks two markers onto separate rows when their values are close enough to collide", () => {
    // min=-3000, max=3000: MACD=2515.93, Signal=2748.72 differ by ~3.9% of
    // the domain -- close enough to overlap if placed on the same row.
    const { container } = render(
      <LevelGauge
        min={-3000}
        max={3000}
        markers={[
          { value: 2515.93, label: "MACD" },
          { value: 2748.72, label: "Signal" },
        ]}
      />
    );
    const macd = screen.getByText(/MACD: 2515.93/);
    const signal = screen.getByText(/Signal: 2748.72/);
    expect(macd.style.top).not.toEqual(signal.style.top);
    // The taller callout area only kicks in when a collision was detected.
    expect(container.querySelector(".h-10")).toBeTruthy();
  });

  it("keeps two far-apart markers on the same row", () => {
    const { container } = render(
      <LevelGauge
        min={-10}
        max={10}
        markers={[
          { value: -8, label: "MACD" },
          { value: 8, label: "Signal" },
        ]}
      />
    );
    const macd = screen.getByText(/MACD: -8/);
    const signal = screen.getByText(/Signal: 8/);
    expect(macd.style.top).toEqual(signal.style.top);
    expect(container.querySelector(".h-10")).toBeFalsy();
  });
});
