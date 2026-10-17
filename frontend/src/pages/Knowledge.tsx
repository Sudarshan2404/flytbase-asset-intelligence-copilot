import { useState } from "react";
import { getClaims, getCorrections, getNotes } from "../api";

export function KnowledgePage() {
  const [assetId, setAssetId] = useState("WELL-0001");
  const [notes, setNotes] = useState<Array<Record<string, any>>>([]);
  const [claims, setClaims] = useState<Array<Record<string, any>>>([]);
  const [corrections, setCorrections] = useState<Array<Record<string, any>>>([]);
  const [error, setError] = useState<string | null>(null);

  async function load(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const [noteBody, claimBody, correctionBody] = await Promise.all([
        getNotes(assetId),
        getClaims(assetId),
        getCorrections(),
      ]);
      setNotes(noteBody.notes);
      setClaims(claimBody.claims);
      setCorrections(correctionBody.corrections);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load knowledge");
    }
  }

  return (
    <div className="page-container">
      <header>
        <h1 className="page-title">Knowledge</h1>
        <p className="muted mt-2 max-w-3xl text-sm leading-6">
          Shared notes and claims are visible to every operator and default to unverified. Private
          conversation memory is not shown here and is only used by the copilot for the supplied
          user_id. This UI does not apply corrections.
        </p>
      </header>
      <form className="flex gap-2" onSubmit={load}>
        <input
          value={assetId}
          onChange={(event) => setAssetId(event.target.value)}
          className="app-input min-w-0 flex-1 sm:flex-none"
        />
        <button className="primary-button focus-ring" type="submit">
          Load shared records
        </button>
      </form>
      {error && <p className="notice-error">{error}</p>}
      <section className="surface-card p-4 sm:p-5">
        <h2 className="section-title">Shared notes</h2>
        <ul className="mt-2 space-y-2 text-sm">
          {notes.map((note) => (
            <li key={String(note.note_id)} className="border-b border-[var(--border)] pb-2 last:border-0">
              <div className="font-medium">{note.note_id}</div>
              <div>{note.text}</div>
              <div className="muted text-xs">{note.verification_status || note.source}</div>
            </li>
          ))}
          {!notes.length && <li className="muted">Load a well to see notes.</li>}
        </ul>
      </section>
      <section className="notice-warning p-4 sm:p-5">
        <h2 className="section-title text-inherit">Unconfirmed claims</h2>
        <ul className="mt-2 space-y-2 text-sm">
          {claims.map((claim) => (
            <li key={String(claim.claim_id)}>
              {claim.text} · {claim.status} · {claim.asset_id}
            </li>
          ))}
          {!claims.length && <li>No claims loaded.</li>}
        </ul>
      </section>
      <section className="surface-card p-4 sm:p-5">
        <h2 className="section-title">Auditable structured corrections</h2>
        <ul className="mt-2 space-y-2 text-sm">
          {corrections.map((item) => (
            <li key={String(item.correction_id)}>
              {item.correction_id}: {item.field_name} on {item.inspection_id} ({item.status})
            </li>
          ))}
          {!corrections.length && <li className="muted">No structured corrections in the database.</li>}
        </ul>
      </section>
    </div>
  );
}
