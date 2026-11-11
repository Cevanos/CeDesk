import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPalette } from "@/components/layout/command-palette";
import { renderWithProviders } from "@/test/render";

const push = vi.fn();
const setTheme = vi.fn();
let role: "admin" | "agent" | "light_agent" = "admin";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light", setTheme }) }));
vi.mock("@/hooks/use-auth", async () => {
  const { can } = await import("@/lib/permissions");
  return { useCurrentUser: () => ({ perms: can(role) }) };
});

const open = () => act(() => void window.dispatchEvent(new Event("cdesk:palette")));

beforeEach(() => {
  push.mockClear();
  setTheme.mockClear();
  role = "admin";
});

describe("CommandPalette", () => {
  it("is closed until asked", () => {
    renderWithProviders(<CommandPalette />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens with Ctrl+K and focuses the search box", async () => {
    renderWithProviders(<CommandPalette />);
    await userEvent.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveFocus();
  });

  it("filters, then navigates with Enter", async () => {
    renderWithProviders(<CommandPalette />);
    open();
    await userEvent.type(await screen.findByRole("combobox"), "agents");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    await userEvent.keyboard("{Enter}");
    expect(push).toHaveBeenCalledWith("/agents");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("moves the highlight with the arrow keys", async () => {
    renderWithProviders(<CommandPalette />);
    open();
    await screen.findByRole("combobox");
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
  });

  it("closes on Escape", async () => {
    renderWithProviders(<CommandPalette />);
    open();
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("only offers settings to admins", async () => {
    role = "agent";
    renderWithProviders(<CommandPalette />);
    open();
    await screen.findByRole("dialog");
    expect(screen.queryByText("Settings: members")).not.toBeInTheDocument();
    expect(screen.getByText("Create a new ticket")).toBeInTheDocument();
  });

  it("hides ticket creation from light agents", async () => {
    role = "light_agent";
    renderWithProviders(<CommandPalette />);
    open();
    await screen.findByRole("dialog");
    expect(screen.queryByText("Create a new ticket")).not.toBeInTheDocument();
  });

  it("toggles the theme", async () => {
    renderWithProviders(<CommandPalette />);
    open();
    await userEvent.type(await screen.findByRole("combobox"), "theme");
    await userEvent.keyboard("{Enter}");
    expect(setTheme).toHaveBeenCalledWith("dark");
  });
});
