type ScopePrincipal = {
    id?: string; mode?: string; role?: string; roles?: readonly string[]
    permissions?: readonly string[]; resource_ids?: readonly string[]
    must_change_password?: boolean; admin_feature_enabled?: boolean
}

// Cosmetic profile fields and grant ordering do not change access. This key
// only controls UI invalidation; the backend still authorizes every request.
export function identityScope(mode: string, user?: ScopePrincipal) {
    const canonical = (values?: readonly string[]) => [...new Set(values ?? [])].sort()
    return JSON.stringify([mode, user?.mode, user?.id, user?.role, canonical(user?.roles),
        canonical(user?.permissions), canonical(user?.resource_ids),
        Boolean(user?.must_change_password), Boolean(user?.admin_feature_enabled)])
}
