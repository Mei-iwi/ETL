import { useCallback, useState } from "react";
import {updateResourceMetadata } from '../services/resourceApi'
import { clearSearchViewCache } from '../pages/search/searchViewCache'
import type {
    UpdateResourceMetadataRequest,
    UpdateResourceMetadataResponse,
} from '../types/resourceMetadata'

export function useUpdateResourceMetadata(){
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [data, setData] = useState<UpdateResourceMetadataResponse | null>(null)

    const reset =useCallback(() => {
        setIsLoading(false)
        setError(null)
        setData(null)
    }, [])

    const mutate = useCallback(async(
        resourceId: string,
        payload: UpdateResourceMetadataRequest,
    ): Promise<UpdateResourceMetadataResponse> => {
        setIsLoading(true)
        setError(null)
        setData(null)

        try {
            const result = await updateResourceMetadata(resourceId, payload)
            clearSearchViewCache()
            setData(result)
            return result
        }catch (cause: unknown){
            const message = cause instanceof Error
                ? cause.message
                : 'Không thể cập nhật metadata.'
            setError(message)
            throw cause instanceof Error ? cause : new Error(message)
        } finally {
            setIsLoading(false)
        }
    }, [])
    return { isLoading, error, data, mutate, reset}
}

