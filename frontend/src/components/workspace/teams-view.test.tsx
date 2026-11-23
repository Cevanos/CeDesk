import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { TeamsView } from "@/components/workspace/teams-view";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";

const agent = (id: string, name: string, is_active = true) => ({
  member_id: id,
  name,
  role: "agent",
  status: "online",
  open_tickets: 0,
  channels: [],
  capacity: null,
  skills: [],
  is_active,
});

const team = {
  id: "t1",
  name: "Front desk",
  auto_assign: true,
  max_open_per_agent: 5,
  member_ids: ["m1", "m2"],
};

describe("TeamsView member picker", () => {
  it("offers only active members and drops a deactivated one when saving", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.get(`${API}/teams`, () => HttpResponse.json([team])),
      http.get(`${API}/agents`, () =>
        HttpResponse.json([agent("m1", "Meera"), agent("m2", "Old Joiner", false)]),
      ),
      http.patch(`${API}/teams/t1`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(team);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TeamsView />);
    await user.click(await screen.findByRole("button", { name: /edit/i }));

    expect(await screen.findByLabelText("Meera")).toBeChecked();
    expect(screen.queryByLabelText("Old Joiner")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save team" }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ member_ids: ["m1"] });
  });
});
