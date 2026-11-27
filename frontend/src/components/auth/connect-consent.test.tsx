import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectConsent } from "@/components/auth/connect-consent";
import { server } from "@/test/server";

const nav = vi.hoisted(() => ({ go: vi.fn(), search: new URLSearchParams() }));
vi.mock("@/lib/navigation", () => ({ hardNavigate: nav.go }));
vi.mock("next/navigation", () => ({ useSearchParams: () => nav.search }));

const CONSENT = {
  app: {
    client_id: "https://claude.example/client.json",
    name: "Claude",
    client_uri: null,
    logo_uri: null,
    registration: "metadata_document",
    domain: "claude.example",
  },
  redirect_host: "claude.example",
  scopes: ["read", "write"],
  resource: "https://desk.test/mcp",
  workspace: { id: "w1", name: "Acme Support", slug: "acme" },
  member: { id: "m1", name: "Neha", email: "neha@acme.test" },
};

function open(search: string) {
  nav.search = new URLSearchParams(search);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ConnectConsent />
    </QueryClientProvider>,
  );
}

beforeEach(() => nav.go.mockClear());

describe("ConnectConsent", () => {
  it("shows the app, the member, the workspace and where the code goes", async () => {
    server.use(http.get("*/api/oauth/consent", () => HttpResponse.json(CONSENT)));
    open("?request=h1");
    expect(await screen.findByRole("heading", { name: "Connect Claude?" })).toBeTruthy();
    expect(document.body.textContent).toContain("Acme Support");
    expect(document.body.textContent).toContain("neha@acme.test");
    expect(document.body.textContent).toContain("claude.example");
  });

  it("approving sends the answer and goes back to the app", async () => {
    let sent: unknown = null;
    server.use(
      http.get("*/api/oauth/consent", () => HttpResponse.json(CONSENT)),
      http.post("*/api/oauth/consent", async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ redirect_to: "https://claude.example/cb?code=c&iss=x" });
      }),
    );
    open("?request=h1");
    fireEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() =>
      expect(nav.go).toHaveBeenCalledWith("https://claude.example/cb?code=c&iss=x"),
    );
    expect(sent).toEqual({ request: "h1", decision: "approve" });
  });

  it("denying goes back too", async () => {
    server.use(
      http.get("*/api/oauth/consent", () => HttpResponse.json(CONSENT)),
      http.post("*/api/oauth/consent", () =>
        HttpResponse.json({ redirect_to: "https://claude.example/cb?error=access_denied" }),
      ),
    );
    open("?request=h1");
    fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
    await waitFor(() =>
      expect(nav.go).toHaveBeenCalledWith("https://claude.example/cb?error=access_denied"),
    );
  });

  it("points to two-step setup when the workspace requires it", async () => {
    server.use(
      http.get("*/api/oauth/consent", () =>
        HttpResponse.json(
          { error: { code: "TwoFactorSetupRequired", message: "Set it up." } },
          { status: 403 },
        ),
      ),
    );
    open("?request=h1");
    expect((await screen.findByRole("alert")).textContent).toContain("two-step");
    expect(screen.getByRole("link", { name: "Set it up" })).toBeTruthy();
  });

  it("says so when the link has no request", async () => {
    open("");
    expect((await screen.findByRole("alert")).textContent).toContain("missing");
  });
});
