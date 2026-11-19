import { BrowserRouter, HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { PortalHomePage } from './pages/PortalHomePage';
import { ForecastMapPage } from './pages/ForecastMapPage';
import { ExplorerPage } from './pages/ExplorerPage';
import { ExportReceiptPage } from './pages/ExportReceiptPage';

const IS_DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true';

export function AppRoutes() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<PortalHomePage />} />
        <Route path="/map" element={<ForecastMapPage />} />
        <Route path="/explorer" element={<ExplorerPage />} />
        <Route path="/export-receipt" element={<ExportReceiptPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

export default function App() {
  const Router = IS_DEMO_MODE ? HashRouter : BrowserRouter;
  return (
    <Router>
      <AppRoutes />
    </Router>
  );
}
