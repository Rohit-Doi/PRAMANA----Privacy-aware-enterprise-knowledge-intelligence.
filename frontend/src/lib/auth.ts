"use client";

import { create } from "zustand";

import { api, refreshSession, setAccessToken } from "./api";
import type { Me, TokenResponse } from "./types";

type Status = "loading" | "signed-in" | "signed-out";

interface AuthState {
  status: Status;
  me: Me | null;
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<{ mfaToken: string | null }>;
  completeMfa: (mfaToken: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
  has: (permission: string) => boolean;
}

export const useAuth = create<AuthState>((set, get) => ({
  status: "loading",
  me: null,

  // On page load the access token is gone (it only lives in memory); the refresh cookie restores it.
  bootstrap: async () => {
    if (get().status === "signed-in") return;
    if (await refreshSession()) {
      try {
        set({ me: await api<Me>("/auth/me"), status: "signed-in" });
        return;
      } catch {
        // fall through to signed-out
      }
    }
    set({ status: "signed-out", me: null });
  },

  login: async (email, password) => {
    const result = await api<TokenResponse>("/auth/login", { method: "POST", body: { email, password } });
    if (result.mfa_required) return { mfaToken: result.mfa_token };
    setAccessToken(result.access_token);
    set({ me: await api<Me>("/auth/me"), status: "signed-in" });
    return { mfaToken: null };
  },

  completeMfa: async (mfaToken, code) => {
    const result = await api<TokenResponse>("/auth/mfa/verify", {
      method: "POST",
      body: { mfa_token: mfaToken, code },
    });
    setAccessToken(result.access_token);
    set({ me: await api<Me>("/auth/me"), status: "signed-in" });
  },

  logout: async () => {
    try {
      await api<void>("/auth/logout", { method: "POST", csrf: true });
    } catch {
      // the session may already be gone
    }
    setAccessToken(null);
    set({ status: "signed-out", me: null });
  },

  reload: async () => {
    set({ me: await api<Me>("/auth/me") });
  },

  has: (permission) => Boolean(get().me?.permissions.includes(permission)),
}));
