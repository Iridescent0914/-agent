const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '')

interface ApiErrorShape {
  message?: string
  detail?: string
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const body = (await response.json()) as ApiErrorShape
      message = body.message || body.detail || message
    } catch {
      // Keep the status-based fallback when the server did not return JSON.
    }
    throw new Error(message)
  }
  return (await response.json()) as T
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
