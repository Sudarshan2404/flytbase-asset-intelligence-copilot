import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { getConversations, getUsers, postChat } from "./api";
import type { ChatResponse } from "./schema";

export type SessionMessage = {
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
  error?: string;
};

export type Thread = {
  localId: string;
  conversationId: string | null;
  title: string;
  messages: SessionMessage[];
};

export type StoredConversation = {
  conversation_id: string;
  started_at: string | null;
  title: string | null;
  last_text: string | null;
};

type WorkspaceValue = {
  users: Array<{ user_id: string; name: string; role: string }>;
  userId: string;
  setUserId: (id: string) => void;
  threads: Thread[];
  activeId: string;
  active: Thread;
  stored: StoredConversation[];
  storedNote: string;
  loading: boolean;
  newChat: () => void;
  selectThread: (localId: string) => void;
  continueStored: (conversationId: string, title: string) => void;
  send: (message: string) => Promise<void>;
};

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

function newLocalId() {
  return `local-${crypto.randomUUID?.() || String(Date.now())}`;
}

function emptyThread(): Thread {
  return { localId: newLocalId(), conversationId: null, title: "New chat", messages: [] };
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [users, setUsers] = useState<Array<{ user_id: string; name: string; role: string }>>([]);
  const [userId, setUserIdState] = useState("U-201");
  const bootstrap = useRef<Thread | null>(null);
  if (!bootstrap.current) bootstrap.current = emptyThread();
  const [threads, setThreads] = useState<Thread[]>([bootstrap.current]);
  const [activeId, setActiveId] = useState(bootstrap.current.localId);
  const [stored, setStored] = useState<StoredConversation[]>([]);
  const [storedNote, setStoredNote] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getUsers()
      .then((body) => setUsers(body.users))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    getConversations(userId)
      .then((body) => {
        setStored(body.conversations);
        setStoredNote(body.note);
      })
      .catch(() => {
        setStored([]);
        setStoredNote("");
      });
  }, [userId, threads]);

  const active = threads.find((thread) => thread.localId === activeId) || threads[0];

  function setUserId(id: string) {
    setUserIdState(id);
    const thread = emptyThread();
    setThreads([thread]);
    setActiveId(thread.localId);
  }

  function newChat() {
    const thread = emptyThread();
    setThreads((current) => [thread, ...current]);
    setActiveId(thread.localId);
  }

  function selectThread(localId: string) {
    setActiveId(localId);
  }

  function continueStored(conversationId: string, title: string) {
    const existing = threads.find((thread) => thread.conversationId === conversationId);
    if (existing) {
      setActiveId(existing.localId);
      return;
    }
    const thread: Thread = {
      localId: newLocalId(),
      conversationId,
      title: title || conversationId,
      messages: [],
    };
    setThreads((current) => [thread, ...current]);
    setActiveId(thread.localId);
  }

  async function send(message: string) {
    const trimmed = message.trim();
    if (!trimmed || loading) return;
    const targetId = active.localId;
    setThreads((current) =>
      current.map((thread) =>
        thread.localId === targetId
          ? {
              ...thread,
              title: thread.messages.length ? thread.title : trimmed.slice(0, 48),
              messages: [...thread.messages, { role: "user", text: trimmed }],
            }
          : thread,
      ),
    );
    setLoading(true);
    try {
      const response = await postChat({
        message: trimmed,
        user_id: userId,
        conversation_id: active.conversationId,
      });
      setThreads((current) =>
        current.map((thread) =>
          thread.localId === targetId
            ? {
                ...thread,
                conversationId: response.conversation_id || thread.conversationId,
                messages: [
                  ...thread.messages,
                  {
                    role: "assistant",
                    text: response.answer,
                    response,
                    error: response.error || undefined,
                  },
                ],
              }
            : thread,
        ),
      );
    } catch (err) {
      setThreads((current) =>
        current.map((thread) =>
          thread.localId === targetId
            ? {
                ...thread,
                messages: [
                  ...thread.messages,
                  {
                    role: "assistant",
                    text: "The backend did not complete that request.",
                    error: err instanceof Error ? err.message : "Unknown error",
                  },
                ],
              }
            : thread,
        ),
      );
    } finally {
      setLoading(false);
    }
  }

  const value = useMemo<WorkspaceValue>(
    () => ({
      users,
      userId,
      setUserId,
      threads,
      activeId: active.localId,
      active,
      stored,
      storedNote,
      loading,
      newChat,
      selectThread,
      continueStored,
      send,
    }),
    [users, userId, threads, active, stored, storedNote, loading],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return value;
}
