"use client";

import { create } from "zustand";

type Theme = "light" | "dark";
const KEY = "pramana-theme";

interface ThemeState {
  theme: Theme;
  hydrate: () => void;
  toggle: () => void;
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: "light",
  hydrate: () => {
    const stored = window.localStorage.getItem(KEY) as Theme | null;
    const preferred: Theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    set({ theme: stored ?? preferred });
  },
  toggle: () => {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    window.localStorage.setItem(KEY, next);
    set({ theme: next });
  },
}));
