import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ssoHref } from "@/components/auth/login-form";
import { SsoCallback } from "@/components/auth/sso-callback";
import { server } from "@/test/server";

const nav = vi.hoisted(() => ({ go: vi.fn() }));
vi.mock("@/lib/navigation", () => ({ hardNavigate: nav.go }));

function open(hash: string) {
  window.history.replaceState(null, "", `/sso/callback${hash}`);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SsoCallback />
    </QueryClientProvider>,
  );
}
const TOKEN = "tok_abcdefghijklmnop1234";

beforeEach(() => nav.go.mockClear());

describe("SsoCallback", () => {
  it("clears the fragment and goes to next on success", async () => {
    server.use(http.post("*/api/auth/sso-session", () => HttpResponse.json({ role: "agent" })));
    open(`#token=${TOKEN}&expires_at=2030-01-01T00:00:00Z&next=/reports`);
    expect(window.location.hash).toBe("");
    await waitFor(() => expect(nav.go).toHaveBeenCalledWith("/reports"));
  });

  it("falls back to /tickets for an unsafe next", async () => {
    server.use(http.post("*/api/auth/sso-session", () => HttpResponse.json({})));
    open(`#token=${TOKEN}&next=//evil.test`);
    await waitFor(() => expect(nav.go).toHaveBeenCalledWith("/tickets"));
  });

  it("shows the API's message on error", async () => {
    open("#error=That%20person%20isn%27t%20an%20active%20member");
    expect((await screen.findByRole("alert")).textContent).toContain("isn't an active member");
    expect(screen.getByRole("link", { name: "Back to sign in" })).toBeTruthy();
    expect(window.location.hash).toBe("");
  });

  it("shows an error when there is no token", async () => {
    open("");
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(nav.go).not.toHaveBeenCalled();
  });

  it("shows a generic error when the proxy refuses the token", async () => {
    server.use(
      http.post("*/api/auth/sso-session", () =>
        HttpResponse.json({ error: { code: "NotSignedIn", message: "x" } }, { status: 401 }),
      ),
    );
    open(`#token=${TOKEN}`);
    expect((await screen.findByRole("alert")).textContent).toContain("not valid");
    expect(nav.go).not.toHaveBeenCalled();
  });
});

describe("ssoHref", () => {
  it("uses the API start_url and adds a safe next", () => {
    expect(ssoHref("https://api.test/api/v1/sso/acme/start", "acme", "/reports")).toBe(
      "https://api.test/api/v1/sso/acme/start?next=%2Freports",
    );
    expect(ssoHref("https://api.test/s", "acme", "//evil")).toBe("https://api.test/s");
  });
  it("falls back to the proxy path without start_url", () => {
    expect(ssoHref(null, "acme", null)).toBe("/api/sso/acme/start");
  });
});
