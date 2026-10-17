import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getCompliance } from "../api";

export function CompliancePage() {
  const [params, setParams] = useSearchParams();
  const [assetId, setAssetId] = useState(params.get("asset") || "WELL-0001");
  const [status, setStatus] = useState("");
  const [data, setData] = useState<{ findings: Array<Record<string, any>>; start: string; end: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function load(id: string) {
    setLoading(true);
    setError(null);
    getCompliance(id)
      .then(setData)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (params.get("asset")) load(params.get("asset") as string);
  }, [params]);

  const rows = useMemo(() => {
    const findings = data?.findings || [];
    if (!status) return findings;
    return findings.filter((item) => item.status === status);
  }, [data, status]);

  return (
    <div className="page-container">
      <header>
        <h1 className="page-title">Compliance</h1>
        <p className="muted mt-2 max-w-3xl text-sm leading-6">
          Findings are produced by the deterministic compliance engine for one well and a 12-month
          evaluation window. This screen does not invent a fleet-wide unresolved list.
        </p>
      </header>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setParams({ asset: assetId });
          load(assetId);
        }}
      >
        <input
          value={assetId}
          onChange={(event) => setAssetId(event.target.value)}
          className="app-input"
        />
        <button className="primary-button focus-ring" type="submit">
          Evaluate
        </button>
        <select value={status} onChange={(event) => setStatus(event.target.value)} className="app-input">
          <option value="">All statuses</option>
          {["noncompliant", "overdue", "pending", "compliant", "insufficient_evidence", "condition_flagged"].map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </form>
      {loading && <p className="muted text-sm">Evaluating rules…</p>}
      {error && <p className="notice-error">{error}</p>}
      {data && (
        <p className="muted text-xs">
          {data.start} to {data.end} · {rows.length} findings shown
        </p>
      )}
      <div className="table-shell">
        <table className="data-table">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Status</th>
              <th>Inspection</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.rule_id}-${index}`}>
                <td className="font-medium">{row.rule_id}</td>
                <td>{row.status}</td>
                <td>{row.inspection_id || "—"}</td>
                <td className="max-w-xl">{row.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
