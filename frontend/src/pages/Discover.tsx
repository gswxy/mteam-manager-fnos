import { useCallback, useEffect, useState } from 'react'
import { get, post, fmtSize, fmtNum, fmtTime, fmtRate } from '../api'
import { DiscountBadge, Empty, Modal, Progress, Spinner, useToast } from '../ui'

const TABS = [
  { key: 'latest', label: '最新发布' },
  { key: 'hot', label: '热门资源' },
  { key: 'free', label: '免费资源' },
  { key: 'rating', label: '豆瓣高分' },
]
const CATS = [
  { key: 'all', label: '全部' },
  { key: 'movie', label: '电影' },
  { key: 'tv', label: '电视剧' },
  { key: 'anime', label: '动漫' },
  { key: 'show', label: '综艺' },
]
const CAT_LABEL: Record<string, string> = {
  movie: '电影', tv: '电视剧', anime: '动漫', show: '综艺', other: '其他',
}
const QUALITIES = ['4k', '1080p', '720p']
const SIZES = [
  { key: '', label: '任意大小' },
  { key: '0-5', label: '< 5GB' },
  { key: '5-20', label: '5-20GB' },
  { key: '20-60', label: '20-60GB' },
  { key: '60-0', label: '> 60GB' },
]

interface Media {
  key: string; title: string; douban_rating?: number; imdb_rating?: number
  poster?: string; category: string; created_date?: string; version_count: number
  seeders_total: number; has_free: boolean; min_size?: number; max_size: number
  versions: Torrent[]
}
interface Torrent {
  id: string; name: string; size: number; seeders: number; leechers: number
  discount: string; discount_end?: string; created_date?: string; small_descr?: string
  douban_rating?: number; category?: string; labels?: string[]
  descr?: string; mediainfo?: string; risk?: any; favorited?: boolean
}

