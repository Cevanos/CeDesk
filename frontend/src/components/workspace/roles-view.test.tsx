import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { RolesView } from "@/components/workspace/roles-view";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";

// A role as the API returns it: five booleans, no `actions` list.
const role = (over: Record<string, unknown> = {}) => ({
  id: "r1",
  name: "Billing readers",
  reply: true,
  assign_any: false,
  reports: true,
  settings: false,
  approve: false,
  scope: "all",
  team_id: null,
  team_name: null,
  member_count: 2,
  ...over,
});

describe("RolesView", () => {
  it("counts the rights from the five booleans the API sends", async () => {
    server.use(
      http.get(`${API}/roles`, () => HttpResponse.json([role()])),
      http.get(`${API}/teams`, () => HttpResponse.json([])),
    );
    renderWithProviders(<RolesView />);
    expect(await screen.findByText("Billing readers")).toBeInTheDocument();
    expect(screen.getByText("2 rights")).toBeInTheDocument();
  });

  it("creates a role with only the fields the API accepts (no `actions`)", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.get(`${API}/roles`, () => HttpResponse.json([])),
      http.get(`${API}/teams`, () => HttpResponse.json([])),
      http.post(`${API}/roles`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(role({ id: "r2", name: "Floor lead" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<RolesView />);
    await user.click(await screen.findByRole("button", { name: /add role/i }));
    await user.type(screen.getByLabelText("Name"), "Floor lead");
    await user.click(screen.getByLabelText("Assign tickets to anyone"));
    await user.click(screen.getByLabelText("View reports"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({
      name: "Floor lead",
      reply: true,
      assign_any: true,
      reports: true,
      settings: false,
      approve: false,
      scope: "all",
      team_id: null,
    });
    expect(body).not.toHaveProperty("actions");
  });

  it("edits a role with its rights ticked from the booleans, and sends the whole role", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.get(`${API}/roles`, () => HttpResponse.json([role()])),
      http.get(`${API}/teams`, () => HttpResponse.json([])),
      http.put(`${API}/roles/r1`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(role());
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<RolesView />);
    await user.click(await screen.findByRole("button", { name: /edit/i }));
    expect(screen.getByLabelText("Reply to customers")).toBeChecked();
    expect(screen.getByLabelText("View reports")).toBeChecked();
    expect(screen.getByLabelText("Approve requests")).not.toBeChecked();
    await user.click(screen.getByLabelText("Approve requests"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ name: "Billing readers", reports: true, approve: true });
    expect(body).not.toHaveProperty("actions");
  });
});
