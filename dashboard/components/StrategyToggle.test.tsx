import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrategyToggle } from "./StrategyToggle";

describe("StrategyToggle", () => {
  it("optimistically flips state and calls onToggle with the new value", async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<StrategyToggle configId="config-1" initialEnabled={false} onToggle={onToggle} />);

    const toggle = screen.getByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "false");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(onToggle).toHaveBeenCalledWith("config-1", true));
  });

  it("reverts and shows an error if the write fails", async () => {
    const onToggle = vi.fn().mockRejectedValue(new Error("db down"));
    const user = userEvent.setup();
    render(<StrategyToggle configId="config-1" initialEnabled={true} onToggle={onToggle} />);

    const toggle = screen.getByRole("switch");
    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(await screen.findByText(/failed to save/i)).toBeInTheDocument();
  });
});