export default function DiscoverPage() {
  const toast = useToast()
  const [tab, setTab] = useState('latest')
  const [cat, setCat] = useState('all')
  const [q, setQ] = useState('')
  const [quality, setQuality] = useState<string[]>([])
  const [sizeRange, setSizeRange] = useState('')
  const [promoOnly, setPromoOnly] = useState(false)
  const [items, setItems] = useState<Media[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [coverage, setCoverage] = useState('')
  const [detail, setDetail] = useState<Media | null>(null)
  const [torrentDetail, setTorrentDetail] = useState<Torrent | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = useCallback(async (p = 1) => {
    setLoading(true)
    try {
      let data
      if (q.trim()) {
        data = await get(`/api/discover/search?q=${encodeURIComponent(q.trim())}&category=${cat}&page=${p}`)
      } else {
        data = await get(`/api/discover?tab=${tab}&category=${cat}&page=${p}`)
      }
      setItems(filterLocal(data.items || []))
      setTotal(data.total || 0)
      setCoverage(data.coverage || '')
    } catch (e: any) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [q, tab, cat])

  useEffect(() => { load(1); setPage(1) }, [tab, cat])
  useEffect(() => {
    const t = setTimeout(() => { if (q.trim()) { load(1); setPage(1) } }, 600)
    return () => clearTimeout(t)
  }, [q])

  function filterLocal(list: Media[]): Media[] {
    let out = list
    if (quality.length) {
      out = out.filter(m => m.versions?.some(v =>
        quality.some(qq => v.name.toLowerCase().includes(qq === '4k' ? '2160p' : qq))))
    }
    if (sizeRange) {
      const [minS, maxS] = sizeRange.split('-').map(Number)
      out = out.filter(m => m.versions?.some(v =>
        v.size >= minS * 1024 ** 3 && (!maxS || v.size <= maxS * 1024 ** 3)))
    }
    if (promoOnly) out = out.filter(m => m.has_free)
    return out
  }

  async function openDetail(m: Media) {
    setDetail(m)
    setDetailLoading(true)
    try {
      const fresh = await get(`/api/discover/media/${encodeURIComponent(m.key)}`)
      setDetail(fresh)
    } catch { /* 用列表数据 */ } finally {
      setDetailLoading(false)
    }
  }

  async function openTorrent(t: Torrent) {
    setTorrentDetail(null)
    setDetailLoading(true)
    try {
      const d = await get(`/api/discover/torrent/${t.id}`)
      setTorrentDetail(d)
    } catch (e: any) {
      toast(e.message, 'error')
    } finally {
      setDetailLoading(false)
    }
  }

  async function download(t: Torrent) {
    const m = detail || items.find(x => x.versions.some(v => v.id === t.id))
    const cat = m && m.category in CAT_LABEL ? m.category : 'other'
    try {
      await post(`/api/discover/torrent/${t.id}/download`, { category: cat })
      toast('已添加到下载中心', 'ok')
    } catch (e: any) {
      toast(e.message, 'error')
    }
  }

  async function favorite(t: Torrent) {
    try {
      const res = await post(`/api/discover/torrent/${t.id}/favorite`)
      toast(res.favorited ? '已收藏' : '已取消收藏', 'ok')
      if (torrentDetail) setTorrentDetail({ ...torrentDetail, favorited: res.favorited })
    } catch (e: any) {
      toast(e.message, 'error')
    }
  }

  const pageSize = 24
  const pages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="page">
      <h1 className="page-title">影视发现</h1>
      <div className="page-sub">资源来自 M-Team 站点实时接口 · 评分仅展示站点返回数据</div>

      <div className="card" style={{ padding: '14px 16px' }}>
        <div className="row">
          <input style={{ flex: 1, minWidth: 200 }} placeholder="搜索影视名称、关键词…"
            value={q} onChange={e => setQ(e.target.value)} />
          <button className="btn btn-primary" onClick={() => { load(1); setPage(1) }}>搜索</button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {TABS.map(t => (
            <button key={t.key} className={`btn btn-sm${tab === t.key && !q ? ' btn-primary' : ''}`}
              onClick={() => { setQ(''); setTab(t.key) }}>{t.label}</button>
          ))}
          <span style={{ width: 1, height: 18, background: 'var(--border)' }} />
          {CATS.map(c => (
            <button key={c.key} className={`btn btn-sm btn-ghost${cat === c.key ? ' btn-primary' : ''}`}
              style={cat === c.key ? {} : { color: 'var(--text-2)' }}
              onClick={() => setCat(c.key)}>{c.label}</button>
          ))}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          {QUALITIES.map(qq => (
            <button key={qq} className={`btn btn-sm${quality.includes(qq) ? ' btn-primary' : ''}`}
              onClick={() => setQuality(arr => qq === 'any' ? [] :
                arr.includes(qq) ? arr.filter(x => x !== qq) : [...arr, qq])}>
              {qq.toUpperCase()}
            </button>
          ))}
          <select value={sizeRange} onChange={e => setSizeRange(e.target.value)}>
            {SIZES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <label className="row" style={{ gap: 5, fontSize: 12.5, cursor: 'pointer' }}>
            <input type="checkbox" checked={promoOnly} onChange={e => setPromoOnly(e.target.checked)} />
            仅看促销
          </label>
        </div>
      </div>

      {coverage && !q && (
        <div style={{ fontSize: 12, color: 'var(--text-3)', margin: '0 2px 12px' }}>ⓘ {coverage}</div>
      )}

      {loading ? <Spinner /> : items.length === 0 ? (
        <Empty text={q ? '没有匹配的结果，换个关键词试试' : '暂无缓存数据，请先添加账号后点击搜索或刷新'} />
      ) : (
        <>
          <div className="media-grid">
            {items.map(m => (
              <div key={m.key} className="media-card" onClick={() => openDetail(m)}>
                <div className="media-poster" style={{ backgroundImage: m.poster ? `url(${m.poster})` : undefined }}>
                  <div className="badges">
                    {m.has_free && <DiscountBadge discount="FREE" />}
                  </div>
                  <div className="rating">
                    <span>★ {m.douban_rating ?? m.imdb_rating ?? '—'}</span>
                    <span>{fmtNum(m.seeders_total)} 种</span>
                  </div>
                </div>
                <div className="media-meta">
                  <div className="media-title" title={m.title}>{m.title}</div>
                  <div className="media-sub">
                    {CAT_LABEL[m.category] || '其他'} · {m.version_count} 个版本 · {fmtSize(m.max_size)}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {pages > 1 && (
            <div className="row" style={{ justifyContent: 'center', marginTop: 20 }}>
              <button className="btn btn-sm" disabled={page <= 1}
                onClick={() => { const p = page - 1; setPage(p); load(p) }}>上一页</button>
              <span style={{ fontSize: 12.5, color: 'var(--text-3)' }}>{page} / {pages}（共 {total} 部）</span>
              <button className="btn btn-sm" disabled={page >= pages}
                onClick={() => { const p = page + 1; setPage(p); load(p) }}>下一页</button>
            </div>
          )}
        </>
      )}

      {/* 影视详情 */}
      <Modal open={!!detail} onClose={() => setDetail(null)}
        title={detail?.title || ''} wide
        footer={<button className="btn" onClick={() => setDetail(null)}>关闭</button>}>
        {detail && (
          <>
            <div className="detail-head">
              <div className="detail-poster" style={{ backgroundImage: detail.poster ? `url(${detail.poster})` : undefined }} />
              <div style={{ flex: 1 }}>
                <div className="row" style={{ marginBottom: 8 }}>
                  {detail.douban_rating && <span>豆瓣 <b style={{ color: 'var(--accent)' }}>{detail.douban_rating}</b></span>}
                  {detail.imdb_rating && <span style={{ color: 'var(--text-3)' }}>IMDb {detail.imdb_rating}</span>}
                  <span style={{ color: 'var(--text-3)' }}>{CAT_LABEL[detail.category] || '其他'}</span>
                </div>
                <div style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 10 }}>
                  共 {detail.version_count} 个资源版本 · 累计做种 {fmtNum(detail.seeders_total)} 人
                  {detail.created_date && ` · 最新 ${fmtTime(detail.created_date)}`}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                  同一作品的不同版本聚合展示（按豆瓣 / IMDb / 标题识别）
                </div>
              </div>
            </div>
            {detailLoading && <Spinner />}
            <div className="table-wrap" style={{ marginTop: 16 }}>
              <table className="table">
                <thead><tr><th>资源版本</th><th>大小</th><th>做种</th><th>促销</th><th>发布时间</th><th></th></tr></thead>
                <tbody>
                  {(detail.versions || []).map(v => (
                    <tr key={v.id} className="clickable" onClick={() => openTorrent(v)}>
                      <td style={{ maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        title={v.name}>{v.name}</td>
                      <td>{fmtSize(v.size)}</td>
                      <td>{fmtNum(v.seeders)}</td>
                      <td><DiscountBadge discount={v.discount} /></td>
                      <td>{fmtTime(v.created_date)}</td>
                      <td><button className="btn btn-sm btn-primary"
                        onClick={e => { e.stopPropagation(); download(v) }}>下载</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Modal>

      {/* 种子详情 */}
      <Modal open={!!torrentDetail} onClose={() => setTorrentDetail(null)}
        title={torrentDetail?.name || ''} wide
        footer={
          <>
            <button className="btn" onClick={() => torrentDetail && favorite(torrentDetail)}>
              {torrentDetail?.favorited ? '★ 已收藏' : '☆ 收藏'}
            </button>
            <button className="btn btn-primary" onClick={() => torrentDetail && download(torrentDetail)}>
              手动下载
            </button>
          </>
        }>
        {torrentDetail && (
          <>
            <div className="row" style={{ marginBottom: 12 }}>
              <DiscountBadge discount={torrentDetail.discount} />
              <span>{fmtSize(torrentDetail.size)}</span>
              <span style={{ color: 'var(--text-3)' }}>做种 {fmtNum(torrentDetail.seeders)} · 下载 {fmtNum(torrentDetail.leechers)}</span>
              <span style={{ color: 'var(--text-3)' }}>{fmtTime(torrentDetail.created_date)}</span>
              {torrentDetail.discount_end && (
                <span style={{ color: 'var(--warn)', fontSize: 12 }}>促销至 {torrentDetail.discount_end}</span>
              )}
            </div>
            {torrentDetail.risk && (
              <div className={`card${torrentDetail.risk.level === 'danger' ? '' : ''}`} style={{
                marginBottom: 12, padding: '10px 14px',
                background: torrentDetail.risk.level === 'danger' ? 'var(--danger-soft)' :
                  torrentDetail.risk.level === 'warn' ? 'var(--warn-soft)' : 'var(--ok-soft)',
                border: 'none',
              }}>
                <b>分享率预测</b>：计量下载 {fmtSize(torrentDetail.risk.billed_download)} ·
                预计上传 {fmtSize(torrentDetail.risk.estimated_upload)} ·
                下载后约 {fmtRate(torrentDetail.risk.predicted_rate)}（当前 {fmtRate(torrentDetail.risk.current_rate)}）
                <div style={{ fontSize: 12, marginTop: 4 }}>{torrentDetail.risk.message}</div>
              </div>
            )}
            {torrentDetail.small_descr && (
              <div style={{ marginBottom: 10 }}><b>简介</b>：{torrentDetail.small_descr}</div>
            )}
            {torrentDetail.descr && (
              <div style={{ marginBottom: 10, maxHeight: 180, overflowY: 'auto', fontSize: 12.5, color: 'var(--text-2)' }}>
                {torrentDetail.descr.slice(0, 2000)}
              </div>
            )}
            {torrentDetail.mediainfo && (
              <details>
                <summary style={{ cursor: 'pointer', fontWeight: 580, marginBottom: 6 }}>MediaInfo</summary>
                <pre className="mono" style={{
                  background: 'var(--bg-soft)', padding: 12, borderRadius: 8,
                  overflowX: 'auto', maxHeight: 260, whiteSpace: 'pre-wrap',
                }}>{torrentDetail.mediainfo}</pre>
              </details>
            )}
          </>
        )}
      </Modal>
    </div>
  )
}
