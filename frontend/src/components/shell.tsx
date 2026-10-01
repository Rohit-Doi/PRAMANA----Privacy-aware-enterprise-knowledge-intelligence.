"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  Activity,
  BarChart3,
  ClipboardCheck,
  Database,
  FileSearch,
  GitCompareArrows,
  LogOut,
  Menu,
  MessageSquareText,
  Moon,
  ScrollText,
  Settings,
  ShieldCheck,
  Sun,
  Users,
  Waypoints,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, Spinner } from "@/components/ui";
import { setSessionLostHandler } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { ROLE_LABEL, SENSITIVITY, cn, departmentLabel } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  permission?: string;
}

const NAV: { heading: string; items: NavItem[] }[] = [
  {
    heading: "Work",
    items: [
      { href: "/ask", label: "Ask", icon: MessageSquareText, permission: "query:run" },
      { href: "/verify", label: "Verify Studio", icon: ClipboardCheck, permission: "verify:run" },
      { href: "/compare", label: "Compare roles", icon: GitCompareArrows, permission: "users:simulate" },
    ],
  },
  {
    heading: "Knowledge",
    items: [
      { href: "/sources", label: "Knowledge Sources", icon: Database, permission: "documents:read" },
      { href: "/graph", label: "Knowledge Graph", icon: Waypoints, permission: "graph:read" },
    ],
  },
  {
    heading: "Governance",
    items: [
      { href: "/policies", label: "Policies", icon: ShieldCheck, permission: "policy:read" },
      { href: "/users", label: "Users & Roles", icon: Users, permission: "users:manage" },
      { href: "/audit", label: "Audit Logs", icon: ScrollText, permission: "audit:read" },
    ],
  },
  {
    heading: "Insight",
    items: [
      { href: "/monitoring", label: "Agent Monitoring", icon: Activity, permission: "monitoring:read" },
      { href: "/reports", label: "Validation Reports", icon: FileSearch, permission: "reports:read" },
      { href: "/analytics", label: "Analytics", icon: BarChart3, permission: "analytics:read" },
    ],
  },
];

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const me = useAuth((s) => s.me);
  const permissions = new Set(me?.permissions ?? []);
  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-4 overflow-y-auto px-3 py-4 scroll-thin">
      <Link href="/ask" className="flex items-center gap-2 px-2" onClick={onNavigate}>
        <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-sm font-bold text-primary-foreground">
          P
        </span>
        <span>
          <span className="block text-sm font-semibold leading-4">PRAMĀṆA</span>
          <span className="block text-[11px] leading-4 text-muted">Verified knowledge</span>
        </span>
      </Link>
      {NAV.map((group) => {
        const items = group.items.filter((item) => !item.permission || permissions.has(item.permission));
        if (!items.length) return null;
        return (
          <div key={group.heading}>
            <p className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{group.heading}</p>
            <ul className="space-y-0.5">
              {items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors",
                        active ? "bg-primary-soft font-medium text-primary" : "text-foreground hover:bg-surface-muted",
                      )}
                    >
                      <item.icon className="h-4 w-4 shrink-0" aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <div className="mt-auto">
        <Link
          href="/settings"
          onClick={onNavigate}
          className={cn(
            "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-surface-muted",
            pathname.startsWith("/settings") && "bg-primary-soft font-medium text-primary",
          )}
        >
          <Settings className="h-4 w-4" aria-hidden />
          Settings
        </Link>
      </div>
    </nav>
  );
}

function AccessSummary() {
  const me = useAuth((s) => s.me);
  if (!me) return null;
  return (
    <div className="hidden items-center gap-2 text-xs text-muted lg:flex">
      <span>
        {ROLE_LABEL[me.role] ?? me.role} · {departmentLabel(me.department)} · {me.region.toUpperCase()}
      </span>
      <span className="rounded border px-1.5 py-0.5">
        Reads up to {SENSITIVITY[me.max_rank_own_dept]}
        {me.max_rank_other_dept !== me.max_rank_own_dept && ` (own dept), ${SENSITIVITY[me.max_rank_other_dept]} elsewhere`}
      </span>
    </div>
  );
}

function UserMenu() {
  const me = useAuth((s) => s.me);
  const logout = useAuth((s) => s.logout);
  const router = useRouter();
  if (!me) return null;
  const initials = me.full_name
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("");
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary"
          aria-label="Account menu"
        >
          {initials}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} className="z-50 w-64 rounded-md border bg-surface p-1 shadow-lg">
          <div className="px-2 py-2">
            <p className="text-sm font-medium">{me.full_name}</p>
            <p className="truncate text-xs text-muted">{me.email}</p>
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item asChild>
            <Link href="/settings" className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-muted">
              <Settings className="h-4 w-4" /> Settings
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Item
            onSelect={async () => {
              await logout();
              router.replace("/login");
            }}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-muted"
          >
            <LogOut className="h-4 w-4" /> Sign out
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const status = useAuth((s) => s.status);
  const bootstrap = useAuth((s) => s.bootstrap);
  const theme = useTheme((s) => s.theme);
  const toggleTheme = useTheme((s) => s.toggle);
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    void bootstrap();
    setSessionLostHandler(() => {
      useAuth.setState({ status: "signed-out", me: null });
    });
  }, [bootstrap]);

  useEffect(() => {
    if (status === "signed-out") router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [status, router, pathname]);

  if (status !== "signed-in") {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 border-r bg-surface md:block">
        <Sidebar />
      </aside>
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button className="absolute inset-0 bg-black/50" aria-label="Close menu" onClick={() => setMenuOpen(false)} />
          <aside className="relative h-full w-64 border-r bg-surface">
            <Sidebar onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b bg-surface px-3">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMenuOpen((v) => !v)} aria-label="Menu">
              {menuOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </Button>
            <AccessSummary />
          </div>
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}>
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
            <UserMenu />
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}

export function PageBody({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("mx-auto w-full max-w-7xl px-4 py-5 md:px-6", className)}>{children}</div>;
}

export function RequirePermission({ permission, children }: { permission: string; children: React.ReactNode }) {
  const me = useAuth((s) => s.me);
  if (me && !me.permissions.includes(permission)) {
    return (
      <PageBody>
        <div className="rounded-lg border bg-surface p-8 text-center">
          <p className="text-sm font-medium">You do not have access to this page</p>
          <p className="mt-1 text-sm text-muted">It needs the permission “{permission}”, which your role does not include.</p>
        </div>
      </PageBody>
    );
  }
  return <>{children}</>;
}
