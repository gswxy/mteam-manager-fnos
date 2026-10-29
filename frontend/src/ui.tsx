import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'

// ---------- Toast ----------
type Toast = { id: number; msg: string; kind: 'ok' | 'warn' | 'error' | 'info' }
const ToastCtx = createContext<(msg: string, kind?: Toast['kind']) => void>(() => {})
export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((msg: string, kind: Toast['kind'] = 'info') => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t, { id, msg, kind }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4200)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">
        {toasts.map(t => <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>)}
      </div>
    </ToastCtx.Provider>
  )
}

// ---------- 主题 ----------
export function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('mtm-theme')
    if (saved === 'dark' || saved === 'light') return saved
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('mtm-theme', theme)
  }, [theme])
  return { theme, toggle: () => setTheme(t => (t === 'light' ? 'dark' : 'light')) }
}

// ---------- 通用小组件 ----------
export function Badge({ kind, children }: { kind: string; children: React.ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>
}

export function DiscountBadge({ discount }: { discount: string }) {
  if (discount === 'FREE') return <Badge kind="free">免费</Badge>
  if (discount === '_2X_FREE') return <Badge kind="free">免费·2X</Badge>
  if (discount === '_2X') return <Badge kind="2x">2X上传</Badge>
  if (discount === 'PERCENT_50') return <Badge kind="2x">50%</Badge>
  if (discount === '_2X_PERCENT_50') return <Badge kind="2x">50%·2X</Badge>
  return null
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    downloading: ['ok', '下载中'], waiting: ['gray', '等待中'], paused: ['warn', '已暂停'],
    checking: ['gray', '校验中'], seeding: ['ok', '做种中'], completed: ['ok', '已完成'],
    error: ['danger', '失败'], lost: ['danger', '已丢失'], pending: ['gray', '排队中'],
    pushing: ['gray', '推送中'], deleted: ['gray', '已删除'],
  }
  const [kind, label] = map[status] || ['gray', status]
  return <Badge kind={kind}>{label}</Badge>
}

export function Progress({ value, ok }: { value: number; ok?: boolean }) {
  return (
    <div className={`progress${ok ? ' ok' : ''}`}>
      <div style={{ width: `${Math.min(100, Math.max(0, (value || 0) * 100))}%` }} />
    </div>
  )
}

export function Modal({ open, onClose, title, wide, children, footer }: {
  open: boolean; onClose: () => void; title: React.ReactNode; wide?: boolean
  children: React.ReactNode; footer?: React.ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    if (open) window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="modal-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className={`modal${wide ? ' wide' : ''}`}>
        <h3>{title}</h3>
        {children}
        {footer && <div className="modal-actions">{footer}</div>}
      </div>
    </div>
  )
}

export function Switch({ checked, onChange, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean
}) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} disabled={disabled}
        onChange={e => onChange(e.target.checked)} />
      <span />
    </label>
  )
}

export function Spinner() {
  return <div className="spinner" />
}

export function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <div>{text}</div>
    </div>
  )
}

export function useInterval(fn: () => void, ms: number | null) {
  useEffect(() => {
    if (ms === null) return
    const id = setInterval(fn, ms)
    return () => clearInterval(id)
  })
}

export function statCard(label: string, value: React.ReactNode, sub?: React.ReactNode) {
  return (
    <div className="card" style={{ marginBottom: 0, padding: '14px 16px' }}>
      <div style={{ fontSize: 12, color: 'var(--text-3)', fontWeight: 560 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 680, margin: '4px 0 2px' }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{sub}</div>}
    </div>
  )
}
