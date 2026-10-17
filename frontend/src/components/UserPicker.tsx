import { useEffect, useId, useRef, useState } from "react";

type User = {
  user_id: string;
  name: string;
  role: string;
};

type UserPickerProps = {
  users: User[];
  value: string;
  onChange: (userId: string) => void;
};

export function UserPicker({ users, value, onChange }: UserPickerProps) {
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [open, setOpen] = useState(false);
  const selectedIndex = Math.max(0, users.findIndex((user) => user.user_id === value));
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const selected = users[selectedIndex];

  useEffect(() => {
    setActiveIndex(selectedIndex);
  }, [selectedIndex]);

  useEffect(() => {
    if (!open) return;
    optionRefs.current[activeIndex]?.focus();
  }, [open, activeIndex]);

  useEffect(() => {
    function closeOutside(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, []);

  function choose(index: number) {
    const user = users[index];
    if (user) onChange(user.user_id);
    setOpen(false);
  }

  function move(delta: number) {
    if (!users.length) return;
    setActiveIndex((current) => (current + delta + users.length) % users.length);
  }

  return (
    <div ref={rootRef} className="relative">
      <span id={`${listboxId}-label`} className="mb-1 block text-[11px] text-[var(--nav-muted)]">
        Acting as (not authenticated)
      </span>
      <button
        type="button"
        className="focus-ring flex w-full items-center justify-between gap-2 rounded-lg border border-white/[0.15] bg-white/5 px-2.5 py-2 text-left text-xs text-[var(--nav-text)] transition hover:bg-white/10"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-labelledby={`${listboxId}-label ${listboxId}-value`}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            move(event.key === "ArrowDown" ? 1 : -1);
          }
        }}
      >
        <span id={`${listboxId}-value`} className="min-w-0 truncate">
          {selected ? `${selected.user_id} · ${selected.name}` : value}
        </span>
        <span aria-hidden="true" className="text-[10px] text-[var(--nav-muted)]">
          {open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-labelledby={`${listboxId}-label`}
          className="absolute bottom-full left-0 z-50 mb-2 max-h-72 w-full overflow-y-auto rounded-xl border border-[var(--border-strong)] bg-[var(--surface-raised)] p-1 text-[var(--text-primary)] shadow-xl"
        >
          {users.map((user, index) => (
            <button
              key={user.user_id}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              type="button"
              role="option"
              aria-selected={user.user_id === value}
              tabIndex={index === activeIndex ? 0 : -1}
              className={`focus-ring block w-full rounded-lg px-2.5 py-2 text-left ${
                user.user_id === value
                  ? "bg-[var(--brand-soft)] text-[var(--text-primary)]"
                  : "hover:bg-[var(--surface-subtle)]"
              }`}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(index)}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  move(event.key === "ArrowDown" ? 1 : -1);
                } else if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  choose(index);
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  setOpen(false);
                } else if (event.key === "Home") {
                  event.preventDefault();
                  setActiveIndex(0);
                } else if (event.key === "End") {
                  event.preventDefault();
                  setActiveIndex(users.length - 1);
                }
              }}
            >
              <span className="block text-xs font-medium">{user.user_id} · {user.name}</span>
              <span className="mt-0.5 block text-[11px] text-[var(--text-secondary)]">{user.role}</span>
            </button>
          ))}
          {!users.length && <p className="px-2.5 py-2 text-xs text-[var(--text-secondary)]">Loading users…</p>}
        </div>
      )}
    </div>
  );
}
