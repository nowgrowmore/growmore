import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MCXOptionsClient } from "./MCXOptionsClient";
import type {
  MCXOptionsConfig,
  MCXOptionsLeg,
  MCXOptionsPosition,
  MCXOptionsSelection,
} from "@/lib/types";

function config(overrides: Partial<MCXOptionsConfig> = {}): MCXOptionsConfig {
  return {
    id: "config-1",
    strategy_id: "strategy-1",
    enabled: true,
    mode: "paper",
    symbol: "GOLDM",
    lots: 1,
    consolidating_target_delta: "0.30",
    trend_favorable_target_delta: "0.50",
    min_open_interest: 100,
    margin_multiple_of_premium: "3.0",
    updated_at: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

describe("MCXOptionsClient", () => {
  it("renders the config summary and toggle", () => {
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("GOLDM options wheel")).toBeInTheDocument();
    expect(screen.getByText(/Lots 1/)).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("renders an open long-futures position's basis and unrealized P&L", () => {
    const position: MCXOptionsPosition = {
      id: "pos-1",
      config_id: "config-1",
      status: "open",
      state: "long_futures",
      basis: "62000",
      futures_qty: "100",
      futures_contract_expiry: "2026-10-28",
      opened_at: "2026-09-01T00:00:00Z",
      closed_at: null,
      realized_pnl: "0",
      unrealized_pnl: "1250.50",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{ "config-1": [position] }}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("Long futures (covered)")).toBeInTheDocument();
  });

  it("surfaces the open leg on a 'flat' position that has an unsettled short put", () => {
    // Regression: `state` only tracks FUTURES exposure -- a freshly sold put
    // with no assignment yet is legitimately "flat", which used to make the
    // current-position card show nothing (State=Flat, Basis=—, Futures
    // qty=0, Contract expiry=—) even while Trade History showed a real open
    // short put. The open leg must show up in the current-position card too.
    const position: MCXOptionsPosition = {
      id: "pos-1",
      config_id: "config-1",
      status: "open",
      state: "flat",
      basis: null,
      futures_qty: "0",
      futures_contract_expiry: null,
      opened_at: "2026-09-14T00:00:00Z",
      closed_at: null,
      realized_pnl: "0",
      unrealized_pnl: "0",
    };
    const leg: MCXOptionsLeg = {
      id: "leg-1",
      position_id: "pos-1",
      cycle_expiry: "2026-09-24",
      opt_type: "PE",
      strike: "207000",
      premium: "27409.50",
      lots: "1",
      action: "sell_put",
      opened_at: "2026-09-14T00:00:00Z",
      settled_at: null,
      assigned: false,
      called_away: false,
      pnl: null,
    };
    render(
      <MCXOptionsClient
        configs={[config({ symbol: "SILVERM" })]}
        positionsByConfigId={{ "config-1": [position] }}
        legsByConfigId={{ "config-1": [leg] }}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("Flat")).toBeInTheDocument();
    expect(screen.getByText(/Short PE \(put\) @ ₹2,07,000\.00/)).toBeInTheDocument();
    expect(screen.getByText(/premium ₹27,409\.50/)).toBeInTheDocument();
  });

  it("renders the selection log with the reason text, including skipped cycles", () => {
    const selections: MCXOptionsSelection[] = [
      {
        id: "sel-1",
        config_id: "config-1",
        cycle_date: "2026-09-01",
        regime: "consolidating",
        target_delta: "0.30",
        selected_strike: "61000",
        reason: "Consolidating regime, target delta 0.30 -- sold 61000 PE",
        futures_price: "61500.25",
        position_state: "flat",
        position_basis: null,
        position_unrealized_pnl: null,
        candidates_considered: [
          { strike: 60500, delta: 0.18, oi: 4200, ltp: 120.5 },
          { strike: 61000, delta: 0.3, oi: 5000, ltp: 210.0 },
          { strike: 61500, delta: 0.45, oi: 3100, ltp: 340.25 },
        ],
        option_expiry: "2026-09-24",
        created_at: "2026-09-01T00:00:00Z",
      },
      {
        id: "sel-2",
        config_id: "config-1",
        cycle_date: "2026-08-25",
        regime: "trend_unfavorable",
        target_delta: null,
        selected_strike: null,
        reason: "Trend unfavorable -- no entry",
        futures_price: "60800.00",
        position_state: null,
        position_basis: null,
        position_unrealized_pnl: null,
        candidates_considered: null,
        option_expiry: "2026-09-24",
        created_at: "2026-08-25T00:00:00Z",
      },
    ];
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": selections }}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/sold 61000 PE/)).toBeInTheDocument();
    expect(screen.getByText(/no entry/)).toBeInTheDocument();
    expect(screen.getByText("Consolidating")).toBeInTheDocument();
    expect(screen.getByText("Trend unfavorable")).toBeInTheDocument();
  });

  it("renders the daily snapshot fields and every evaluated candidate, with the picked one distinguished", () => {
    const selections: MCXOptionsSelection[] = [
      {
        id: "sel-1",
        config_id: "config-1",
        cycle_date: "2026-09-01",
        regime: "consolidating",
        target_delta: "0.30",
        selected_strike: "61000",
        reason: "entered PE at 61000",
        futures_price: "61500.25",
        position_state: "flat",
        position_basis: null,
        position_unrealized_pnl: null,
        candidates_considered: [
          { strike: 60500, delta: 0.18, oi: 4200, ltp: 120.5 },
          { strike: 61000, delta: 0.3, oi: 5000, ltp: 210.0 },
        ],
        option_expiry: "2026-09-24",
        created_at: "2026-09-01T00:00:00Z",
      },
      {
        id: "sel-2",
        config_id: "config-1",
        cycle_date: "2026-08-31",
        regime: "consolidating",
        target_delta: null,
        selected_strike: null,
        reason: "position already has an open leg, not due for settlement today",
        futures_price: "61200.00",
        position_state: "long_futures",
        position_basis: "61000",
        position_unrealized_pnl: "1250.5",
        candidates_considered: null,
        option_expiry: "2026-09-24",
        created_at: "2026-08-31T00:00:00Z",
      },
    ];
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": selections }}
        onToggle={vi.fn()}
      />
    );

    // Every evaluated candidate is shown, not just the winner.
    expect(screen.getByText(/60,500/)).toBeInTheDocument();
    const pickedEntry = screen.getByText(/61,000.*Δ0\.30/);
    expect(pickedEntry).toBeInTheDocument();
    expect(pickedEntry.className).toMatch(/font-semibold/);

    // Hold-day row's position snapshot is populated.
    expect(screen.getByText("Long futures (covered)")).toBeInTheDocument();
  });

  it("renders trade history rows with outcome labels", () => {
    const leg: MCXOptionsLeg = {
      id: "leg-1",
      position_id: "pos-1",
      cycle_expiry: "2026-09-24",
      opt_type: "PE",
      strike: "61000",
      premium: "350",
      lots: "1",
      action: "sell_put",
      opened_at: "2026-08-27T00:00:00Z",
      settled_at: "2026-09-24T00:00:00Z",
      assigned: true,
      called_away: false,
      pnl: "35000",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{ "config-1": [leg] }}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("sell put")).toBeInTheDocument();
    expect(screen.getByText("Assigned")).toBeInTheDocument();
  });

  it("clarifies an open leg's P&L instead of a bare dash, and shows a settled zero P&L", () => {
    // Regression: the top-level Realized P&L card credits an option's
    // premium the moment it's sold, but the leg itself only gets a `pnl`
    // once it settles -- so an open leg's P&L cell showing a bare "--" next
    // to a nonzero Realized P&L card looked like a contradiction (reported
    // directly against real GOLDM data: Realized P&L +21,065.00, but the
    // still-open leg's row showed "--"). Also covers the sibling bug where
    // `leg.pnl ? ... : "--"` would wrongly show "--" for an exactly-zero
    // settled P&L, since 0 is falsy.
    const openLeg: MCXOptionsLeg = {
      id: "leg-open",
      position_id: "pos-1",
      cycle_expiry: "2026-09-25",
      opt_type: "PE",
      strike: "149000",
      premium: "2106.50",
      lots: "1",
      action: "sell_put",
      opened_at: "2026-09-14T00:00:00Z",
      settled_at: null,
      assigned: false,
      called_away: false,
      pnl: null,
    };
    const settledZeroLeg: MCXOptionsLeg = {
      id: "leg-zero",
      position_id: "pos-1",
      cycle_expiry: "2026-08-24",
      opt_type: "PE",
      strike: "140000",
      premium: "1500",
      lots: "1",
      action: "sell_put",
      opened_at: "2026-07-27T00:00:00Z",
      settled_at: "2026-08-24T00:00:00Z",
      assigned: false,
      called_away: false,
      pnl: "0",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{ "config-1": [openLeg, settledZeroLeg] }}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/open — premium already in Realized P&L/)).toBeInTheDocument();
    // The settled zero-P&L leg's row must show a real "₹0.00", not a bare
    // "--" (the old `leg.pnl ? ... : "--"` treated 0 as falsy) -- there are
    // other legitimate ₹0.00s on the page (e.g. the Unrealized P&L card), so
    // scope the check to this leg's own table row.
    const zeroRow = screen.getByText("Expired OTM").closest("tr");
    expect(zeroRow).not.toBeNull();
    expect(within(zeroRow as HTMLElement).getByText("₹0.00")).toBeInTheDocument();
  });

  it("shows the option expiry and the real cycle timestamp, not just the date", () => {
    // Regression: two manually-triggered cycles landed on the same
    // cycle_date (before/after a same-day bug fix), and with only a
    // date-level "Date" column they were indistinguishable and the expiry
    // being considered wasn't shown anywhere for a SKIPPED cycle at all
    // (MCXOptionsLeg.cycle_expiry only exists once an entry actually
    // happens).
    const selections: MCXOptionsSelection[] = [
      {
        id: "sel-early",
        config_id: "config-1",
        cycle_date: "2026-09-14",
        regime: "consolidating",
        target_delta: "0.30",
        selected_strike: "207000",
        reason: "entered PE at strike 207000.0",
        futures_price: "236895",
        position_state: "flat",
        position_basis: null,
        position_unrealized_pnl: null,
        candidates_considered: [{ strike: 207000, delta: -0.3, oi: 0, ltp: 27409.5 }],
        option_expiry: "2026-09-24",
        created_at: "2026-09-14T13:43:00Z",
      },
      {
        id: "sel-later",
        config_id: "config-1",
        cycle_date: "2026-09-14",
        regime: "consolidating",
        target_delta: "0.30",
        selected_strike: "232000",
        reason: "entered PE at strike 232000.0",
        futures_price: "236895",
        position_state: "flat",
        position_basis: null,
        position_unrealized_pnl: null,
        candidates_considered: [{ strike: 232000, delta: -0.31, oi: 539, ltp: 5400 }],
        option_expiry: "2026-09-24",
        created_at: "2026-09-14T15:12:00Z",
      },
    ];
    render(
      <MCXOptionsClient
        configs={[config({ symbol: "SILVERM" })]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": selections }}
        onToggle={vi.fn()}
      />
    );
    // Both rows' expiry is shown, even though neither has a settled leg yet.
    expect(screen.getAllByText("9/24/2026")).toHaveLength(2);
    // The two same-day cycles render distinct, non-date-only timestamps.
    const earlyRow = screen.getByText(/207000/).closest("tr") as HTMLElement;
    const laterRow = screen.getByText(/232000/).closest("tr") as HTMLElement;
    const earlyTime = within(earlyRow).getAllByText(/2026/)[0].textContent;
    const laterTime = within(laterRow).getAllByText(/2026/)[0].textContent;
    expect(earlyTime).not.toEqual(laterTime);
  });

  it("hides low-OI candidates and caps the evaluated list at the 10 closest to target delta", () => {
    // Mirrors a real thin-market cycle: lots of near-zero-OI strikes far from
    // the target delta, plus a handful of liquid ones actually worth showing.
    const farOtmNoise = Array.from({ length: 20 }, (_, i) => ({
      strike: 180000 + i * 1000,
      delta: -0.02 - i * 0.005,
      oi: i % 3, // 0, 1, or 2 -- all below the display floor
      ltp: 5,
    }));
    const liquidCandidates = [
      { strike: 148000, delta: -0.35, oi: 500, ltp: 900 },
      { strike: 149000, delta: -0.3, oi: 620, ltp: 750 }, // the picked strike
      { strike: 150000, delta: -0.25, oi: 480, ltp: 600 },
    ];
    const selections: MCXOptionsSelection[] = [
      {
        id: "sel-1",
        config_id: "config-1",
        cycle_date: "2026-09-14",
        regime: "consolidating",
        target_delta: "0.30",
        selected_strike: "149000",
        reason: "entered PE at 149000",
        futures_price: "152978",
        position_state: "flat",
        position_basis: null,
        position_unrealized_pnl: null,
        candidates_considered: [...farOtmNoise, ...liquidCandidates],
        option_expiry: "2026-09-24",
        created_at: "2026-09-14T00:00:00Z",
      },
    ];
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": selections }}
        onToggle={vi.fn()}
      />
    );
    // The liquid, near-target candidates show...
    expect(screen.getByText(/₹1,49,000\.00.*Δ-0\.30.*OI 620/)).toBeInTheDocument();
    // ...but the noisy far-OTM zero/near-zero-OI strikes don't.
    expect(screen.queryByText(/OI 0\)/)).not.toBeInTheDocument();
    expect(screen.getByText(/more \(low OI \/ far from target delta, hidden\)/)).toBeInTheDocument();
  });

  it("renders one section per config when there are several", () => {
    render(
      <MCXOptionsClient
        configs={[config({ id: "config-1", symbol: "GOLDM" }), config({ id: "config-2", symbol: "SILVERM" })]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("GOLDM options wheel")).toBeInTheDocument();
    expect(screen.getByText("SILVERM options wheel")).toBeInTheDocument();
  });
});
