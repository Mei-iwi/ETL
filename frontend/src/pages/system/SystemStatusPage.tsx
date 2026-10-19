import { useCallback, useEffect, useRef, useState } from 'react'
import { Database, RefreshCw, Server } from 'lucide-react'
import styles from './SystemStatusPage.module.css'

type ConnectionState = 'checking' | 'online' | 'offline'
type HealthData = {
    status?: string
    database?: string
}

const statusLabels: Record<ConnectionState, string> = {
    checking: 'Đang kiểm tra…',
    online: 'Đã kết nối',
    offline: 'Chưa kết nối',
}

const summaryLabels: Record<ConnectionState, string> = {
    checking: 'Đang kiểm tra kết nối',
    online: 'Hệ thống hoạt động bình thường',
    offline: 'Kết nối đang gặp sự cố',
}

export function SystemStatusPage() {
    const [connectionState, setConnectionState] = useState<ConnectionState>('checking')
    const [healthData, setHealthData] = useState<HealthData | null>(null)
    const [healthError, setHealthError] = useState('')
    const controllerRef = useRef<AbortController | null>(null)
    const databaseState: ConnectionState = connectionState === 'checking'
        ? 'checking'
        : healthData?.database === 'ok' ? 'online' : 'offline'

    const checkHealth = useCallback(async () => {
        controllerRef.current?.abort()
        const controller = new AbortController()
        controllerRef.current = controller
        const timeout = window.setTimeout(() => controller.abort(), 10000)

        setConnectionState('checking')
        setHealthData(null)
        setHealthError('')

        try {
            const response = await fetch('/health', {
                signal: controller.signal,
                cache: 'no-store',
            })
            const data: HealthData | null = await response.json()
            if (controller.signal.aborted) return

            const isOnline = response.ok && data?.status === 'ok' && data?.database === 'ok'
            setConnectionState(isOnline ? 'online' : 'offline')
            setHealthData(data)
            if (!isOnline) {
                setHealthError('Một số kết nối chưa sẵn sàng. Vui lòng kiểm tra lại sau ít phút.')
            }
        } catch {
            if (controllerRef.current !== controller) return
            setConnectionState('offline')
            setHealthError('Không thể kết nối đến hệ thống. Vui lòng kiểm tra mạng và thử lại.')
        } finally {
            window.clearTimeout(timeout)
        }
    }, [])

    useEffect(() => {
        const initialCheck = window.setTimeout(() => void checkHealth(), 0)
        return () => {
            window.clearTimeout(initialCheck)
            controllerRef.current?.abort()
            controllerRef.current = null
        }
    }, [checkHealth])

    const services = [
        { name: 'Hệ thống', label: 'Kết nối máy chủ', icon: Server, state: connectionState },
        { name: 'Cơ sở dữ liệu', label: 'Kết nối dữ liệu', icon: Database, state: databaseState },
    ]

    return (
        <section className={styles.page} aria-labelledby="system-status-title">
            <header className={styles.header}>
                <h1 id="system-status-title">Trạng thái hệ thống</h1>
            </header>

            <div className={styles.panelHeader}>
                <div className={styles.summary} role="status" aria-live="polite">
                    <span className={`${styles.dot} ${styles[connectionState]}`} aria-hidden="true" />
                    <div><h2>{summaryLabels[connectionState]}</h2></div>
                </div>
                <button className={styles.button} type="button" onClick={() => void checkHealth()} disabled={connectionState === 'checking'}>
                    <RefreshCw size={15} className={connectionState === 'checking' ? styles.spin : undefined} aria-hidden="true" />
                    {connectionState === 'checking' ? 'Đang kiểm tra…' : 'Kiểm tra lại'}
                </button>
            </div>

            <div className={styles.services} role="region" aria-label="Trạng thái kết nối dịch vụ" aria-busy={connectionState === 'checking'}>
                {services.map(({ name, label, icon: Icon, state }) => (
                    <article key={name} className={styles.service}>
                        <div className={styles.serviceHeading}>
                            <span className={styles.serviceIcon}><Icon size={25} strokeWidth={1.6} aria-hidden="true" /></span>
                            <div><h3>{name}</h3><span className={styles.serviceLabel}>{label}</span></div>
                        </div>
                        <div className={styles.serviceStatus}>
                            <span>Trạng thái</span>
                            <span className={`${styles.badge} ${styles[state]}`}><span className={styles.dot} aria-hidden="true" />{statusLabels[state]}</span>
                        </div>
                    </article>
                ))}
            </div>
            {healthError && <div className={styles.notice} role="alert">{healthError}</div>}
        </section>
    )
}
