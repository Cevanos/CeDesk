import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { useAgents, useUpdateAgentRouting } from "@/hooks/workspace/agents";
import {
  useCevanosConnection,
  useCheckCevanos,
  useDisconnectCevanos,
  useSaveCevanos,
} from "@/hooks/workspace/cevanos";
import {
  useAddMember,
  useMemberOptions,
  useMembers,
  useResetMemberPassword,
  useUpdateMember,
} from "@/hooks/workspace/members";
import { useMarkAllRead, useMarkRead, useNotifications } from "@/hooks/workspace/notifications";
import { useDeleteRule, useReorderRules, useRules, useSaveRule } from "@/hooks/workspace/rules";
import {
  useDeleteTicketType,
  useReorderTicketTypes,
  useSaveTicketType,
  useTicketTypes,
} from "@/hooks/workspace/ticket-types";
import { queryKeys } from "@/lib/api/query-keys";
import { makeQueryClient, wrapper } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";
const noContent = () => new HttpResponse(null, { status: 204 });

async function wasInvalidated(
  key: readonly unknown[],
  run: (client: ReturnType<typeof makeQueryClient>) => Promise<unknown>,
) {
  const client = makeQueryClient();
  client.setQueryData(key, []);
  await run(client);
  return client.getQueryState(key)?.isInvalidated === true;
}

describe("rules hooks", () => {
  it("lists, saves (create vs update), reorders and deletes", async () => {
    const seen: string[] = [];
    let order: unknown;
    let patchBody: unknown;
    server.use(
      http.get(`${API}/rules`, () => HttpResponse.json({ items: [{ id: "r1" }] })),
      http.post(`${API}/rules`, () => {
        seen.push("POST rules");
        return HttpResponse.json({ id: "r2" });
      }),
      http.patch(`${API}/rules/r1`, async ({ request }) => {
        seen.push("PATCH rules/r1");
        patchBody = await request.json();
        return HttpResponse.json({ id: "r1" });
      }),
      http.put(`${API}/rules/order`, async ({ request }) => {
        order = await request.json();
        return HttpResponse.json({});
      }),
      http.delete(`${API}/rules/r1`, () => {
        seen.push("DELETE rules/r1");
        return noContent();
      }),
    );
    const w = wrapper();
    const list = renderHook(() => useRules(), { wrapper: w });
    await waitFor(() => expect(list.result.current.data).toEqual([{ id: "r1" }]));

    const save = renderHook(() => useSaveRule(), { wrapper: w });
    await act(() => save.result.current.mutateAsync({ name: "New" }));
    await act(() => save.result.current.mutateAsync({ id: "r1", enabled: false }));
    const reorder = renderHook(() => useReorderRules(), { wrapper: w });
    await act(() => reorder.result.current.mutateAsync(["r2", "r1"]));
    const del = renderHook(() => useDeleteRule(), { wrapper: w });
    await act(() => del.result.current.mutateAsync("r1"));

    expect(seen).toEqual(["POST rules", "PATCH rules/r1", "DELETE rules/r1"]);
    expect(patchBody).toEqual({ enabled: false });
    expect(order).toEqual({ ids: ["r2", "r1"] });
  });

  it("invalidates rules after a delete", async () => {
    server.use(http.delete(`${API}/rules/r1`, noContent));
    expect(
      await wasInvalidated(queryKeys.rules.all, async (client) => {
        const { result } = renderHook(() => useDeleteRule(), { wrapper: wrapper(client) });
        await act(() => result.current.mutateAsync("r1"));
      }),
    ).toBe(true);
  });
});

