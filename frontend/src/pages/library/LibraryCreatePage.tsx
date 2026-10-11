import React, { useState } from "react"
import { useNavigate } from "react-router"
import styles from './LibraryPages.module.css'

type CreateResourceRespones = {
    id: string
    title: string
    resource_type_code: string
    media_category: string
    mime_type: string
    file_name: string
    file_size: number
    source_hash: string
    initial_version_id: string
    version_number: number
    lifecycle_status: string,
    created_at: string
}

export function LibraryCreatePage() {
    const navigate = useNavigate()

    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    async function handleSubmit(
        event: React.FormEvent<HTMLFormElement>,
    ) {
        event.preventDefault()

        setLoading(true)
        setError('')

        const form = event.currentTarget
        const formData = new FormData(form)

        try {
            const response = await fetch('/api/v1/resources', {
                method: 'POST',
                body: formData
            })

            if (!response.ok) {
                const body = await response.json().catch(() => null)

                throw new Error(
                    body?.detail?.message ?? body?.detail ?? `HTTP ${response.status}`
                )
            }
            const createdResource: CreateResourceRespones = await response.json()

            navigate(`/library/processing?versionId=${encodeURIComponent(createdResource.initial_version_id)}`)
        }
        catch (e) {
            setError(
                e instanceof Error ? e.message : 'Không thể tạo học liệu',
            )
        } finally {
            setLoading(false)
        }
    }

    return (
        <section className={styles.page}>
            <header className={styles.pageHeader}>
                <h1>Thêm học liệu</h1>
            </header>
            <form className={`${styles.card} ${styles.form}`} onSubmit={handleSubmit}>

                <div className={styles.field}>
                    <label htmlFor="title">Tên học liệu</label>
                    <input type="text" id="title" name="title" required />
                </div>
                <div className={styles.field}>
                    <label htmlFor="resourceType">Mã loại học liệu</label>
                    <input type="text" name="resource_type_code" id="resourceType" required />
                </div>
                <div className={styles.field}>
                    <label htmlFor="providerId">
                        Mã nhà cung cấp
                    </label>
                    <input type="text" id="providerId" name="provider_id" required />
                </div>
                <div className={styles.field}>
                    <label htmlFor="providerName">Tên nhà cung cấp</label>
                    <input type="text" id="providerName" name="provider_name" required />
                </div>
                <div className={styles.field}>
                    <label htmlFor="file">Tải lên tệp học liệu</label>
                    <input id='file' type="file" name="file" accept="application/pdf,.pdf" required />
                </div>
                {error && (
                    <p className={styles.alert} role="alert">{error}</p>
                )}
                <div className={styles.actions}>
                    <button className={styles.primaryButton} type="submit" disabled={loading}>
                        {loading ? 'Đang tạo học liệu...' : 'Tạo học liệu'}
                    </button>
                </div>
            </form>
        </section>
    )
}
