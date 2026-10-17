import { parseChatResponse, type ChatResponse } from "./schema";

const configured = import.meta.env.VITE_API_URL as string | undefined;
export const API_BASE = (configured || "").replace(/\/$/, "");

function url(path: string): string {
  return `${API_BASE}${path}`;
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.slice(0, 240) || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getHealth() {
  return readJson<{ status: string; database_exists: boolean; counts?: Record<string, number> }>(
    await fetch(url("/health")),
  );
}

export async function getOverview() {
  return readJson<Record<string, unknown>>(await fetch(url("/api/overview")));
}

export async function getUsers() {
  return readJson<{ users: Array<{ user_id: string; name: string; role: string }>; identity_authenticated: boolean }>(
    await fetch(url("/api/users")),
  );
}

export async function getAreas() {
  return readJson<{ areas: Array<{ area_id: string; name: string }> }>(await fetch(url("/api/areas")));
}

export async function getWells(params: { q?: string; area?: string; limit?: number; offset?: number }) {
  const search = new URLSearchParams();
  if (params.q) search.set("q", params.q);
  if (params.area) search.set("area", params.area);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.offset) search.set("offset", String(params.offset));
  return readJson<{ total: number; wells: Array<Record<string, unknown>> }>(
    await fetch(`${url("/api/wells")}?${search}`),
  );
}

export async function getWell(assetId: string) {
  return readJson<Record<string, unknown>>(await fetch(url(`/api/wells/${encodeURIComponent(assetId)}`)));
}

export async function getCompliance(assetId: string) {
  const search = new URLSearchParams({ asset_id: assetId });
  return readJson<{ findings: Array<Record<string, unknown>>; start: string; end: string }>(
    await fetch(`${url("/api/compliance")}?${search}`),
  );
}

export async function getNotes(assetId?: string) {
  const search = new URLSearchParams();
  if (assetId) search.set("asset_id", assetId);
  return readJson<{ notes: Array<Record<string, unknown>>; scope: string }>(
    await fetch(`${url("/api/knowledge/notes")}?${search}`),
  );
}

export async function getClaims(assetId?: string) {
  const search = new URLSearchParams();
  if (assetId) search.set("asset_id", assetId);
  return readJson<{ claims: Array<Record<string, unknown>>; note: string }>(
    await fetch(`${url("/api/knowledge/claims")}?${search}`),
  );
}

export async function getCorrections(inspectionId?: string) {
  const search = new URLSearchParams();
  if (inspectionId) search.set("inspection_id", inspectionId);
  return readJson<{ corrections: Array<Record<string, unknown>>; scope: string }>(
    await fetch(`${url("/api/knowledge/corrections")}?${search}`),
  );
}

export async function postChat(body: {
  message: string;
  user_id: string;
  conversation_id?: string | null;
}): Promise<ChatResponse> {
  const response = await fetch(url("/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await readJson<unknown>(response);
  return parseChatResponse(json);
}

export async function getConversations(userId: string) {
  const search = new URLSearchParams({ user_id: userId });
  return readJson<{
    conversations: Array<{ conversation_id: string; started_at: string | null; title: string | null; last_text: string | null }>;
    visuals_persisted: boolean;
    note: string;
    identity_authenticated: boolean;
  }>(await fetch(`${url("/api/conversations")}?${search}`));
}
