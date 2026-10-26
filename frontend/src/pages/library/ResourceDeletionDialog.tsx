import { useEffect, useRef, useState } from 'react'
import { changeResourceDeletion } from '../../services/resourceDeletionApi'
import styles from './LibraryListPage.module.css'

export function ResourceDeletionDialog({ resource, restore, onClose, onSuccess }: {
    resource: { id: string; title: string; deletion_revision: number }
    restore: boolean; onClose: () => void; onSuccess: () => void
}) {
    const dialog = useRef<HTMLDialogElement>(null)
    const request = useRef<AbortController | null>(null)
    const [reason, setReason] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')
    useEffect(() => {
        const element = dialog.current
        element?.showModal()
        return () => { request.current?.abort(); element?.close() }
    }, [])

    async function confirm(event: React.FormEvent<HTMLFormElement>) {
        event.preventDefault()
        if (busy) return
        const controller = new AbortController()
        request.current = controller
        setBusy(true); setError('')
        try {
            await changeResourceDeletion(resource.id, restore, resource.deletion_revision, reason, controller.signal)
            if (!controller.signal.aborted) onSuccess()
        } catch (err) {
            if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Không thể cập nhật học liệu.')
        } finally {
            if (!controller.signal.aborted) setBusy(false)
        }
    }

    return <dialog ref={dialog} className={styles.confirmDialog} aria-labelledby="deletion-title"
        onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
        <form onSubmit={event => void confirm(event)} aria-busy={busy}>
            <h2 id="deletion-title">{restore ? 'Khôi phục học liệu' : 'Xóa học liệu'}</h2>
            <p className={styles.confirmTitle}>{resource.title}</p>
            <p>{restore ? 'Đưa học liệu trở lại danh sách nếu vẫn đáp ứng quyền và trạng thái tại nguồn.'
                : 'Học liệu sẽ được ẩn khỏi danh sách và tìm kiếm. File và dữ liệu nguồn được giữ nguyên.'}</p>
            <label htmlFor="deletion-reason">Lý do (không bắt buộc)</label>
            <textarea id="deletion-reason" maxLength={500} value={reason} disabled={busy}
                onChange={event => setReason(event.target.value)} rows={3} />
            {error && <p role="alert" className={styles.error}>{error}</p>}
            <div className={styles.dialogActions}>
                <button type="button" autoFocus disabled={busy} onClick={onClose}>Hủy</button>
                <button type="submit" disabled={busy} className={restore ? styles.restoreAction : styles.deleteAction}>
                    {busy ? 'Đang xử lý…' : restore ? 'Khôi phục' : 'Xóa học liệu'}
                </button>
            </div>
        </form>
    </dialog>
}
