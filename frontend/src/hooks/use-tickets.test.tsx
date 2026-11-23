import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { useTicket, useTickets, useUpdateTicket } from "@/hooks/use-tickets";
import { queryKeys } from "@/lib/api/query-keys";
import { makeSummary, makeTicket } from "@/test/fixtures";
import { makeQueryClient, wrapper } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";

describe("useTickets", () => {
  it("loads a page with counts and the next cursor", async () => {
    server.use(
      http.get(`${API}/tickets`, ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get("status")).toBe("open");
        expect(url.searchParams.get("limit")).toBe("30");
        return HttpResponse.json({
          items: [makeSummary()],
          next_cursor: "c2",
          counts: { open: 4, pending: 1 },
        });
      }),
    );
    const { result } = renderHook(() => useTickets({ status: "open" }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const page = result.current.data?.pages[0];
    expect(page?.items).toHaveLength(1);
    expect(page?.counts).toEqual({ open: 4, pending: 1 });
    expect(result.current.hasNextPage).toBe(true);
  });
});

describe("useTicket", () => {
  it("exposes the ETag next to the ticket", async () => {
    server.use(
      http.get(`${API}/tickets/t1`, () =>
        HttpResponse.json(makeTicket(), { headers: { etag: '"v7"' } }),
      ),
    );
    const { result } = renderHook(() => useTicket("t1"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.etag).toBe('"v7"');
  });
});

describe("useUpdateTicket (optimistic)", () => {
  function setup() {
    const client = makeQueryClient();
    client.setQueryData(queryKeys.tickets.detail("t1"), { ticket: makeTicket(), etag: '"v1"' });
    // Hold the refetch so we observe the optimistic state, not the refetched one.
    server.use(
      http.get(`${API}/tickets/t1`, async () => {
        await new Promise((r) => setTimeout(r, 50));
        return HttpResponse.json(makeTicket({ status: "open" }), { headers: { etag: '"v1"' } });
      }),
    );
    const { result } = renderHook(() => useUpdateTicket("t1"), { wrapper: wrapper(client) });
    const cached = () =>
      client.getQueryData<{ ticket: { status: string } }>(queryKeys.tickets.detail("t1"));
    return { result, cached };
  }

  it("updates the cache immediately and sends If-Match", async () => {
    let ifMatch: string | null = null;
    server.use(
      http.patch(`${API}/tickets/t1`, ({ request }) => {
        ifMatch = request.headers.get("if-match");
        return HttpResponse.json(makeTicket({ status: "pending" }), { headers: { etag: '"v2"' } });
      }),
    );
    const { result, cached } = setup();
    act(() => result.current.mutate({ patch: { status: "pending" }, etag: '"v1"' }));
    await waitFor(() => expect(cached()?.ticket.status).toBe("pending"));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(ifMatch).toBe('"v1"');
  });

  it("rolls back when the server answers 409", async () => {
    server.use(
      http.patch(`${API}/tickets/t1`, () =>
        HttpResponse.json({ error: { code: "Conflict", message: "stale" } }, { status: 409 }),
      ),
    );
    const { result, cached } = setup();
    act(() => result.current.mutate({ patch: { status: "resolved" }, etag: '"v1"' }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(cached()?.ticket.status).toBe("open");
  });
});