describe("ticket type hooks", () => {
  it("lists, saves, reorders and deletes against the right endpoints", async () => {
    const seen: string[] = [];
    server.use(
      http.get(`${API}/ticket-types`, () => HttpResponse.json([{ id: "tt1" }])),
      http.post(`${API}/ticket-types`, () => {
        seen.push("POST");
        return HttpResponse.json({ id: "tt2" });
      }),
      http.patch(`${API}/ticket-types/tt1`, () => {
        seen.push("PATCH");
        return HttpResponse.json({ id: "tt1" });
      }),
      http.put(`${API}/ticket-types/order`, () => {
        seen.push("PUT order");
        return noContent();
      }),
      http.delete(`${API}/ticket-types/tt1`, () => {
        seen.push("DELETE");
        return noContent();
      }),
    );
    const w = wrapper();
    const list = renderHook(() => useTicketTypes(), { wrapper: w });
    await waitFor(() => expect(list.result.current.data).toEqual([{ id: "tt1" }]));
    const save = renderHook(() => useSaveTicketType(), { wrapper: w });
    await act(() => save.result.current.mutateAsync({ name: "Bug" }));
    await act(() => save.result.current.mutateAsync({ id: "tt1", name: "Bug 2" }));
    const reorder = renderHook(() => useReorderTicketTypes(), { wrapper: w });
    await act(() => reorder.result.current.mutateAsync(["tt2", "tt1"]));
    const del = renderHook(() => useDeleteTicketType(), { wrapper: w });
    await act(() => del.result.current.mutateAsync("tt1"));
    expect(seen).toEqual(["POST", "PATCH", "PUT order", "DELETE"]);
  });
});

