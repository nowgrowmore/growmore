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
    futures_roll_cost_per_lot: "0",
    stop_loss_premium_multiple: null,
    min_dte_days: null,
    max_dte_days: null,
    min_credit_pct_of_strike: null,
    max_relative_spread: null,
    use_bid_for_entry_premium: false,
    fallback_sigma: null,
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
    expect(screen.getAllByText("24 Sept 2026")).toHaveLength(2);
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

// ---------------------------------------------------------------------------
// Independent code review, 2026-09-15. Each case below pins one defect the
// review found on this page -- see each test's own comment.
// ---------------------------------------------------------------------------

function rollLeg(overrides: Partial<MCXOptionsLeg> = {}): MCXOptionsLeg {
  return {
    id: "leg-roll",
    position_id: "pos-1",
    cycle_expiry: "2026-10-28", // the NEW futures contract's expiry, not an option's
    opt_type: "ROLL",
    strike: null,
    premium: null,
    lots: "1",
    action: "roll",
    opened_at: "2026-09-30T00:00:00Z",
    settled_at: "2026-09-30T00:00:00Z",
    assigned: false,
    called_away: false,
    pnl: "-450",
    ...overrides,
  };
}

describe("MCXOptionsClient — review fixes", () => {
  it("renders a ROLL leg as a roll, not as a ₹0 option that expired OTM", () => {
    // D1: `formatCurrency(toNumber(null))` rendered the roll's null
    // strike/premium as "₹0.00", and because a roll has assigned=false,
    // called_away=false and settled_at set, the outcome ladder fell through
    // to "Expired OTM". The page's own intro prose promises rolls show up
    // "as a 'roll' leg" -- they did, with invented numbers and a wrong
    // outcome.
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{ "config-1": [rollLeg()] }}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    const row = screen.getByText("ROLL").closest("tr")!;
    expect(within(row).getByText("Rolled")).toBeInTheDocument();
    expect(within(row).queryByText("Expired OTM")).not.toBeInTheDocument();
    expect(within(row).queryByText("₹0.00")).not.toBeInTheDocument();
    // The roll's transaction cost is still a real, meaningful number.
    expect(within(row).getByText("-₹450.00")).toBeInTheDocument();
  });

  it("renders date-only columns in IST, not in the viewer's timezone", () => {
    // D2: Postgres `date` columns (oid 1082) come back from postgres.js as
    // JS Date objects at UTC MIDNIGHT, and every render called
    // `.toLocaleDateString()` with no timeZone -- so any viewer west of UTC
    // saw a 24 Sep expiry as 23 Sep, and SSR (UTC on Vercel) disagreed with
    // the client on the same row. The bot is rigorously IST-aware; the UI
    // was the only timezone-naive layer.
    const leg: MCXOptionsLeg = {
      ...rollLeg(),
      id: "leg-pe",
      opt_type: "PE",
      strike: "59000",
      premium: "500",
      action: "sell_put",
      cycle_expiry: new Date("2026-09-24") as unknown as string,
      settled_at: null,
      pnl: null,
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
    const row = screen.getByText("PE").closest("tr")!;
    expect(within(row).getByText("24 Sept 2026")).toBeInTheDocument();
    expect(within(row).queryByText("23 Sept 2026")).not.toBeInTheDocument();
  });

  it("renders a genuinely zero unrealized P&L as ₹0.00, not as '—'", () => {
    // D4: the same falsy-zero bug already fixed for `leg.pnl` survived in six
    // other places. It is currently masked only because postgres.js returns
    // `numeric` as the string "0.00" (truthy) -- one `::float8` cast, JSON
    // round-trip or hand-built fixture and a real 0 silently reads as
    // missing data. Zero unrealized P&L is the NORMAL state for a flat
    // position, not an absent one.
    const selection: MCXOptionsSelection = {
      id: "sel-zero",
      config_id: "config-1",
      cycle_date: "2026-09-14",
      regime: "consolidating",
      target_delta: "0.30",
      selected_strike: null,
      reason: "no strike cleared the filters",
      futures_price: "6000",
      position_state: "flat",
      position_basis: null,
      position_unrealized_pnl: 0 as unknown as string,
      candidates_considered: [],
      option_expiry: "2026-09-24",
      created_at: "2026-09-14T18:29:00Z",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": [selection] }}
        onToggle={vi.fn()}
      />
    );
    const row = screen.getByText("no strike cleared the filters").closest("tr")!;
    // formatCurrency deliberately omits the sign for an exact zero.
    // (Other cells on this row legitimately show "—" -- selected_strike and
    // position_basis really are null here. Before the fix, the unrealized
    // cell joined them and this ₹0.00 did not exist anywhere on the row.)
    expect(within(row).getByText("₹0.00")).toBeInTheDocument();
  });

  it("does not crash on a malformed candidates_considered entry", () => {
    // D5: `c.delta.toFixed(2)` / `c.oi.toLocaleString()` run against
    // untrusted JSONB. A null/string/missing field threw during render, and
    // with no error.tsx anywhere under app/ that blanked the entire app
    // shell rather than just this cell.
    const selection: MCXOptionsSelection = {
      id: "sel-bad",
      config_id: "config-1",
      cycle_date: "2026-09-14",
      regime: "consolidating",
      target_delta: "0.30",
      selected_strike: "5900",
      reason: "entered PE at strike 5900",
      futures_price: "6000",
      position_state: "flat",
      position_basis: null,
      position_unrealized_pnl: null,
      candidates_considered: [
        { strike: 5900, delta: -0.3, oi: 5000, ltp: 42.5 },
        { strike: 5850, delta: null, oi: "lots", ltp: 30 },
        { delta: -0.2, oi: 10, ltp: 20 },
      ] as never,
      option_expiry: "2026-09-24",
      created_at: "2026-09-14T18:29:00Z",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{ "config-1": [selection] }}
        onToggle={vi.fn()}
      />
    );
    // The one well-formed candidate still renders; the junk is dropped.
    expect(screen.getByText(/Δ-0\.30/)).toBeInTheDocument();
  });

  it("shows each leg's lots so premium can be reconciled against realized P&L", () => {
    // D6: `realized_pnl` is booked as `premium * lots * lot_size`, but the
    // trade-history table showed only the PER-UNIT premium with no lots
    // column anywhere -- for SILVERM that is a 5x gap the reader had to know
    // about out of band.
    const leg: MCXOptionsLeg = {
      ...rollLeg(),
      id: "leg-pe",
      opt_type: "PE",
      strike: "59000",
      premium: "500",
      lots: "3",
      action: "sell_put",
      settled_at: null,
      pnl: null,
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
    const row = screen.getByText("PE").closest("tr")!;
    expect(within(row).getByText("3")).toBeInTheDocument();
  });

  it("renders target delta as a delta, not as a percentage", () => {
    // D7: the header showed a delta of 0.30 as "30%" while the candidate
    // list right beside it showed "Δ-0.30" -- the same quantity in two
    // incompatible conventions, with opposite signs. Delta is a unitless
    // hedge ratio.
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/Δ0\.30/)).toBeInTheDocument();
    expect(screen.queryByText(/30%/)).not.toBeInTheDocument();
  });

  it("sums unrealized P&L over the same positions the table shows it for", () => {
    // D10: the card summed `unrealized_pnl` over ALL open positions while
    // the table printed "—" unless state === 'long_futures', so a stale
    // value on a flat position would appear in the total with nothing in
    // the table to account for it.
    const flatWithStaleUnrealized: MCXOptionsPosition = {
      id: "pos-flat",
      config_id: "config-1",
      status: "open",
      state: "flat",
      basis: null,
      futures_qty: "0",
      futures_contract_expiry: null,
      opened_at: "2026-09-01T00:00:00Z",
      closed_at: null,
      realized_pnl: "1000",
      unrealized_pnl: "9999",
    };
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{ "config-1": [flatWithStaleUnrealized] }}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    const card = screen
      .getAllByText("Unrealized P&L")
      .find((el) => el.tagName === "DT")!
      .closest("div")!;
    expect(within(card).getByText("₹0.00")).toBeInTheDocument();
  });
});

describe("MCXOptionsClient — risk flags", () => {
  it("says plainly that no risk flags are enabled, rather than staying silent", () => {
    render(
      <MCXOptionsClient
        configs={[config()]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/none enabled \(no stop-loss/)).toBeInTheDocument();
  });

  it("surfaces each flag that IS set", () => {
    render(
      <MCXOptionsClient
        configs={[
          config({
            stop_loss_premium_multiple: "2.5",
            min_dte_days: 7,
            max_dte_days: 45,
            use_bid_for_entry_premium: true,
          }),
        ]}
        positionsByConfigId={{}}
        legsByConfigId={{}}
        selectionsByConfigId={{}}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText(/stop-loss at 2\.5× premium/)).toBeInTheDocument();
    expect(screen.getByText(/DTE 7…45d/)).toBeInTheDocument();
    expect(screen.getByText(/entry priced at bid/)).toBeInTheDocument();
  });
});
