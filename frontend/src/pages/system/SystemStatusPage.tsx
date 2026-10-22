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
    const [databaseState, setDatabaseState] = useState<ConnectionState | 'unknown'>('checking')
    const [databaseError, setDatabaseError] = useState('')
    const [checking, setChecking] = useState(false)

    const controllerRef = useRef<AbortController | null>(null)

    const checkHealth = useCallback(async () => {
        controllerRef.current?.abort()
        const controller = new AbortController()
        controllerRef.current = controller

        setConnectionState('checking')
        setDatabaseState('checking')
        setChecking(true)
        setHealthData(null)
        setHealthError('')
        setDatabaseError('')

        try {
            const response = await fetch('/health', {
                signal: controller.signal,
                cache: 'no-store',
            })

            const data: HealthData = await response.json()

            if (controller.signal.aborted) return

            const isOnline = response.ok &&
                data?.status === 'ok'

            setConnectionState(isOnline ? 'online' : 'offline')
            setHealthData(data)

            if (!response.ok) {
                setHealthError(typeof data?.detail === 'string' ? data.detail : `HTTP ${response.status}`)
            }
            if (!isOnline) {
                setDatabaseState(data?.database === 'unavailable' ? 'offline' : 'unknown')
                return
            }
            if (data.database !== undefined) {
                setDatabaseState(data.database === 'ok' ? 'online' : 'offline')
                return
            }
            try {
                const databaseResponse = await fetch('/api/v1/system/health', {
                    signal: controller.signal, cache: 'no-store',
                })
                const databaseData: HealthData = await databaseResponse.json()
                if (controller.signal.aborted) return
                if (databaseResponse.ok && databaseData?.database === 'ok') {
                    setDatabaseState('online')
                    setHealthData({ ...data, database: 'ok' })
                } else if (databaseResponse.status === 503 && databaseData?.database === 'unavailable') {
                    setDatabaseState('offline')
                    setHealthData({ ...data, database: 'unavailable' })
                } else {
                    setDatabaseState('unknown')
                    setDatabaseError('Chưa thể kiểm tra Database. Vui lòng kiểm tra phiên đăng nhập và quyền quản trị.')
                }
            } catch {
                if (controller.signal.aborted) return
                setDatabaseState('unknown')
                setDatabaseError('Chưa thể kiểm tra kết nối Database. Vui lòng thử lại.')
            }

        } catch (e) {
            if (controller.signal.aborted) return
            setConnectionState('offline')
            setDatabaseState('unknown')

            setHealthError(
                e instanceof Error ? e.message : 'Mất kết nối'
            )
        } finally { if (!controller.signal.aborted) setChecking(false) }
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
                                {connectionState === 'offline' && 'Mất kết nối'}
                            </span>
                        </span>
                    </p>
                    <p className={styles.card}>
                        <strong>Trạng thái Database: </strong>
                        <img className={styles.img} src={database} alt="" />
                        <span className={styles.statusRow}>
                            <span className={`${styles.statusValue} ${styles.databaseStatus} ${styles[databaseState]}`}>
                                {databaseState === 'checking' ? 'Đang kiểm tra...'
                                    : databaseState === 'online' ? 'Database đã kết nối'
                                    : databaseState === 'offline' ? 'Database không khả dụng' : 'Chưa xác định'}
                            </span>
                        </span>
                    </p>
                </div>
                <button className={styles.btn} type="button" onClick={() => void checkHealth()} disabled={checking}>
                    {checking ? 'Đang kiểm tra kết nối ...' : 'Kiểm tra lại'}
                </button>

                {(healthData || healthError) && (
                    <div className="health-respone">
                        <h4>Phản hồi từ hệ thống</h4>
                        {healthData && <pre>{JSON.stringify(healthData, null, 2)}</pre>}
                        {healthError && <p role="alert">{healthError}</p>}
                        {databaseError && <p role="alert">{databaseError}</p>}
                    </div>
                )}
            </div>
        </section>
    )
}
