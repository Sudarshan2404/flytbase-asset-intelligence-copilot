import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ChatResponse } from "../schema";
import { VisualStack } from "../visuals/VisualRenderer";
import { SUGGESTED_PROMPTS } from "../prompts";
import { useWorkspace } from "../workspace";

export function CopilotPage() {
  const { active, loading, send, userId } = useWorkspace();
  const [params] = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") || "");
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [active.messages, loading]);

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const text = draft;
      setDraft("");
      void send(text);
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div ref={scroller} className="flex-1 overflow-y-auto">
        {!active.messages.length ? (
          <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center px-4 py-16">
            <h1 className="text-center text-3xl font-semibold tracking-tight text-[var(--text-primary)]">Ask the field</h1>
            <p className="muted mx-auto mt-3 max-w-lg text-center text-sm leading-6">
              Answers come from authorized tools and deterministic calculations. Charts are built
              from those results, not guessed. Identity is a caller-supplied user_id and is not
              authenticated.
            </p>
            <div className="mt-8 grid gap-2">
              {SUGGESTED_PROMPTS.map((example) => (
                <button
                  key={example}
                  type="button"
                  className="surface-card focus-ring px-4 py-3 text-left text-sm transition hover:border-[var(--brand)] hover:bg-[var(--surface-subtle)]"
                  onClick={() => void send(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
            {active.messages.map((message, index) => (
              <article key={index} className="flex gap-3">
                <div
                  className={`mt-1 h-8 w-8 shrink-0 rounded-full text-center text-[11px] leading-8 ${
                    message.role === "user" ? "bg-[var(--nav-raised)] text-white" : "bg-[var(--brand)] text-white"
                  }`}
                >
                  {message.role === "user" ? "You" : "AI"}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="muted text-xs uppercase tracking-wide">
                    {message.role === "user" ? userId : "Copilot"}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-[15px] leading-7">{message.text}</p>
                  {message.response?.identity_authenticated === false && message.role === "assistant" && (
                    <p className="muted mt-2 text-[11px]">identity_authenticated: false</p>
                  )}
                  {message.response && <VisualStack visuals={message.response.visuals} />}
                  {message.response && <Evidence response={message.response} />}
                  {message.error && (
                    <div className="notice-error mt-3">
                      {message.error}
                      <button
                        className="focus-ring ml-3 underline"
                        type="button"
                        onClick={() => void send(active.messages.find((item) => item.role === "user")?.text || "")}
                      >
                        Retry
                      </button>
                    </div>
                  )}
                </div>
              </article>
            ))}
            {loading && <p className="muted text-sm">Running authorized tools…</p>}
          </div>
        )}
      </div>

      <form
        className="border-t border-[var(--border)] bg-[var(--app-bg)] px-3 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          const text = draft;
          setDraft("");
          void send(text);
        }}
      >
        <div className="surface-card mx-auto flex max-w-3xl items-end gap-2 rounded-2xl px-3 py-2">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Ask about a well, area, or finding. Enter sends, Shift+Enter adds a line."
            className="focus-ring max-h-40 min-h-[44px] flex-1 resize-none bg-transparent py-2 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-secondary)]"
          />
          <button
            disabled={loading || !draft.trim()}
            className="primary-button focus-ring mb-1 rounded-xl"
            type="submit"
          >
            {loading ? "…" : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Evidence({ response }: { response: ChatResponse }) {
  return (
    <div className="mt-3 space-y-2 text-sm">
      {!!response.pending_confirmation.length && (
        <div className="notice-warning p-3">
          <div className="font-semibold">Pending confirmation</div>
          {response.pending_confirmation.map((item, index) => (
            <p key={index}>{item.message || item.text}</p>
          ))}
        </div>
      )}
      {!!response.conflicts.length && (
        <div className="notice-error p-3">
          <div className="font-semibold">Conflicts</div>
          {response.conflicts.map((item, index) => (
            <p key={index}>
              {item.kind}: {item.summary}
            </p>
          ))}
        </div>
      )}
      <details className="app-surface-subtle rounded-xl border p-3">
        <summary className="focus-ring cursor-pointer rounded-sm font-medium">Evidence and tool trace</summary>
        <ul className="mt-2 space-y-1">
          {response.citations.map((citation, index) => (
            <li key={index}>
              {citation.kind}
              {citation.record_id ? ` · ${citation.record_id}` : ""}
              {citation.path ? ` · ${citation.path}` : ""}
              {citation.excerpt ? ` — ${citation.excerpt}` : ""}
            </li>
          ))}
          {!response.citations.length && <li>No citations were returned for this answer.</li>}
        </ul>
        <ul className="muted mt-2 space-y-1 text-xs">
          {response.tool_trace.map((item, index) => (
            <li key={index}>
              {item.ok ? "ok" : "failed"} · {item.name}
              {item.error ? ` · ${item.error}` : ""}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
