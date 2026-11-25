import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { Composer } from "@/components/tickets/composer";
import { collided, latestMessage } from "@/components/tickets/reply-collision";
import type { Message } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";
const mine = { id: "m1", kind: "agent" as const, author_member_id: "me", author_name: "Me" };
const theirs = { id: "m2", kind: "agent" as const, author_member_id: "sam", author_name: "Sam" };

describe("collided", () => {
  it("ignores my own and unchanged messages", () => {
    expect(collided(null, theirs, "me")).toBe(false);
    expect(collided("m2", theirs, "me")).toBe(false);
    expect(collided("m1", mine, "me")).toBe(false);
    expect(collided("m1", theirs, "me")).toBe(true);
    expect(collided("", { ...theirs, author_member_id: null, kind: "customer" }, "me")).toBe(true);
  });

  it("takes the newest non-system message", () => {
    const m = (id: string, at: string, kind: Message["kind"]): Message => ({
      id,
      kind,
      body: "",
      author_member_id: null,
      created_at: at,
    });
    expect(
      latestMessage([m("a", "1", "customer"), m("b", "3", "system"), m("c", "2", "agent")])?.id,
    ).toBe("c");
    expect(latestMessage([])).toBeNull();
  });
});

describe("Composer reply collision", () => {
  it("holds the send when someone else answered first, then sends on 'Send anyway'", async () => {
    let posted = 0;
    server.use(
      http.get(`${API}/agents`, () => HttpResponse.json([])),
      http.post(`${API}/tickets/t1/messages`, () => {
        posted += 1;
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(
      <Composer ticketId="t1" canReply latest={mine} myId="me" onCheck={async () => theirs} />,
    );
    await user.type(screen.getByRole("combobox", { name: /reply to the customer/i }), "Hello");
    await user.click(screen.getByRole("button", { name: /send reply/i }));
    expect(await screen.findByTestId("reply-collision")).toHaveTextContent("Sam replied");
    expect(posted).toBe(0);

    await user.click(screen.getByRole("button", { name: /send anyway/i }));
    await waitFor(() => expect(posted).toBe(1));
    void rerender;
  });
});
