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
import { AccountManagementPage, ChangePasswordPage } from './auth/AccountPages'
import { PersonalProfilePage } from './auth/PersonalProfilePage'

import { AppLayout } from './components/layout/AppLayout'
import { OverviewSummary } from './pages/overview/OverviewSummary'
import { OverviewProcess } from './pages/overview/OverviewProcess'

import { LibraryListPage } from './pages/library/LibraryListPage'
import { LibrarySyncErrorsPage } from './pages/library/LibrarySyncErrorsPage'
import { LibrarySourceFilesPage } from './pages/library/LibrarySourceFilesPage'
import { LibraryCreatePage } from './pages/library/LibraryCreatePage'
import { LibraryProcessingPage } from './pages/library/LibraryProcessingPage'
import { LibraryIndexingPage } from './pages/library/LibraryIndexingPage'
import { LibraryResourceDetailPage } from './pages/library/LibraryResourceDetailPage'
import { LibraryVersionHistoryPage } from './pages/library/LibraryVersionHistoryPage'

import { QueryMethodPage } from './pages/search/QueryMethodPage'

import { EvaluationDataPage } from './pages/experiments/EvaluationDataPage'
import { EvaluationResultsPage } from './pages/experiments/EvalutionResultsPage'
import { ExperimentsReportPage } from './pages/experiments/ExperimentsReportsPage'
import { RunExperimentPage } from './pages/experiments/RunExperimentPage'

import { SystemStatusPage } from './pages/system/SystemStatusPage'
import { MasterDataPage } from './pages/system/MasterDataPage'
import { SystemCapalitiesPage } from './pages/system/SystemCapalitiesPage'
import { SystemMasterSyncLayout, SystemMasterSyncPage } from './pages/system/SystemMasterSyncPage'
import { LibraryUpdateVersion } from './pages/library/LibraryUpdateVersion'

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
            <Route path='query-methods' element={<QueryMethodPage />} />
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
