export type PersonalProfile = {
    full_name: string; email: string; role_name: string; access_role: string; is_active: boolean
    email_verified: boolean | null; created_at: string | null; subject_name: string | null
    organization_name: string | null; address: string | null; bio: string | null; updated_at: string | null
    revision: string; editable: boolean; unavailable_reason: string | null
}
export type ProfileChanges = Pick<PersonalProfile, 'organization_name' | 'address' | 'bio' | 'revision'>

const messages: Record<string, string> = {
    PROFILE_CHANGED: 'Thông tin đã thay đổi ở nơi khác. Hãy tải lại hồ sơ trước khi lưu.',
    PROFILE_NOT_LINKED: 'Tài khoản này chưa liên kết với hồ sơ đồng bộ.',
    PROFILE_UNAVAILABLE: 'Chưa thể tải hồ sơ. Vui lòng thử lại sau.',
}

export class ProfileApiError extends Error {
    code: string
    constructor(code: string, message: string) { super(message); this.code = code }
}

function valid(value: unknown): value is PersonalProfile {
    if (value === null || typeof value !== 'object') return false
    const v = value as Record<string, unknown>
    return ['full_name', 'email', 'role_name', 'access_role', 'revision'].every(key => typeof v[key] === 'string')
        && ['is_active', 'editable'].every(key => typeof v[key] === 'boolean')
        && (v.email_verified === null || typeof v.email_verified === 'boolean')
        && ['created_at', 'subject_name', 'organization_name', 'address', 'bio', 'updated_at', 'unavailable_reason']
            .every(key => v[key] === null || typeof v[key] === 'string')
        && /^(missing|[a-f0-9]{64})$/.test(v.revision as string)
}

async function request(signal?: AbortSignal, values?: ProfileChanges): Promise<PersonalProfile> {
    const response = await fetch('/api/v1/auth/profile', {
        method: values ? 'PATCH' : 'GET', cache: 'no-store', credentials: 'same-origin', signal,
        headers: { Accept: 'application/json', ...(values ? { 'Content-Type': 'application/json' } : {}) },
        ...(values ? { body: JSON.stringify(values) } : {}),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
        const code = typeof data?.detail?.code === 'string' ? data.detail.code : 'PROFILE_UNAVAILABLE'
        throw new ProfileApiError(code, Object.hasOwn(messages, code) ? messages[code]
            : response.status === 401 ? 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'
                : response.status === 422 ? 'Thông tin nhập chưa hợp lệ. Vui lòng kiểm tra lại.'
                    : 'Chưa thể cập nhật hồ sơ. Vui lòng thử lại.')
    }
    if (!valid(data)) throw new ProfileApiError('PROFILE_INVALID_RESPONSE', 'Phản hồi hồ sơ không hợp lệ. Vui lòng thử lại.')
    return data
}

export const getPersonalProfile = (signal?: AbortSignal) => request(signal)
export const updatePersonalProfile = (values: ProfileChanges, signal?: AbortSignal) => request(signal, values)
