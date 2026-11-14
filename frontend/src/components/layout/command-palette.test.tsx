import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPalette } from "@/components/layout/command-palette";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const push = vi.fn();
const setTheme = vi.fn();
let role: "admin" | "agent" | "light_agent" = "admin";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light", setTheme }) }));
vi.mock("@/hooks/use-auth", async () => {
  const { can } = await import("@/lib/permissions");
  return { useCurrentUser: () => ({ perms: can(role) }) };
});

const API = "http://localhost:3000/api";
const noSearchResults = () =>
  server.use(
    http.get(`${API}/tickets`, () => HttpResponse.json({ items: [] })),
    http.get(`${API}/agents`, () => HttpResponse.json({ items: [] })),
  );

const open = () => act(() => void window.dispatchEvent(new Event("cdesk:palette")));

beforeEach(() => {
  noSearchResults();
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

  it("finds tickets and agents as you type", async () => {
    let q: string | null = null;
    server.use(
      http.get(`${API}/tickets`, ({ request }) => {
        q = new URL(request.url).searchParams.get("q");
        return HttpResponse.json({
          items: [
            { id: "t9", label: "#9", subject: "Refund for order", contact: { name: "Asha" } },
          ],
        });
      }),
      http.get(`${API}/agents`, () =>
        HttpResponse.json({ items: [{ member_id: "m1", name: "Refunda Singh", open_tickets: 2 }] }),
      ),
    );
    renderWithProviders(<CommandPalette />);
    open();
    await userEvent.type(await screen.findByRole("combobox"), "refund");

    expect(await screen.findByText("Refund for order")).toBeInTheDocument();
    expect(screen.getByText("Refunda Singh")).toBeInTheDocument();
    expect(q).toBe("refund");

    await userEvent.click(screen.getByText("Refund for order"));
    expect(push).toHaveBeenCalledWith("/tickets/t9");
  });

  it("opens the agents page from an agent result", async () => {
    server.use(
      http.get(`${API}/agents`, () =>
        HttpResponse.json({ items: [{ member_id: "m1", name: "Neha Rao", open_tickets: 1 }] }),
      ),
    );
    renderWithProviders(<CommandPalette />);
    open();
    await userEvent.type(await screen.findByRole("combobox"), "neha");
    await userEvent.click(await screen.findByText("Neha Rao"));
    expect(push).toHaveBeenCalledWith("/agents");
  });

  it("does not hit the API for a single character", async () => {
    const hit = vi.fn();
    server.use(http.get(`${API}/tickets`, () => (hit(), HttpResponse.json({ items: [] }))));
    renderWithProviders(<CommandPalette />);
    open();
    await userEvent.type(await screen.findByRole("combobox"), "r");
    await new Promise((r) => setTimeout(r, 400));
    expect(hit).not.toHaveBeenCalled();
  });
});
