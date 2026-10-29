import { useCallback, useEffect, useState } from 'react'
import { get, post, fmtSize, fmtSpeed, fmtTime, fmtRate } from '../api'
import { DiscountBadge, Empty, Progress, Spinner, StatusBadge, Switch, useInterval, useToast, statCard } from '../ui'

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'downloading', label: '正在下载' },
  { key: 'seeding', label: '正在做种' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '下载失败' },
  { key: 'auto', label: '自动任务' },
  { key: 'protected', label: '已保护' },
  { key: 'cleanup', label: '待清理' },
  { key: 'history', label: '历史记录' },
]

interface Task {
  id: number; torrent_id: string; torrent_name: string; size: number
  source: 'manual' | 'auto'; rule_name?: string; hit_reason?: string
  category?: string; save_path: string; fnos_task_id?: number
  status: string; progress: number; dl_speed: number; up_speed: number
  uploaded: number; downloaded: number; ratio?: number; protected: number
  added_at: string; completed_at?: string; removed_at?: string; remove_reason?: string
  meta?: any
}

export default function DownloadsPage() {
  const toast = useToast()
  const [filter, setFilter] = useState('all')
  const [items, setItems] = useState<Task[]>([])
  const [history, setHistory] = useState<any[]>([])
  const [stats, setStats] = useState<any>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      if (filter === 'history') {
        const h = await get('/api/downloads/history')
        setHistory(h.items || [])
        setItems([])
      } else {
        const d = await get(`/api/downloads?filter=${filter}`)
        setItems(d.items || [])
        setStats(d.stats || {})
      }
    } catch (e: any) {
      if (e.status !== 401) toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { load() }, [filter])
  useInterval(load, 8000)

  async function act(id: number, action: string, body?: any) {
    if (action === 'delete' && body?.delete_files) {
      if (!window.confirm('确定删除任务并删除已下载数据？此操作不可恢复。')) return
    } else if (action === 'delete' && !window.confirm('确定删除任务记录？')) {
      return
    }
    setBusy(id)
    try {
      await post(`/api/downloads/${id}/${action}`, body || {})
      toast('操作成功', 'ok')
      load()
    } catch (e: any) {
      toast(e.message, 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="page">
      <h1 className="page-title">下载管理</h1>
      <div className="page-sub">任务统一由飞牛下载中心执行 · 每 8 秒自动刷新</div>

      <div className="grid grid-stats" style={{ marginBottom: 16 }}>
        {statCard('总任务', stats.total ?? '-')}
        {statCard('下载中', stats.downloading ?? '-')}
        {statCard('做种中', stats.seeding ?? '-')}
        {statCard('自动任务', stats.auto ?? '-')}
        {statCard('已保护', stats.protected ?? '-')}
        {statCard('总占用', fmtSize(stats.total_size))}
      </div>

      <div className="tabs">
        {FILTERS.map(f => (
          <div key={f.key} className={`tab${filter === f.key ? ' active' : ''}`}
            onClick={() => { setFilter(f.key); setLoading(true) }}>{f.label}</div>
        ))}
      </div>

      {loading ? <Spinner /> : filter === 'history' ? (
        history.length === 0 ? <Empty text="暂无历史记录" /> : (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>种子</th><th>来源</th><th>大小</th><th>删除时间</th><th>删除原因</th><th>回收空间</th></tr></thead>
                <tbody>
                  {history.map(t => (
                    <tr key={t.id}>
                      <td title={t.torrent_name} style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.torrent_name}</td>
                      <td>{t.source === 'auto' ? '自动' : '手动'}</td>
                      <td>{fmtSize(t.size)}</td>
                      <td>{fmtTime(t.removed_at)}</td>
                      <td style={{ color: 'var(--text-3)' }}>{t.remove_reason || '-'}</td>
                      <td>{t.freed_bytes ? fmtSize(t.freed_bytes) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      ) : items.length === 0 ? <Empty text="没有符合条件的任务" /> : (
        items.map(t => (
          <div className="card" key={t.id} style={{ padding: '14px 18px' }}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row" style={{ marginBottom: 6 }}>
                  <StatusBadge status={t.status} />
                  <DiscountBadge discount={t.meta?.discount || ''} />
                  {t.source === 'auto' ? <span className="badge badge-gray">自动</span> : <span className="badge badge-gray">手动</span>}
                  {t.protected ? <span className="badge badge-ok">永久保留</span> : null}
                </div>
                <div style={{ fontWeight: 600, fontSize: 13.5, wordBreak: 'break-all' }}>{t.torrent_name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
                  {fmtSize(t.size)} · 保存至 {t.save_path}
                  {t.source === 'auto' && t.rule_name && ` · 规则「${t.rule_name}」`}
                  {t.source === 'auto' && t.hit_reason && ` · ${t.hit_reason}`}
                </div>
                {(t.status === 'downloading' || t.status === 'waiting' || t.status === 'checking') && (
                  <div style={{ marginTop: 8, maxWidth: 480 }}>
                    <Progress value={t.progress} />
                    <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
                      {(t.progress * 100).toFixed(1)}% · ↓{fmtSpeed(t.dl_speed)} · ↑{fmtSpeed(t.up_speed)}
                    </div>
                  </div>
                )}
                {(t.status === 'seeding' || t.status === 'completed') && (
                  <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 6 }}>
                    ↑{fmtSpeed(t.up_speed)} · 累计上传 {fmtSize(t.uploaded)} · 分享率 {fmtRate(t.ratio)}
                    {t.completed_at && ` · 完成于 ${fmtTime(t.completed_at)}`}
                  </div>
                )}
                {(t.status === 'error' || t.status === 'lost') && (
                  <div style={{ fontSize: 12, color: 'var(--danger)', marginTop: 6 }}>
                    {t.status === 'lost' ? '任务在下载中心中不存在（可能已被手动删除）' : '下载出错，可在飞牛下载中心查看详情'}
                  </div>
                )}
              </div>
              <div className="row" style={{ flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                <div className="row">
                  {(t.status === 'downloading' || t.status === 'waiting') && (
                    <button className="btn btn-sm" disabled={busy === t.id} onClick={() => act(t.id, 'pause')}>暂停</button>
                  )}
                  {t.status === 'paused' && (
                    <button className="btn btn-sm" disabled={busy === t.id} onClick={() => act(t.id, 'resume')}>恢复</button>
                  )}
                  {!t.protected && (
                    <button className="btn btn-sm" disabled={busy === t.id}
                      onClick={() => act(t.id, 'protect')}>永久保留</button>
                  )}
                  {t.protected && (
                    <button className="btn btn-sm" disabled={busy === t.id}
                      onClick={() => act(t.id, 'unprotect')}>取消保留</button>
                  )}
                  <button className="btn btn-sm btn-danger" disabled={busy === t.id}
                    onClick={() => act(t.id, 'delete', { delete_files: false })}>删除任务</button>
                  <button className="btn btn-sm btn-danger" disabled={busy === t.id}
                    onClick={() => act(t.id, 'delete', { delete_files: true })}>删除并清数据</button>
                </div>
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  )
}
