import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { Composer } from "@/components/tickets/thread";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";

function mount(replyCc?: string[]) {
  server.use(http.get(`${API}/agents`, () => HttpResponse.json([])));
  renderWithProviders(<Composer ticketId="t1" canReply channel="email" replyCc={replyCc} />);
}

describe("Composer reply CC", () => {
  it("shows who the reply will copy, read-only", () => {
    mount(["rahul@x.com", "sam@x.com"]);
    expect(screen.getByTestId("reply-cc")).toHaveTextContent("Cc: rahul@x.com, sam@x.com");
  });

  it("shows nothing when nobody is copied", () => {
    mount([]);
    expect(screen.queryByTestId("reply-cc")).not.toBeInTheDocument();
  });

  it("hides it on a private note, which is not emailed", async () => {
    mount(["rahul@x.com"]);
    await userEvent.setup().click(screen.getByRole("tab", { name: /private note/i }));
    expect(screen.queryByTestId("reply-cc")).not.toBeInTheDocument();
  });
});
