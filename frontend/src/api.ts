// 相对路径：兼容 fnOS 统一网关前缀（/app/mteam-manager）与直接访问两种部署形态
const base = import.meta.env.DEV ? '' : 'api'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      detail = body.detail || body.error || detail
    } catch { /* ignore */ }
    throw new ApiError(detail, resp.status)
  }
  return resp.json()
}

export const get = <T = any>(path: string) => api<T>(path)
export const post = <T = any>(path: string, body?: any) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const put = <T = any>(path: string, body: any) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const patch = <T = any>(path: string, body: any) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
export const del = <T = any>(path: string) => api<T>(path, { method: 'DELETE' })

// ---------- 工具 ----------
export function fmtSize(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0, v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(v >= 10 ? 1 : 2)} ${units[i]}`
}

export function fmtSpeed(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '0 B/s'
  return fmtSize(bytes) + '/s'
}

export function fmtNum(n?: number | null): string {
  if (n === null || n === undefined) return '-'
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function fmtTime(s?: string | null): string {
  if (!s) return '-'
  return s.slice(5, 16)
}

export function fmtRate(r?: number | null): string {
  if (r === null || r === undefined) return '-'
  if (r === Infinity) return '∞'
  return r.toFixed(2)
}
