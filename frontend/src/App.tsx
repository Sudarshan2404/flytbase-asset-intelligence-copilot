import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
import { AssetDetailPage } from "./pages/AssetDetail";
import { AssetsPage } from "./pages/Assets";
import { CompliancePage } from "./pages/Compliance";
import { CopilotPage } from "./pages/Copilot";
import { KnowledgePage } from "./pages/Knowledge";
import { OverviewPage } from "./pages/Overview";
import { WorkspaceProvider } from "./workspace";

export function App() {
  return (
    <WorkspaceProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<CopilotPage />} />
          <Route path="/copilot" element={<Navigate to="/" replace />} />
          <Route path="/fleet" element={<OverviewPage />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/assets/:assetId" element={<AssetDetailPage />} />
          <Route path="/compliance" element={<CompliancePage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </WorkspaceProvider>
  );
}
