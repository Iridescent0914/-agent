const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '')

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  if (path.startsWith('/api/')) {
    const apiIndex = API_BASE_URL.indexOf('/api/')
    return (apiIndex >= 0 ? API_BASE_URL.slice(0, apiIndex) : API_BASE_URL) + path
  }
  return `${API_BASE_URL}/${path.replace(/^\/+/, '')}`
}

interface ApiErrorShape {
  message?: string
  detail?: string
  details?: Array<{ msg?: string }>
}

async function apiRequest<T>(path: string, options: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options)
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const body = (await response.json()) as ApiErrorShape
      message = body.details?.map((detail) => detail.msg).filter(Boolean).join('；') || body.message || body.detail || message
    } catch {
      // Keep the status-based fallback when the server did not return JSON.
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(path, { signal })
}

export function apiPost<T>(path: string, body: unknown, signal?: AbortSignal, headers?: Record<string, string>): Promise<T> {
  return apiRequest<T>(path, { method: 'POST', body: JSON.stringify(body), signal, headers: { 'Content-Type': 'application/json', ...headers } })
}

export function apiPostForm<T>(path: string, body: FormData, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(path, { method: 'POST', body, signal })
}

export function apiPut<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(path, { method: 'PUT', body: JSON.stringify(body), signal, headers: { 'Content-Type': 'application/json' } })
}

export function apiPatch<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(path, { method: 'PATCH', body: JSON.stringify(body), signal, headers: { 'Content-Type': 'application/json' } })
}

export function apiDelete<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(path, { method: 'DELETE', body: JSON.stringify(body), signal, headers: { 'Content-Type': 'application/json' } })
}

export function queryString(values: Record<string, string | number | boolean | undefined | null>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}
