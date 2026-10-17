import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getWell } from "../api";

export function AssetDetailPage() {
  const { assetId = "" } = useParams();
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getWell(assetId)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, [assetId]);

  if (error) return <p className="notice-error">{error}</p>;
  if (!data) return <p className="muted text-sm">Loading well…</p>;

  const well = data.well || {};
  const production = data.production || {};
  const inspection = data.latest_inspection;

  return (
    <div className="page-container">
      <Link to="/assets" className="text-link focus-ring text-sm">
        Back to explorer
      </Link>
      <div className="grid gap-3 md:grid-cols-3">
        <article className="surface-card p-4 sm:p-5 md:col-span-2">
          <h1 className="page-title">{well.asset_id}</h1>
          <p className="muted mt-1 text-sm">{well.name}</p>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div>Type: {well.well_type || "—"}</div>
            <div>Area: {well.area_id || "—"}</div>
            <div>Team: {well.team_id || "—"}</div>
            <div>Installed: {well.installed_on || "—"}</div>
            <div>Recorded status: {well.recorded_status || "—"}</div>
            <div>
              Location:{" "}
              {well.lat != null && well.lon != null ? `${well.lat}, ${well.lon}` : "Not stored"}
            </div>
          </dl>
        </article>
        <article className="surface-card p-4 sm:p-5">
          <h2 className="section-title">Calculated production</h2>
          <p className="mt-2 text-2xl font-semibold">
            {production.producing === true ? "Producing" : production.producing === false ? "Not producing" : "Unknown"}
          </p>
          <p className="muted mt-1 text-xs">{production.message || production.status}</p>
          <p className="mt-2 text-sm">Delta: {production.delta_c ?? "n/a"} °C</p>
          <p className="text-sm">Inspection: {production.inspection_id || "none"}</p>
        </article>
      </div>
      {inspection && (
        <article className="surface-card p-4 text-sm sm:p-5">
          <h2 className="section-title">Latest inspection</h2>
          <p>{inspection.inspection_id} · {inspection.inspected_at}</p>
          <p className="muted mt-1">{inspection.notes || "No notes."}</p>
        </article>
      )}
      <div className="flex gap-3 text-sm">
        <Link className="text-link focus-ring" to={`/compliance?asset=${assetId}`}>
          Compliance findings
        </Link>
        <Link className="text-link focus-ring" to={`/?q=${encodeURIComponent(`Is ${assetId} producing oil right now?`)}`}>
          Ask copilot
        </Link>
      </div>
    </div>
  );
}