describe("member hooks", () => {
  it("pages through members using next_after", async () => {
    const afters: (string | null)[] = [];
    server.use(
      http.get(`${API}/members`, ({ request }) => {
        const url = new URL(request.url);
        const after = url.searchParams.get("after");
        afters.push(after);
        expect(url.searchParams.get("limit")).toBe("50");
        return HttpResponse.json(
          after
            ? { items: [{ id: "m2" }], next_after: null }
            : { items: [{ id: "m1" }], next_after: "m1" },
        );
      }),
    );
    const { result } = renderHook(() => useMembers(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(afters).toEqual([null, "m1"]);
    expect(result.current.hasNextPage).toBe(false);
  });

  it("requests up to 200 options and stays idle when disabled", async () => {
    let limit: string | null = null;
    server.use(
      http.get(`${API}/members`, ({ request }) => {
        limit = new URL(request.url).searchParams.get("limit");
        return HttpResponse.json([{ id: "m1" }]);
      }),
    );
    const off = renderHook(() => useMemberOptions(false), { wrapper: wrapper() });
    expect(off.result.current.fetchStatus).toBe("idle");
    const on = renderHook(() => useMemberOptions(), { wrapper: wrapper() });
    await waitFor(() => expect(on.result.current.data).toEqual([{ id: "m1" }]));
    expect(limit).toBe("200");
  });

  it("sends add, update and password-reset calls and invalidates members", async () => {
    const bodies: Record<string, unknown> = {};
    server.use(
      http.post(`${API}/members`, async ({ request }) => {
        bodies.add = await request.json();
        return HttpResponse.json({ id: "m9" });
      }),
      http.patch(`${API}/members/m1`, async ({ request }) => {
        bodies.update = await request.json();
        return HttpResponse.json({});
      }),
      http.post(`${API}/members/m1/password`, async ({ request }) => {
        bodies.password = await request.json();
        return noContent();
      }),
    );
    const key = queryKeys.members.list;
    expect(
      await wasInvalidated(key, async (client) => {
        const w = wrapper(client);
        const add = renderHook(() => useAddMember(), { wrapper: w });
        await act(() =>
          add.result.current.mutateAsync({
            email: "a@b.co",
            name: "A",
            role: "agent",
            password: "pw-long-enough",
          }),
        );
        const upd = renderHook(() => useUpdateMember(), { wrapper: w });
        await act(() => upd.result.current.mutateAsync({ id: "m1", is_active: false }));
        const pw = renderHook(() => useResetMemberPassword(), { wrapper: w });
        await act(() => pw.result.current.mutateAsync({ id: "m1", new_password: "new-pw-12345" }));
      }),
    ).toBe(true);
    expect(bodies.update).toEqual({ is_active: false });
    expect(bodies.password).toEqual({ new_password: "new-pw-12345" });
    expect(bodies.add).toMatchObject({ email: "a@b.co", role: "agent" });
  });
});

describe("agent hooks", () => {
  it("normalises missing fields and accepts `id` as `member_id`", async () => {
    server.use(http.get(`${API}/agents`, () => HttpResponse.json({ items: [{ id: "a1" }] })));
    const { result } = renderHook(() => useAgents(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0]).toMatchObject({
      member_id: "a1",
      channels: [],
      skills: [],
      open_tickets: 0,
    });
  });

  it("PATCHes routing without the id in the body", async () => {
    let body: unknown;
    server.use(
      http.patch(`${API}/agents/a1/routing`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({});
      }),
    );
    expect(
      await wasInvalidated(queryKeys.agents.all, async (client) => {
        const { result } = renderHook(() => useUpdateAgentRouting(), {
          wrapper: wrapper(client),
        });
        await act(() => result.current.mutateAsync({ id: "a1", status: "away" as never }));
      }),
    ).toBe(true);
    expect(body).toEqual({ status: "away" });
  });
});

describe("cevanos hooks", () => {
  it("writes the saved connection straight into the cache", async () => {
    const saved = { connected: true };
    server.use(http.put(`${API}/cevanos/connection`, () => HttpResponse.json(saved)));
    const client = makeQueryClient();
    const { result } = renderHook(() => useSaveCevanos(), { wrapper: wrapper(client) });
    await act(() => result.current.mutateAsync({ close_on_resolve: true }));
    expect(client.getQueryData(queryKeys.cevanos.all)).toEqual(saved);
  });

  it("loads, checks and disconnects", async () => {
    server.use(
      http.get(`${API}/cevanos/connection`, () => HttpResponse.json({ connected: false })),
      http.post(`${API}/cevanos/connection/check`, () =>
        HttpResponse.json({ ok: false, missing_scopes: ["tickets"], message: "nope" }),
      ),
      http.delete(`${API}/cevanos/connection`, noContent),
    );
    const w = wrapper();
    const conn = renderHook(() => useCevanosConnection(), { wrapper: w });
    await waitFor(() => expect(conn.result.current.data).toEqual({ connected: false }));
    const check = renderHook(() => useCheckCevanos(), { wrapper: w });
    const res = await act(() => check.result.current.mutateAsync());
    expect(res).toMatchObject({ ok: false, missing_scopes: ["tickets"] });
    const disc = renderHook(() => useDisconnectCevanos(), { wrapper: w });
    await act(() => disc.result.current.mutateAsync());
  });
});

describe("notification hooks", () => {
  it("returns items with a numeric unread count (defaults to 0)", async () => {
    server.use(
      http.get(`${API}/notifications`, () => HttpResponse.json({ items: [{ id: "n1" }] })),
    );
    const { result } = renderHook(() => useNotifications(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ items: [{ id: "n1" }], unread: 0 });
  });

  it("marks one and all as read, then refreshes notifications", async () => {
    const seen: string[] = [];
    server.use(
      http.post(`${API}/notifications/n1/read`, () => {
        seen.push("one");
        return noContent();
      }),
      http.post(`${API}/notifications/read-all`, () => {
        seen.push("all");
        return noContent();
      }),
    );
    const key = queryKeys.notifications.list();
    expect(
      await wasInvalidated(key, async (client) => {
        const w = wrapper(client);
        const one = renderHook(() => useMarkRead(), { wrapper: w });
        await act(() => one.result.current.mutateAsync("n1"));
        const all = renderHook(() => useMarkAllRead(), { wrapper: w });
        await act(() => all.result.current.mutateAsync());
      }),
    ).toBe(true);
    expect(seen).toEqual(["one", "all"]);
  });
});
