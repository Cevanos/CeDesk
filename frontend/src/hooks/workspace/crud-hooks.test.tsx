import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  useDeleteMacro,
  useMacros,
  useReorderMacros,
  useSaveMacro,
} from "@/hooks/workspace/macros";
import {
  useDeleteSavedReply,
  useSavedReplies,
  useSaveSavedReply,
} from "@/hooks/workspace/saved-replies";
import { useDeleteTeam, useSaveTeam, useTeams } from "@/hooks/workspace/teams";
import { queryKeys } from "@/lib/api/query-keys";
import { makeQueryClient, wrapper } from "@/test/render";
import { server } from "@/test/server";

const API = "http://localhost:3000/api";

/** Run `fn`, then report whether the query under `key` was marked stale by it. */
async function invalidated(
  key: readonly unknown[],
  run: (client: ReturnType<typeof makeQueryClient>) => Promise<void>,
) {
  const client = makeQueryClient();
  client.setQueryData(key, []);
  await run(client);
  return client.getQueryState(key)?.isInvalidated === true;
}

describe("useTeams", () => {
  it("unwraps a { items } envelope into a list", async () => {
    server.use(http.get(`${API}/teams`, () => HttpResponse.json({ items: [{ id: "t1" }] })));
    const { result } = renderHook(() => useTeams(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: "t1" }]);
  });

  it("surfaces a server error instead of an empty list", async () => {
    server.use(
      http.get(`${API}/teams`, () => HttpResponse.json({ error: "boom" }, { status: 500 })),
    );
    const { result } = renderHook(() => useTeams(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});

describe("useSaveTeam / useDeleteTeam", () => {
  it("POSTs without an id and PATCHes with one (id is not sent in the body)", async () => {
    let post: unknown;
    let patch: unknown;
    server.use(
      http.post(`${API}/teams`, async ({ request }) => {
        post = await request.json();
        return HttpResponse.json({ id: "new" });
      }),
      http.patch(`${API}/teams/t1`, async ({ request }) => {
        patch = await request.json();
        return HttpResponse.json({ id: "t1" });
      }),
    );
    const { result } = renderHook(() => useSaveTeam(), { wrapper: wrapper() });
    await act(() => result.current.mutateAsync({ name: "Billing" }));
    await act(() => result.current.mutateAsync({ id: "t1", name: "Tech" }));
    expect(post).toEqual({ name: "Billing" });
    expect(patch).toEqual({ name: "Tech" });
  });

  it("invalidates the teams list after save and delete", async () => {
    server.use(
      http.post(`${API}/teams`, () => HttpResponse.json({ id: "n" })),
      http.delete(`${API}/teams/t1`, () => new HttpResponse(null, { status: 204 })),
    );
    expect(
      await invalidated(queryKeys.teams.all, async (client) => {
        const { result } = renderHook(() => useSaveTeam(), { wrapper: wrapper(client) });
        await act(() => result.current.mutateAsync({ name: "x" }));
      }),
    ).toBe(true);
    expect(
      await invalidated(queryKeys.teams.all, async (client) => {
        const { result } = renderHook(() => useDeleteTeam(), { wrapper: wrapper(client) });
        await act(() => result.current.mutateAsync("t1"));
      }),
    ).toBe(true);
  });

  it("does not invalidate when the save fails", async () => {
    server.use(
      http.post(`${API}/teams`, () => HttpResponse.json({ error: "no" }, { status: 400 })),
    );
    expect(
      await invalidated(queryKeys.teams.all, async (client) => {
        const { result } = renderHook(() => useSaveTeam(), { wrapper: wrapper(client) });
        await act(() => result.current.mutateAsync({ name: "x" }).catch(() => undefined));
      }),
    ).toBe(false);
  });
});

describe("macros hooks", () => {
  it("lists, saves, reorders and deletes against the right endpoints", async () => {
    const calls: string[] = [];
    let order: unknown;
    server.use(
      http.get(`${API}/macros`, () => HttpResponse.json([{ id: "m1" }])),
      http.post(`${API}/macros`, () => {
        calls.push("POST macros");
        return HttpResponse.json({ id: "m2" });
      }),
      http.patch(`${API}/macros/m1`, () => {
        calls.push("PATCH macros/m1");
        return HttpResponse.json({ id: "m1" });
      }),
      http.put(`${API}/macros/order`, async ({ request }) => {
        order = await request.json();
        return HttpResponse.json({});
      }),
      http.delete(`${API}/macros/m1`, () => {
        calls.push("DELETE macros/m1");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const w = wrapper();
    const list = renderHook(() => useMacros(), { wrapper: w });
    await waitFor(() => expect(list.result.current.data).toEqual([{ id: "m1" }]));

    const save = renderHook(() => useSaveMacro(), { wrapper: w });
    await act(() => save.result.current.mutateAsync({ name: "New" }));
    await act(() => save.result.current.mutateAsync({ id: "m1", name: "Edit" }));
    const reorder = renderHook(() => useReorderMacros(), { wrapper: w });
    await act(() => reorder.result.current.mutateAsync(["m2", "m1"]));
    const del = renderHook(() => useDeleteMacro(), { wrapper: w });
    await act(() => del.result.current.mutateAsync("m1"));

    expect(calls).toEqual(["POST macros", "PATCH macros/m1", "DELETE macros/m1"]);
    expect(order).toEqual({ ids: ["m2", "m1"] });
  });

  it("does not fetch while disabled", () => {
    let hit = false;
    server.use(
      http.get(`${API}/macros`, () => {
        hit = true;
        return HttpResponse.json([]);
      }),
    );
    const { result } = renderHook(() => useMacros(false), { wrapper: wrapper() });
    expect(result.current.fetchStatus).toBe("idle");
    expect(hit).toBe(false);
  });
});

describe("saved replies hooks", () => {
  it("sends the search text as ?q=", async () => {
    let q: string | null = null;
    server.use(
      http.get(`${API}/saved-replies`, ({ request }) => {
        q = new URL(request.url).searchParams.get("q");
        return HttpResponse.json([{ id: "s1" }]);
      }),
    );
    const { result } = renderHook(() => useSavedReplies("refund"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(q).toBe("refund");
  });

  it("invalidates every search variant after save and delete", async () => {
    server.use(
      http.post(`${API}/saved-replies`, () => HttpResponse.json({ id: "s" })),
      http.delete(`${API}/saved-replies/s1`, () => new HttpResponse(null, { status: 204 })),
    );
    // A cached search ("search", "hi") sits under the broad "saved-replies" prefix.
    const key = queryKeys.savedReplies.search("hi");
    expect(
      await invalidated(key, async (client) => {
        const { result } = renderHook(() => useSaveSavedReply(), { wrapper: wrapper(client) });
        await act(() => result.current.mutateAsync({ shortcut: "hi", body: "Hello" }));
      }),
    ).toBe(true);
    expect(
      await invalidated(key, async (client) => {
        const { result } = renderHook(() => useDeleteSavedReply(), { wrapper: wrapper(client) });
        await act(() => result.current.mutateAsync("s1"));
      }),
    ).toBe(true);
  });
});
