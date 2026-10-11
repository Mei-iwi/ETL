import { useCallback, useEffect, useRef, useState } from "react"
import styles from './SytemStatusPage.module.css'
import system from '../../assets/img/system.png'
import database from '../../assets/img/database.png'
export function SystemStatusPage() {

    type ConnectionState = 'checking' | 'online' | 'offline'

    const [connectionState, setConnectionState] = useState<ConnectionState>('checking')

    type HealthData = {
        status?: string
        database?: string
        detail?: string
    }

    const [healthData, setHealthData] = useState<HealthData | null>(null)
    const [healthError, setHealthError] = useState('')

    const databaseState = connectionState === 'checking' ? 'checking' : healthData?.database === 'ok' ? 'online' : 'offline'

    const controllerRef = useRef<AbortController | null>(null)

    const checkHealth = useCallback(async () => {
        controllerRef.current?.abort()
        const controller = new AbortController()
        controllerRef.current = controller

        setConnectionState('checking')
        setHealthData(null)
        setHealthError('')

        try {
            const response = await fetch('/health', {
                signal: controller.signal,
                cache: 'no-store',
            })

            const data = await response.json()

            if (controller.signal.aborted) return

            if (response.ok) {
                setConnectionState('online')
            } else {
                setConnectionState('offline')
            }

            setHealthData(data || `HTTP ${response.status}`)

        } catch (e) {
            if (controller.signal.aborted) return
            setConnectionState('offline')

            setHealthError(
                e instanceof Error ? e.message : 'Mất kết nối'
            )
        }
    }, [])


    useEffect(() => {
        void checkHealth()

        return () => {
            controllerRef.current?.abort()
        }
    }, [checkHealth])

    return (
        <section>
            <h1>Trạng thái hệ thống</h1>
            <div className={styles.content}>
                <div className={styles.grid}>
                    <p className={styles.card}>
                        <strong>
                            Trạng thái hệ thống:
                        </strong>
                        <img className={styles.img} src={system} alt="" />
                        <span className={styles.statusRow}>
                            <span className={`${styles.statusValue} connection-status ${connectionState}`} role="status" aria-live="polite">
                                {connectionState === 'checking' && 'Đang kiểm tra...'}
                                {connectionState === 'online' && 'Đã kết nối'}
                                {connectionState == 'offline' && 'Mất kết nối'}
                            </span>
                        </span>
                    </p>
                    <p className={styles.card}>
                        <strong>Trạng thái Database: </strong>
                        <img className={styles.img} src={database} alt="" />
                        <span className={styles.statusRow}>
                            <span className={`${styles.statusValue} ${styles.databaseStatus} ${styles[databaseState]}`}>
                                {connectionState === 'checking' ? 'Đang kiểm tra...'
                                    : healthData?.database === 'ok' ? 'Database đã kết nối' : 'Database không khả dụng'}
                            </span>
                        </span>
                    </p>
                </div>
                <button className={styles.btn} type="button" onClick={() => void checkHealth()} disabled={connectionState === 'checking'}>
                    {connectionState === 'checking' ? 'Đang kiểm tra kết nối ...' : 'Kiểm tra lại'}
                </button>

                {healthData && (
                    <div className="health-respone">
                        <h4>Phản hồi từ hệ thống</h4>
                        <pre>{healthData ? JSON.stringify(healthData, null, 2) : healthError}</pre>
                    </div>
                )}
            </div>
        </section>
    )
}
