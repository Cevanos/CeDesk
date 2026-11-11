/** Browser-side API client. Talks to our own /api/* proxy only; the session cookie is httpOnly. */

export interface FieldError {
  field: string;
  location?: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: FieldError[];
  requestId: string | null;
  retryAfter: number | null;
  /**
   * True when the request did not carry `If-Match`: a 409 then means something
   * specific (e.g. "accept the chat first", "still scanning"), not an edit clash.
   */
  specificConflict = false;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    details?: FieldError[];
    requestId?: string | null;
    retryAfter?: number | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? [];
    this.requestId = init.requestId ?? null;
    this.retryAfter = init.retryAfter ?? null;
  }

  /** Message to show the user. */
  get userMessage(): string {
    switch (this.status) {
      case 401:
        return this.code === "NotSignedIn"
          ? "Your session has ended. Please sign in."
          : this.message;
      case 409:
        return this.specificConflict && this.message
          ? this.message
          : "Someone else changed this. Reload to see the latest.";
      case 429:
        return this.retryAfter
          ? `Too many tries. Wait ${this.retryAfter} seconds.`
          : "Too many tries. Please wait a moment.";
      case 503:
        return this.message || "This service can't be reached right now. Try again.";
      case 500:
        return "Something went wrong." + (this.requestId ? ` (ref ${this.requestId})` : "");
      default:
        return this.message || "Something went wrong.";
    }
  }

  fieldMessage(field: string): string | undefined {
    return this.details.find((d) => d.field === field)?.message;
  }
}

export interface ApiResult<T> {
  data: T;
  etag: string | null;
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Query;
  /** JSON body (objects) or a raw Blob/File for uploads. */
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null) {
  onUnauthorized = fn;
}

async function parseError(res: Response): Promise<ApiError> {
  let code = "Internal";
  let message = "Something went wrong.";
  let details: FieldError[] = [];
  try {
    const json = await res.json();
    if (json?.error) {
      code = json.error.code ?? code;
      message = json.error.message ?? message;
      details = json.error.details ?? [];
    }
  } catch {
    /* non-JSON error body */
  }
  const retry = Number(res.headers.get("retry-after"));
  return new ApiError({
    status: res.status,
    code,
    message,
    details,
    requestId: res.headers.get("x-request-id"),
    retryAfter: Number.isFinite(retry) && retry > 0 ? retry : null,
  });
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<ApiResult<T>> {
  const { method = "GET", query, body, headers = {}, signal } = opts;
  const init: RequestInit = {
    method,
    headers: { Accept: "application/json", ...headers },
    credentials: "same-origin",
    cache: "no-store",
    signal,
  };

  if (body instanceof Blob) {
    init.body = body;
  } else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let res: Response;
  try {
    res = await fetch(`/api/${path.replace(/^\/+/, "")}${qs(query)}`, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError({
      status: 0,
      code: "Network",
      message: "You appear to be offline. Check your connection and try again.",
    });
  }

  if (!res.ok) {
    const err = await parseError(res);
    // 401 anywhere (except the sign-in call itself) ends the session.
    if (res.status === 401 && !path.startsWith("auth/sign-in")) onUnauthorized?.();
    if (res.status === 409) {
      err.specificConflict = !Object.keys(headers).some((h) => h.toLowerCase() === "if-match");
    }
    throw err;
  }

  const etag = res.headers.get("etag");
  if (res.status === 204) return { data: undefined as T, etag };
  const text = await res.text();
  return { data: (text ? JSON.parse(text) : undefined) as T, etag };
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    request<T>(path, { query, signal }).then((r) => r.data),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>(path, { method: "POST", body, headers }).then((r) => r.data),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body }).then((r) => r.data),
  patch: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>(path, { method: "PATCH", body, headers }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }).then((r) => r.data),
};

/** List envelopes aren't consistent, so accept a bare array or a list under any key. */
export function listOf<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object") {
    const o = d as Record<string, unknown>;
    for (const k of [
      "items",
      "tickets",
      "results",
      "data",
      "members",
      "agents",
      "teams",
      "notifications",
      "emails",
      "events",
      "attachments",
    ]) {
      if (Array.isArray(o[k])) return o[k] as T[];
    }
    const first = Object.values(o).find(Array.isArray);
    if (first) return first as T[];
  }
  return [];
}

/**
 * One `Idempotency-Key` per user action. Call `header()` when
 * sending: it makes a key the first time and returns the SAME key on every
 * retry. Call `done()` once the action succeeded (or the input changed) so the
 * next click gets a fresh key.
 */
export function createIdempotency() {
  let key: string | null = null;
  let print: string | undefined;
  return {
    /**
     * Pass a fingerprint of the request (e.g. its JSON): the key is only reused
     * for an identical request, because the same key with different content is a 422.
     */
    header(fingerprint?: string): Record<string, string> {
      if (key === null || print !== fingerprint) {
        key = crypto.randomUUID();
        print = fingerprint;
      }
      return { "Idempotency-Key": key };
    },
    done() {
      key = null;
      print = undefined;
    },
  };
}
