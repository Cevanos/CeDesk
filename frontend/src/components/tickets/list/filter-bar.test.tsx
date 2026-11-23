import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FilterBar, activeChips } from "@/components/tickets/list/filter-bar";
import type { TicketFilters } from "@/lib/api/ticket-filters";

const base: TicketFilters = {
  status: "open",
  scope: "everyone",
  priority: "",
  team: "",
  q: "",
  sort: "priority",
};

describe("activeChips", () => {
  it("is empty for default filters", () => expect(activeChips(base)).toEqual([]));

  it("describes each active filter and how to clear it", () => {
    const chips = activeChips(
      { ...base, scope: "mine", priority: "high", team: "t1", q: "refund" },
      "Billing",
    );
    expect(chips.map((c) => c.label)).toEqual(["Mine", "High priority", "Billing", "“refund”"]);
    expect(chips[0]?.clear).toEqual({ scope: null });
  });
});

describe("FilterBar", () => {
  const setup = (filters = base, text = "") => {
    const props = {
      filters,
      chips: activeChips(filters),
      text,
      onSearch: vi.fn(),
      onClearSearch: vi.fn(),
      onClearAll: vi.fn(),
      set: vi.fn(),
    };
    render(<FilterBar {...props} />);
    return props;
  };

  it("sends typed text to onSearch", async () => {
    const p = setup();
    await userEvent.type(screen.getByRole("searchbox", { name: "Search tickets" }), "a");
    expect(p.onSearch).toHaveBeenCalledWith("a");
  });

  it("removes a single filter from its chip", async () => {
    const p = setup({ ...base, scope: "mine" });
    await userEvent.click(screen.getByRole("button", { name: "Remove filter Mine" }));
    expect(p.set).toHaveBeenCalledWith({ scope: null });
  });

  it("clears the search through its own handler", async () => {
    const p = setup({ ...base, q: "x" }, "x");
    await userEvent.click(screen.getByRole("button", { name: "Remove filter “x”" }));
    expect(p.onClearSearch).toHaveBeenCalled();
    expect(p.set).not.toHaveBeenCalled();
  });

  it("clears everything", async () => {
    const p = setup({ ...base, scope: "mine" });
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(p.onClearAll).toHaveBeenCalled();
  });
});
