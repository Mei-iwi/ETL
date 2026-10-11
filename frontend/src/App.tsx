import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from 'react-router'
import './App.css'

import { AppLayout } from './components/layout/AppLayout'
import { OverviewSummary } from './pages/overview/OverviewSummary'
import { OverviewProcess } from './pages/overview/OverviewProcess'

import { LibraryListPage } from './pages/library/LibraryListPage'
import { LibraryCreatePage } from './pages/library/LibraryCreatePage'
import { LibraryProcessingPage } from './pages/library/LibraryProcessingPage'
import { LibraryIndexingPage } from './pages/library/LibraryIndexingPage'

import { MaterialSearchPage } from './pages/search/MaterialSearchPage'
import { QueryMethodPage } from './pages/search/QueryMethodPage'
import { SearchResultpage } from './pages/search/SearchResultPage'

import { EvaluationDataPage } from './pages/experiments/EvaluationDataPage'
import { EvaluationResultsPage } from './pages/experiments/EvalutionResultsPage'
import { ExperimentsReportPage } from './pages/experiments/ExperimentsReportsPage'
import { RunExperimentPage } from './pages/experiments/RunExperimentPage'

import { SystemStatusPage } from './pages/system/SystemStatusPage'
import { SystemCapalitiesPage } from './pages/system/SystemCapalitiesPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppLayout />}>
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
          </Route>
          <Route path='search'>
            <Route index element={<Navigate to='materials' />} />
            <Route path='materials' element={<MaterialSearchPage />} />
            <Route path='query-methods' element={<QueryMethodPage />} />
            <Route path='result' element={<SearchResultpage />} />
          </Route>
          <Route path='experiments'>
            <Route index element={<Navigate to='evaluation-data' />} />
            <Route path='evaluation-data' element={<EvaluationDataPage />} />
            <Route path='run' element={<RunExperimentPage />} />
            <Route path='results' element={<EvaluationResultsPage />} />
            <Route path='reports' element={<ExperimentsReportPage />} />
          </Route>
          <Route path='systems'>
            <Route index element={<Navigate to='status' />} />
            <Route path='status' element={<SystemStatusPage />} />
            <Route path='capabilities' element={<SystemCapalitiesPage />} />
          </Route>
        </Route>

      </Routes >
    </BrowserRouter >
  )
}
