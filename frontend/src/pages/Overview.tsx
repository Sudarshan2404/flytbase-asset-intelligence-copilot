import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getOverview } from "../api";

export function OverviewPage() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOverview()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <p className="notice-error">{error}</p>;
  if (!data) return <p className="muted text-sm">Loading fleet counts…</p>;

  const counts = (data.counts || {}) as Record<string, number>;
  const cards = [
    ["Wells", counts.wells],
    ["Inspections", counts.inspections],
    ["Maintenance jobs", counts.maintenance],
    ["Regulatory events", counts.regulatory_events],
  ];

  return (
    <div className="page-container">
      <header>
        <h1 className="page-title">Fleet</h1>
        <p className="muted mt-2 max-w-3xl text-sm leading-6">
          Counts come from the ingested SQLite database (as of {String(data.as_of)}). Production and
          compliance values are calculated only when a well is opened or asked about.
        </p>
      </header>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map(([label, value]) => (
          <article key={label} className="surface-card px-4 py-4">
            <div className="muted text-xs font-medium uppercase tracking-wide">{label}</div>
            <div className="mt-2 text-3xl font-semibold tracking-tight text-[var(--text-primary)]">{value ?? "—"}</div>
          </article>
        ))}
      </div>
      <section className="surface-card p-4 sm:p-5">
        <h2 className="section-title">Areas</h2>
        <div className="mt-3 overflow-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Area</th>
                <th>Name</th>
                <th>Wells</th>
              </tr>
            </thead>
            <tbody>
              {(data.areas as Array<Record<string, unknown>> | undefined)?.map((area) => (
                <tr key={String(area.area_id)}>
                  <td>
                    <Link className="text-link focus-ring" to={`/assets?area=${area.area_id}`}>
                      {String(area.area_id)}
                    </Link>
                  </td>
                  <td>{String(area.name ?? "")}</td>
                  <td>{String(area.well_count ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="surface-card p-4 sm:p-5">
        <h2 className="section-title">Recent inspections</h2>
        <ul className="mt-3 space-y-2 text-sm">
          {(data.recent_inspections as Array<Record<string, unknown>> | undefined)?.map((row) => (
            <li key={String(row.inspection_id)} className="flex flex-col justify-between gap-1 border-b border-[var(--border)] py-2 last:border-0 sm:flex-row sm:gap-4">
              <Link className="text-link focus-ring" to={`/assets/${row.asset_id}`}>
                {String(row.asset_id)}
              </Link>
              <time className="muted tabular-nums" dateTime={String(row.inspected_at)}>{String(row.inspected_at)}</time>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
