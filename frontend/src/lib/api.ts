// HTTP client for the PRAMĀṆA API.
//
// The access token lives in memory only. The refresh token is an httpOnly cookie the
// browser sends to /auth/refresh; this module uses it to renew the access token once
// when a request comes back 401, then repeats the request.

import type { StreamEvent, TokenResponse } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const V1 = `${API_URL}/api/v1`;
const CSRF = { "X-Requested-With": "pramana-web" };

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onSessionLost: (() => void) | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function setSessionLostHandler(handler: () => void) {
  onSessionLost = handler;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields?: { field: string; message: string }[],
  ) {
    super(message);
  }
}

async function toError(response: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = await response.json();
  } catch {
    // not JSON
  }
  const detail =
    typeof body.detail === "string" ? body.detail : typeof body.title === "string" ? body.title : response.statusText;
  return new ApiError(
    response.status,
    typeof body.code === "string" ? body.code : "error",
    detail || "Request failed",
    Array.isArray(body.errors) ? (body.errors as { field: string; message: string }[]) : undefined,
  );
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const response = await fetch(`${V1}/auth/refresh`, { method: "POST", credentials: "include", headers: CSRF });
        if (!response.ok) return false;
        const body = (await response.json()) as TokenResponse;
        accessToken = body.access_token;
        return Boolean(accessToken);
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  csrf?: boolean;
  raw?: boolean;
}

function buildUrl(path: string, query?: RequestOptions["query"]) {
  const url = new URL(`${V1}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (options.csrf) Object.assign(headers, CSRF);
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  return fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    headers,
    body,
    credentials: "include",
  });
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response = await send(path, options);
  if (response.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/mfa/verify")) {
    if (await refreshSession()) {
      response = await send(path, options);
    } else {
      onSessionLost?.();
    }
  }
  if (!response.ok) throw await toError(response);
  if (options.raw) return response as unknown as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** POST that returns server-sent events; calls onEvent for each one. */
export async function stream(
  path: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const open = () =>
    fetch(`${V1}${path}`, {
      method: "POST",
      credentials: "include",
      signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify(body),
    });
  let response = await open();
  if (response.status === 401) {
    if (await refreshSession()) response = await open();
    else onSessionLost?.();
  }
  if (!response.ok || !response.body) throw await toError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) {
        try {
          onEvent(JSON.parse(data) as StreamEvent);
        } catch {
          // keep-alive comment or partial frame
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export async function downloadFile(path: string, query: RequestOptions["query"], filename: string) {
  const response = await api<Response>(path, { query, raw: true });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
