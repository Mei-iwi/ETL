import type { ContentUnit } from '../types/etl';

export interface ApiResponse<T> {
  ok: boolean;
  status: number;
  data?: T;
  error?: string;
  correlationId?: string | null;
}

export class ApiService {
  private baseUrl: string;

  constructor(baseUrl: string = '') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  setBaseUrl(url: string) {
    this.baseUrl = url.replace(/\/$/, '');
  }

  getBaseUrl() {
    return this.baseUrl;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<ApiResponse<T>> {
    const url = `${this.baseUrl}${path}`;
    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...(options.headers || {}),
        },
      });

      const correlationId = response.headers.get('X-Correlation-ID');
      let responseData: any = null;

      try {
        responseData = await response.json();
      } catch {
        // Non-JSON response
        responseData = null;
      }

      if (!response.ok) {
        let errorMsg = `HTTP ${response.status}`;
        if (response.status === 403) {
          errorMsg = 'Admin API disabled; set ADMIN_ENABLED=true in .env to use admin endpoints.';
        } else if (response.status === 503) {
          errorMsg = 'Database unavailable (503 Service Unavailable)';
        } else if (response.status === 422) {
          errorMsg = 'Invalid request fields (422 Unprocessable Entity)';
        } else if (response.status === 400) {
          errorMsg = responseData?.detail || 'Invalid ETL operation';
        } else if (responseData?.detail) {
          errorMsg = responseData.detail;
        }

        return {
          ok: false,
          status: response.status,
          error: errorMsg,
          correlationId,
        };
      }

      return {
        ok: true,
        status: response.status,
        data: responseData as T,
        correlationId,
      };
    } catch (err: any) {
      return {
        ok: false,
        status: 0,
        error: `Network error: ${err.message || 'Cannot connect to backend server'}`,
        correlationId: null,
      };
    }
  }

  async getHealth() {
    return this.request<{ status: string; database: string }>('/health');
  }

  async runMasterSync(stream: string = 'all', full: boolean = false) {
    return this.request<Record<string, any>>('/admin/sync/master', {
      method: 'POST',
      body: JSON.stringify({ stream, full }),
    });
  }

  async ingestResource(
    resourceId: string,
    bucket: string,
    objectKey: string,
    uploadedBy?: string | null
  ) {
    const body: Record<string, any> = {
      bucket,
      object_key: objectKey,
    };
    if (uploadedBy && uploadedBy.trim()) {
      body.uploaded_by = uploadedBy.trim();
    }

    return this.request<{ action: 'CREATED' | 'NO_CHANGE'; version: any }>(
      `/admin/resources/${encodeURIComponent(resourceId)}/versions/ingest`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      }
    );
  }

  async createOcrJob(versionId: string, triggeredBy?: string | null) {
    const body: Record<string, any> = {};
    if (triggeredBy && triggeredBy.trim()) {
      body.triggered_by = triggeredBy.trim();
    }

    return this.request<any>(
      `/admin/resource-versions/${encodeURIComponent(versionId)}/ocr-jobs`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      }
    );
  }

  async runPostprocess(versionId: string) {
    return this.request<{
      content_unit_count: number;
      coverage: { successful_pages: number; total_pages: number; failed_pages: number };
      input_fingerprint: string;
    }>(`/admin/resource-versions/${encodeURIComponent(versionId)}/postprocess`, {
      method: 'POST',
    });
  }

  async getEtlStatus(versionId: string) {
    return this.request<any>(
      `/admin/resource-versions/${encodeURIComponent(versionId)}/etl-status`
    );
  }

  async getContentUnits(versionId: string, offset = 0, limit = 50, search = '') {
    const query = new URLSearchParams({ offset: String(offset), limit: String(limit), search });
    return this.request<{ items: ContentUnit[]; total: number; offset: number; limit: number }>(
      `/admin/resource-versions/${encodeURIComponent(versionId)}/content-units?${query}`
    );
  }
}

export const api = new ApiService();
