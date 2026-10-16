import { Navigate, useLocation } from 'react-router'

export function ExperimentsReportPage() {
    const { search } = useLocation()
    return <Navigate to={`/experiments/results${search}`} replace />
}
