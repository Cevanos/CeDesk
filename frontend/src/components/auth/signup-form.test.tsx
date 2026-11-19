import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";
import { SignupForm } from "@/components/auth/signup-form";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const nav = vi.hoisted(() => ({ replace: vi.fn(), search: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, refresh: vi.fn(), push: vi.fn() }),
  useSearchParams: () => nav.search,
}));

const ack = (over = {}) => ({
  signup_id: "sid-1",
  code_expires_at: new Date(Date.now() + 600_000).toISOString(),
  resend_after: new Date(Date.now() + 60_000).toISOString(),
  ...over,
});
const err = (status: number, code: string, message = "msg", headers = {}) =>
  HttpResponse.json({ error: { code, message } }, { status, headers });

const config = (enabled = true) =>
  http.get("*/api/auth/signup-config", () =>
    HttpResponse.json({ enabled, captcha_site_key: null }),
  );
const configGone = http.get("*/api/auth/signup-config", () => err(404, "NotFound"));

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Workspace name"), "Acme Support");
  await user.type(screen.getByLabelText("Your name"), "Priya Shah");
  await user.type(screen.getByLabelText("Email"), "priya@acme.com");
  await user.type(screen.getByLabelText("Password"), "a-long-password");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));
}

beforeEach(() => {
  nav.replace.mockClear();
  nav.search = new URLSearchParams();
});

describe("SignupForm", () => {
  it("suggests an address from the workspace name", async () => {
    server.use(config());
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await user.type(await screen.findByLabelText("Workspace name"), "Acme Support");
    expect(screen.getByLabelText("Workspace address")).toHaveValue("acme-support");
  });

  it("happy path: form, code, redirect to login with the workspace", async () => {
    let sent: Record<string, unknown> = {};
    server.use(
      config(),
      http.post("*/api/auth/signup", async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ack(), { status: 202 });
      }),
      http.post("*/api/auth/signup/verify", async ({ request }) => {
        expect(await request.json()).toEqual({ signup_id: "sid-1", code: "123456" });
        return HttpResponse.json({ workspace: "acme-support" });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    expect(sent.workspace).toBe("acme-support");
    const box = await screen.findByLabelText("6 digit code");
    await waitFor(() => expect(box).toHaveFocus());
    await user.type(box, "123 456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?workspace=acme-support"));
  });

  it("shows slug_taken under the address field", async () => {
    server.use(
      config(),
      http.post("*/api/auth/signup", () => err(409, "slug_taken")),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    expect(await screen.findByText("That address is taken. Try another.")).toBeInTheDocument();
  });

  it("asks for a new address when it is taken at verify, then resends the same code", async () => {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      config(),
      http.post("*/api/auth/signup", () => HttpResponse.json(ack(), { status: 202 })),
      http.post("*/api/auth/signup/verify", async ({ request }) => {
        const b = (await request.json()) as Record<string, unknown>;
        bodies.push(b);
        return b.workspace ? HttpResponse.json({ workspace: b.workspace }) : err(409, "slug_taken");
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    await user.type(await screen.findByLabelText("6 digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    const addr = await screen.findByLabelText("Workspace address");
    await user.clear(addr);
    await user.type(addr, "acme-two");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?workspace=acme-two"));
    expect(bodies[1]).toEqual({ signup_id: "sid-1", code: "123456", workspace: "acme-two" });
  });

  it("clears the box on a bad code and keeps focus", async () => {
    server.use(
      config(),
      http.post("*/api/auth/signup", () => HttpResponse.json(ack(), { status: 202 })),
      http.post("*/api/auth/signup/verify", () => err(422, "bad_code")),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    const box = await screen.findByLabelText("6 digit code");
    await user.type(box, "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/That code didn.t work/)).toBeInTheDocument();
    expect(box).toHaveValue("");
    expect(box).toHaveFocus();
  });

  it("offers a new code after code_expired and the resend starts a new countdown", async () => {
    server.use(
      config(),
      http.post("*/api/auth/signup", () =>
        HttpResponse.json(ack({ resend_after: new Date(Date.now() - 1000).toISOString() }), {
          status: 202,
        }),
      ),
      http.post("*/api/auth/signup/verify", () => err(410, "code_expired")),
      http.post("*/api/auth/signup/resend", () => HttpResponse.json(ack(), { status: 202 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    await user.type(await screen.findByLabelText("6 digit code"), "111111");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/expired or was tried too many times/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send a new code" }));
    expect(await screen.findByRole("button", { name: /Send a new code in \d+s/ })).toBeDisabled();
  });

  it("goes back to the form on signup_expired, keeping what was typed except the password", async () => {
    server.use(
      config(),
      http.post("*/api/auth/signup", () => HttpResponse.json(ack(), { status: 202 })),
      http.post("*/api/auth/signup/verify", () => err(410, "signup_expired")),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    await user.type(await screen.findByLabelText("6 digit code"), "111111");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByLabelText("Workspace name")).toHaveValue("Acme Support");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("counts down the resend wait", async () => {
    server.use(
      config(),
      http.post("*/api/auth/signup", () => HttpResponse.json(ack(), { status: 202 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    expect(await screen.findByRole("button", { name: /Send a new code in \d+s/ })).toBeDisabled();
  });

  it("disables the button for Retry-After on a 429", async () => {
    server.use(
      config(),
      http.post("*/api/auth/signup", () =>
        err(429, "TooManyAttempts", "Slow down", { "Retry-After": "30" }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SignupForm />);
    await fillAndSubmit(user);
    expect(await screen.findByText(/Wait 30 seconds/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create workspace \(wait \d+s\)/ })).toBeDisabled();
  });

  it.each([
    ["off", config(false)],
    ["an error", configGone],
  ])("shows not available when the config is %s", async (_n, handler) => {
    server.use(handler);
    renderWithProviders(<SignupForm />);
    expect(await screen.findByText(/Sign-up isn.t available/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to sign in/ })).toHaveAttribute("href", "/login");
  });
});

describe("LoginForm sign-up link", () => {
  const sso = http.get("*/api/sso/*", () => HttpResponse.json({ enabled: false }));

  it("shows the link only when sign-up is on", async () => {
    server.use(config(true), sso);
    renderWithProviders(<LoginForm />);
    expect(await screen.findByRole("link", { name: "Create a workspace" })).toHaveAttribute(
      "href",
      "/signup",
    );
  });

  it.each([
    ["off", config(false)],
    ["an error", configGone],
  ])("hides the link when the config is %s", async (_n, handler) => {
    server.use(handler, sso);
    renderWithProviders(<LoginForm />);
    await screen.findByRole("button", { name: "Sign in" });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("link", { name: "Create a workspace" })).toBeNull();
  });

  it("fills the workspace from ?workspace= over the remembered one", async () => {
    localStorage.setItem("cdesk.workspace", "old");
    nav.search = new URLSearchParams("workspace=new-co");
    server.use(config(false), sso);
    renderWithProviders(<LoginForm />);
    expect(await screen.findByLabelText("Workspace")).toHaveValue("new-co");
    localStorage.clear();
  });
});
