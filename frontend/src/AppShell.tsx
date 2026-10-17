import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { getHealth } from "./api";
import { UserPicker } from "./components/UserPicker";
import { useWorkspace } from "./workspace";

const featureLinks = [
  { to: "/fleet", label: "Fleet" },
  { to: "/assets", label: "Assets" },
  { to: "/compliance", label: "Compliance" },
  { to: "/knowledge", label: "Knowledge" },
];

function applyTheme(theme: "light" | "dark") {
  document.documentElement.classList.toggle("dark", theme === "dark");
  localStorage.setItem("fb-theme", theme);
}

export function AppShell() {
  const location = useLocation();
  const chatMode = location.pathname === "/";
  const workspace = useWorkspace();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const stored = localStorage.getItem("fb-theme");
    return stored === "dark" ? "dark" : "light";
  });
  const [backend, setBackend] = useState<"checking" | "ok" | "down">("checking");

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    getHealth()
      .then((health) => setBackend(health.status === "ok" ? "ok" : "down"))
      .catch(() => setBackend("down"));
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--text-primary)]">
      {open && (
        <button
          type="button"
          className="fixed inset-0 z-20 bg-black/40 lg:hidden"
          aria-label="Close sidebar"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        className={`fixed inset-y-0 z-30 flex w-64 flex-col border-r border-white/10 bg-[var(--nav-bg)] text-[var(--nav-text)] transition-transform duration-200 lg:static ${
          open ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-4">
          <div>
            <div className="text-[11px] uppercase tracking-[0.22em] text-[#65d4cb]">FlytBase</div>
            <div className="text-sm font-semibold">Asset Intelligence</div>
          </div>
          <button
            type="button"
            className="focus-ring rounded-md border border-white/20 px-2 py-1 text-xs text-[var(--nav-text)] lg:hidden"
            onClick={() => setOpen(false)}
          >
            Close
          </button>
        </div>
        <div className="px-3">
          <NavLink
            to="/"
            onClick={() => {
              workspace.newChat();
              setOpen(false);
            }}
            className="focus-ring flex w-full items-center justify-center rounded-lg bg-[var(--brand)] px-3 py-2 text-sm font-medium text-white transition hover:bg-[var(--brand-hover)]"
          >
            New chat
          </NavLink>
        </div>
        <div className="mt-4 flex-1 overflow-y-auto px-3 pb-3">
          <p className="px-2 text-[11px] uppercase tracking-wide text-[var(--nav-muted)]">This session</p>
          <div className="mt-1 space-y-1">
            {workspace.threads.map((thread) => (
              <NavLink
                key={thread.localId}
                to="/"
                onClick={() => {
                  workspace.selectThread(thread.localId);
                  setOpen(false);
                }}
                className={`block truncate rounded-lg px-2 py-2 text-sm ${
                  chatMode && workspace.activeId === thread.localId
                    ? "bg-white/[0.12] text-white"
                    : "text-[var(--nav-text)] hover:bg-white/[0.08]"
                }`}
              >
                {thread.title}
              </NavLink>
            ))}
          </div>
          {!!workspace.stored.length && (
            <>
              <p className="mt-5 px-2 text-[11px] uppercase tracking-wide text-[var(--nav-muted)]">Stored text threads</p>
              <div className="mt-1 space-y-1">
                {workspace.stored.map((item) => (
                  <button
                    key={item.conversation_id}
                    type="button"
                    onClick={() => {
                      workspace.continueStored(item.conversation_id, item.title || item.last_text || item.conversation_id);
                      setOpen(false);
                    }}
                    className="focus-ring block w-full truncate rounded-lg px-2 py-2 text-left text-sm text-[var(--nav-text)] hover:bg-white/[0.08]"
                  >
                    {item.title || item.last_text || item.conversation_id}
                  </button>
                ))}
              </div>
              <p className="mt-2 px-2 text-[11px] leading-relaxed text-[var(--nav-muted)]">{workspace.storedNote}</p>
            </>
          )}
          <p className="mt-5 px-2 text-[11px] uppercase tracking-wide text-[var(--nav-muted)]">Records</p>
          <nav className="mt-1 space-y-1">
            {featureLinks.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  `focus-ring block rounded-lg px-2 py-2 text-sm ${isActive ? "bg-white/[0.12] text-white" : "text-[var(--nav-text)] hover:bg-white/[0.08]"}`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="space-y-3 border-t border-white/10 p-3 text-xs">
          <UserPicker users={workspace.users} value={workspace.userId} onChange={workspace.setUserId} />
          <div className="flex items-center justify-between">
            <span className={backend === "ok" ? "text-[#7bdcb4]" : backend === "down" ? "text-[#ff9b91]" : "text-[var(--nav-muted)]"}>
              {backend === "ok" ? "Backend connected" : backend === "down" ? "Backend unreachable" : "Checking…"}
            </span>
            <button
              type="button"
              className="focus-ring rounded-md border border-white/20 px-2 py-1 text-[var(--nav-text)] hover:bg-white/10"
              onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
            >
              {theme === "dark" ? "Light" : "Dark"}
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-2 lg:hidden">
          <button
            type="button"
            className="focus-ring rounded-md border border-[var(--border)] px-2 py-1 text-sm"
            onClick={() => setOpen(true)}
          >
            Menu
          </button>
          <span className="text-sm font-medium">Copilot</span>
        </header>
        <main className={`min-h-0 min-w-0 flex-1 ${chatMode ? "overflow-hidden" : "overflow-auto p-4 sm:p-5 lg:p-8"}`}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
