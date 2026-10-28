import { Component, Suspense } from 'react'
import type { ReactNode } from 'react'

type Props = { children: ReactNode; resetKey: string }
type State = { failed: boolean; routeKey: string }

class RouteErrorBoundary extends Component<Props, State> {
    state = { failed: false, routeKey: this.props.resetKey }

    static getDerivedStateFromError() {
        return { failed: true }
    }

    static getDerivedStateFromProps(props: Props, state: State) {
        return props.resetKey !== state.routeKey ? { failed: false, routeKey: props.resetKey } : null
    }

    render() {
        if (this.state.failed) {
            return <div className="route-state" role="alert">
                <p>Không thể hiển thị trang. Vui lòng tải lại để thử lại.</p>
                <button type="button" onClick={() => window.location.reload()}>Tải lại trang</button>
            </div>
        }
        return this.props.children
    }
}

export function RouteContent({ children, resetKey }: Props) {
    return <RouteErrorBoundary resetKey={resetKey}>
        <Suspense fallback={<div className="route-state" role="status" aria-live="polite">Đang tải trang…</div>}>
            {children}
        </Suspense>
    </RouteErrorBoundary>
}
