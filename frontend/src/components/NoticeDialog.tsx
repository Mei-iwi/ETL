import { useEffect, useId, useRef } from 'react'
import styles from './NoticeDialog.module.css'

export function NoticeDialog({ title, message, onClose }: { title: string; message: string; onClose: () => void }) {
    const ref = useRef<HTMLDialogElement>(null)
    const id = useId()
    useEffect(() => {
        const dialog = ref.current
        dialog?.showModal()
        return () => dialog?.close()
    }, [])
    return <dialog ref={ref} className={styles.dialog} aria-labelledby={id} onCancel={event => {
        event.preventDefault(); onClose()
    }}>
        <h2 id={id}>{title}</h2><p>{message}</p>
        <button autoFocus type="button" onClick={onClose}>Đóng</button>
    </dialog>
}
