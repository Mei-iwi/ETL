import React from 'react';
import { EtlProvider, useEtl } from './context/EtlContext';
import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { ApiBanner } from './components/common/ApiBanner';

// Screens
import { OverviewScreen } from './components/screens/OverviewScreen';
import { MasterSyncScreen } from './components/screens/MasterSyncScreen';
import { ResourcesScreen } from './components/screens/ResourcesScreen';
import { ResourceDetailScreen } from './components/screens/ResourceDetailScreen';
import { IngestionScreen } from './components/screens/IngestionScreen';
import { OcrJobsScreen } from './components/screens/OcrJobsScreen';
import { OperationsScreen } from './components/screens/OperationsScreen';
import { PostprocessScreen } from './components/screens/PostprocessScreen';
import { ContentUnitsScreen } from './components/screens/ContentUnitsScreen';
import { SystemHealthScreen } from './components/screens/SystemHealthScreen';

const AppContent: React.FC = () => {
  const { activeScreen, notifications, dismissNotification } = useEtl();

  const renderActiveScreen = () => {
    switch (activeScreen) {
      case 'overview':
        return <OverviewScreen />;
      case 'sync':
        return <MasterSyncScreen />;
      case 'resources':
        return <ResourcesScreen />;
      case 'resource-detail':
        return <ResourceDetailScreen />;
      case 'ingest':
        return <IngestionScreen />;
      case 'ocr':
        return <OcrJobsScreen />;
      case 'operations':
        return <OperationsScreen />;
      case 'postprocess':
        return <PostprocessScreen />;
      case 'content-units':
        return <ContentUnitsScreen />;
      case 'health':
        return <SystemHealthScreen />;
      default:
        return <OverviewScreen />;
    }
  };

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <Sidebar />

      {/* Main Operational Area */}
      <div className="main-wrapper">
        <Header />
        <ApiBanner />

        <main className="main-content">
          {renderActiveScreen()}
        </main>
      </div>

      {/* Toast Notifications */}
      {notifications.length > 0 && (
        <div className="notifications-container">
          {notifications.map((n) => (
            <div key={n.id} className={`toast ${n.type}`}>
              <div>
                <div className="toast-title">{n.title}</div>
                <div className="toast-message">{n.message}</div>
                <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '4px' }}>
                  {n.timestamp}
                </div>
              </div>
              <button
                type="button"
                className="toast-close"
                onClick={() => dismissNotification(n.id)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <EtlProvider>
      <AppContent />
    </EtlProvider>
  );
};

export default App;
