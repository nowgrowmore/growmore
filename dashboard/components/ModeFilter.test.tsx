import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModeFilter } from "./ModeFilter";

const OPTIONS = [
  { value: "all" as const, label: "All" },
  { value: "paper" as const, label: "Paper" },
  { value: "live" as const, label: "Live" },
];

describe("ModeFilter", () => {
  it("renders all options and marks the active one selected", () => {
    render(<ModeFilter value="paper" onChange={() => {}} options={OPTIONS} />);
    expect(screen.getByRole("tab", { name: "Paper" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Live" })).toHaveAttribute("aria-selected", "false");
  });

  it("calls onChange with the clicked option's value", async () => {
    const onChange = vi.fn();
    render(<ModeFilter value="all" onChange={onChange} options={OPTIONS} />);
    await userEvent.click(screen.getByRole("tab", { name: "Live" }));
    expect(onChange).toHaveBeenCalledWith("live");
  });
});
