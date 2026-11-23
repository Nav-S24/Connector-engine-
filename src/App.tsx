import { useEffect, useState } from 'react';
import { subscribeMockMode } from './api';
import { Layout, type PageId } from './components/Layout';
import { AuditLogPage } from './pages/AuditLogPage';
import { BenchmarksPage } from './pages/BenchmarksPage';
import { DriftRepairsPage } from './pages/DriftRepairsPage';
import { OverviewPage } from './pages/OverviewPage';
import { RunsPage } from './pages/RunsPage';

export default function App() {
  const [activePage, setActivePage] = useState<PageId>('overview');
  const [isMock, setIsMock] = useState<boolean>(false);
  const [refreshSignal, setRefreshSignal] = useState<number>(0);
  const [refreshing, setRefreshing] = useState<boolean>(false);

  useEffect(() => {
    return subscribeMockMode(setIsMock);
  }, []);

  const handleRefresh = () => {
    setRefreshSignal((prev) => prev + 1);
  };

  return (
    <Layout
      activePage={activePage}
      onNavigate={setActivePage}
      isMock={isMock}
      onRefresh={handleRefresh}
      refreshing={refreshing}
    >
      {activePage === 'overview' && (
        <OverviewPage
          refreshSignal={refreshSignal}
          onRefreshStateChange={setRefreshing}
        />
      )}
      {activePage === 'runs' && (
        <RunsPage
          refreshSignal={refreshSignal}
          onRefreshStateChange={setRefreshing}
        />
      )}
      {activePage === 'drift-repairs' && (
        <DriftRepairsPage
          refreshSignal={refreshSignal}
          onRefreshStateChange={setRefreshing}
        />
      )}
      {activePage === 'audit' && (
        <AuditLogPage
          refreshSignal={refreshSignal}
          onRefreshStateChange={setRefreshing}
        />
      )}
      {activePage === 'benchmarks' && (
        <BenchmarksPage
          refreshSignal={refreshSignal}
          onRefreshStateChange={setRefreshing}
        />
      )}
    </Layout>
  );
}
