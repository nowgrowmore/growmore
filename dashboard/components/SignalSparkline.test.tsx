import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { SignalSparkline } from "./SignalSparkline";
import type { SignalHistoryRow } from "@/lib/types";

function row(action: "HOLD" | "BUY" | "SELL", i: number): SignalHistoryRow {
  return {
    id: `r${i}`,
    bot_config_id: "c1",
    action,
    checked_at: `2026-01-0${i}T00:00:00Z`,
    ltp: "100",
  };
}

describe("SignalSparkline", () => {
  it("renders one dot per history row", () => {
    const { container } = render(
      <SignalSparkline history={[row("HOLD", 1), row("HOLD", 2), row("BUY", 3)]} />
    );
    expect(container.querySelectorAll("[title]")).toHaveLength(3);
  });

  it("renders nothing for empty history", () => {
    const { container } = render(<SignalSparkline history={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("colors BUY and SELL distinctly from HOLD", () => {
    const { container } = render(
      <SignalSparkline history={[row("HOLD", 1), row("BUY", 2), row("SELL", 3)]} />
    );
    const dots = container.querySelectorAll("[title]");
    expect(dots[0].className).toContain("text-muted");
    expect(dots[1].className).toContain("success-text");
    expect(dots[2].className).toContain("critical-text");
  });
});
