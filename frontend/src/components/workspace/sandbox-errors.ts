"use client";

import { ApiError } from "@/lib/api/client";

export const msg = (e: unknown) =>
  e instanceof ApiError ? e.userMessage : "Something went wrong.";
/** The 422 names the bad item; show every message it carries. */
export const msg422 = (e: unknown) =>
  e instanceof ApiError && e.status === 422 && e.details.length
    ? e.details.map((d) => d.message).join(" ")
    : msg(e);
