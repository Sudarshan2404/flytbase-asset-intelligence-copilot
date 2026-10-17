import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getAreas, getWells } from "../api";

export function AssetsPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [area, setArea] = useState(params.get("area") || "");
  const [areas, setAreas] = useState<Array<{ area_id: string; name: string }>>([]);
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAreas().then((body) => setAreas(body.areas)).catch(() => undefined);
  }, []);

  useEffect(() => {
    getWells({ q: params.get("q") || undefined, area: params.get("area") || undefined, limit: 40 })
      .then((body) => {
        setRows(body.wells);
        setTotal(body.total);
      })
      .catch((err: Error) => setError(err.message));
  }, [params]);

  function apply(event: React.FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (q) next.set("q", q);
    if (area) next.set("area", area);
    setParams(next);
  }

  return (
    <div className="page-container">
      <header>
        <h1 className="page-title">Assets</h1>
        <p className="muted mt-2 text-sm">Browse ingested wells without inferring production from recorded status.</p>
      </header>
      <form className="flex flex-wrap gap-2" onSubmit={apply}>
        <input
          value={q}
          onChange={(event) => setQ(event.target.value)}
          placeholder="Search WELL-0001 or name"
          className="app-input min-w-56 flex-1"
        />
        <select value={area} onChange={(event) => setArea(event.target.value)} className="app-input">
          <option value="">All areas</option>
          {areas.map((item) => (
            <option key={item.area_id} value={item.area_id}>
              {item.area_id} — {item.name}
            </option>
          ))}
        </select>
        <button className="primary-button focus-ring" type="submit">
          Filter
        </button>
      </form>
      {error && <p className="notice-error">{error}</p>}
      <p className="muted text-xs">{total} wells match. Production is calculated on the well page, not guessed here.</p>
      <div className="table-shell">
        <table className="data-table">
          <thead>
            <tr>
              <th>Well</th>
              <th>Name</th>
              <th>Type</th>
              <th>Area</th>
              <th>Recorded status</th>
              <th>Coordinates</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((well) => (
              <tr key={String(well.asset_id)}>
                <td>
                  <Link className="text-link focus-ring" to={`/assets/${well.asset_id}`}>
                    {String(well.asset_id)}
                  </Link>
                </td>
                <td>{String(well.name ?? "")}</td>
                <td>{String(well.well_type ?? "")}</td>
                <td>{String(well.area_id ?? "")}</td>
                <td>{String(well.recorded_status ?? "—")}</td>
                <td>{well.lat != null && well.lon != null ? `${well.lat}, ${well.lon}` : "Not stored"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
