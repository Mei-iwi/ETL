import { lazy, Suspense } from 'react'
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from 'react-router'
import './App.css'
import { AuthGuard, AuthProvider } from './auth/AuthProvider'
import { LoginPage } from './auth/LoginPage'

import { AppLayout } from './components/layout/AppLayout'

const AccountManagementPage = lazy(() => import('./auth/AccountPages').then(module => ({ default: module.AccountManagementPage })))
const ChangePasswordPage = lazy(() => import('./auth/AccountPages').then(module => ({ default: module.ChangePasswordPage })))
const PersonalProfilePage = lazy(() => import('./auth/PersonalProfilePage').then(module => ({ default: module.PersonalProfilePage })))
const OverviewSummary = lazy(() => import('./pages/overview/OverviewSummary').then(module => ({ default: module.OverviewSummary })))
const OverviewProcess = lazy(() => import('./pages/overview/OverviewProcess').then(module => ({ default: module.OverviewProcess })))
const LibraryListPage = lazy(() => import('./pages/library/LibraryListPage').then(module => ({ default: module.LibraryListPage })))
const LibrarySyncErrorsPage = lazy(() => import('./pages/library/LibrarySyncErrorsPage').then(module => ({ default: module.LibrarySyncErrorsPage })))
const LibrarySourceFilesPage = lazy(() => import('./pages/library/LibrarySourceFilesPage').then(module => ({ default: module.LibrarySourceFilesPage })))
const LibraryCreatePage = lazy(() => import('./pages/library/LibraryCreatePage').then(module => ({ default: module.LibraryCreatePage })))
const LibraryProcessingPage = lazy(() => import('./pages/library/LibraryProcessingPage').then(module => ({ default: module.LibraryProcessingPage })))
const LibraryIndexingPage = lazy(() => import('./pages/library/LibraryIndexingPage').then(module => ({ default: module.LibraryIndexingPage })))
const LibraryResourceDetailPage = lazy(() => import('./pages/library/LibraryResourceDetailPage').then(module => ({ default: module.LibraryResourceDetailPage })))
const LibraryVersionHistoryPage = lazy(() => import('./pages/library/LibraryVersionHistoryPage').then(module => ({ default: module.LibraryVersionHistoryPage })))
const QueryMethodPage = lazy(() => import('./pages/search/QueryMethodPage').then(module => ({ default: module.QueryMethodPage })))
const EvaluationDataPage = lazy(() => import('./pages/experiments/EvaluationDataPage').then(module => ({ default: module.EvaluationDataPage })))
const EvaluationResultsPage = lazy(() => import('./pages/experiments/EvalutionResultsPage').then(module => ({ default: module.EvaluationResultsPage })))
const ExperimentsReportPage = lazy(() => import('./pages/experiments/ExperimentsReportsPage').then(module => ({ default: module.ExperimentsReportPage })))
const RunExperimentPage = lazy(() => import('./pages/experiments/RunExperimentPage').then(module => ({ default: module.RunExperimentPage })))
const SystemStatusPage = lazy(() => import('./pages/system/SystemStatusPage').then(module => ({ default: module.SystemStatusPage })))
const MasterDataPage = lazy(() => import('./pages/system/MasterDataPage').then(module => ({ default: module.MasterDataPage })))
const SystemCapalitiesPage = lazy(() => import('./pages/system/SystemCapalitiesPage').then(module => ({ default: module.SystemCapalitiesPage })))
const SystemMasterSyncLayout = lazy(() => import('./pages/system/SystemMasterSyncPage').then(module => ({ default: module.SystemMasterSyncLayout })))
const SystemMasterSyncPage = lazy(() => import('./pages/system/SystemMasterSyncPage').then(module => ({ default: module.SystemMasterSyncPage })))
const DataReconciliationPage = lazy(() => import('./pages/system/DataReconciliationPage').then(module => ({ default: module.DataReconciliationPage })))
const LibraryUpdateVersion = lazy(() => import('./pages/library/LibraryUpdateVersion').then(module => ({ default: module.LibraryUpdateVersion })))

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AuthGuard />}>
        <Route path="/" element={<AppLayout />}>
          <Route path="account/password" element={<ChangePasswordPage />} />
          <Route path="account/profile" element={<PersonalProfilePage />} />
          <Route
            index
            element={<Navigate to="overview" replace />}
          />
          <Route path="overview">
            <Route index element={<Navigate to='summary' replace />} />
            <Route path="summary" element={<OverviewSummary />} />
            <Route path='process' element={<OverviewProcess />} />
          </Route>
          <Route path='library'>
            <Route index element={<Navigate to='list' replace />} />
            <Route path='list' element={<LibraryListPage />} />
            <Route path='add' element={<LibraryCreatePage />} />
            <Route path='processing' element={<LibraryProcessingPage />} />
            <Route path='indexing' element={<LibraryIndexingPage />} />
            <Route path='source-sync' element={<SystemMasterSyncLayout />}>
              <Route index element={<SystemMasterSyncPage />} />
              <Route path='files' element={<LibrarySourceFilesPage />} />
              <Route path='reconciliation' element={<DataReconciliationPage />} />
            </Route>
            <Route path='source-files' element={<Navigate to='/library/source-sync/files' replace />} />
            <Route path='source-sync-errors'>
              <Route index element={<Navigate to='retry' replace />} />
              <Route path='retry' element={<LibrarySyncErrorsPage key='retry' />} />
              <Route path='blocked' element={<LibrarySyncErrorsPage key='blocked' retryable={false} />} />
            </Route>
            <Route path='source-sync-blocked' element={<Navigate to='/library/source-sync-errors/blocked' replace />} />
            <Route path=":resourceId" element={<LibraryResourceDetailPage />}/>
            <Route path=':resourceId/versions' element={<LibraryVersionHistoryPage />}/>
            <Route path=':resourceId/update-version' element={<LibraryUpdateVersion />}/>
          </Route>
          <Route path='search'>
            <Route index element={<Navigate to='query-methods' replace />} />
            <Route path='materials' element={<SearchAlias />} />
            <Route path='query-methods' element={<Suspense fallback={<p role="status">Đang mở tra cứu học liệu…</p>}><QueryMethodPage /></Suspense>} />
            <Route path='result' element={<SearchAlias />} />
          </Route>
          <Route path='experiments'>
            <Route index element={<Navigate to='evaluation-data' replace />} />
            <Route path='evaluation-data' element={<EvaluationDataPage />} />
            <Route path='run' element={<RunExperimentPage />} />
            <Route path='results' element={<EvaluationResultsPage />} />
            <Route path='reports' element={<ExperimentsReportPage />} />
          </Route>
          <Route path='systems'>
            <Route index element={<Navigate to='status' />} />
            <Route path='status' element={<SystemStatusPage />} />
            <Route path='users' element={<AccountManagementPage />} />
            <Route path='master-data' element={<MasterDataPage />} />
            <Route path='capabilities' element={<SystemCapalitiesPage />} />
            <Route path='master-sync' element={<Navigate to='/library/source-sync' replace />} />
          </Route>
        </Route>

        </Route>
      </Routes >
      </AuthProvider>
    </BrowserRouter >
  )
}

function SearchAlias() {
  const { search } = useLocation()
  return <Navigate to={`/search/query-methods${search}`} replace />
}
