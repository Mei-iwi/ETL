export function canManageResourceDeletion(user?: {
    role: string; admin_feature_enabled: boolean; must_change_password?: boolean
}) {
    return user?.role === 'ADMIN' && user.admin_feature_enabled && !user.must_change_password
}

const ERRORS: Record<string, string> = {
    AUTH_FORBIDDEN: 'Bạn không có quyền thực hiện thao tác này.',
    AUTH_ADMIN_DISABLED: 'Chức năng quản trị đang tắt.',
    RESOURCE_NOT_FOUND: 'Không tìm thấy học liệu. Hãy tải lại danh sách.',
    RESOURCE_DELETION_CONFLICT: 'Học liệu đã thay đổi. Hãy tải lại danh sách trước khi thử lại.',
    RESOURCE_ORIGIN_UNVERIFIED: 'Chưa xác minh được nguồn quản lý học liệu.',
    RESOURCE_RESTORE_SOURCE_RESTRICTED: 'Không thể khôi phục vì học liệu tại nguồn không còn đủ điều kiện truy cập.',
    RESOURCE_RESTORE_VERSION_NOT_READY: 'Phiên bản hiện tại chưa đủ điều kiện khôi phục.',
    AUTH_MASTER_STALE: 'Dữ liệu xác thực chưa được đồng bộ kịp. Vui lòng thử lại sau.',
}

export async function changeResourceDeletion(
    id: string, restore: boolean, revision: number, reason: string, signal?: AbortSignal,
): Promise<{ resource_id: string; deleted: boolean; revision: number; changed: boolean }> {
    const response = await fetch(`/api/v1/resources/${encodeURIComponent(id)}${restore ? '/restore' : ''}`, {
        method: restore ? 'POST' : 'DELETE', signal,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ expected_revision: revision, reason: reason.trim() || null }),
    })
    const body = await response.json().catch(() => null)
    if (!response.ok) {
        throw new Error(ERRORS[body?.detail?.code] ??
            (response.status === 401 ? 'Phiên đăng nhập đã hết hạn.' : 'Không thể cập nhật học liệu. Vui lòng thử lại.'))
    }
    return body
}
