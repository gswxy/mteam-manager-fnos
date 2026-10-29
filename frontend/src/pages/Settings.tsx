import { useCallback, useEffect, useState } from 'react'
import { get, post, put, del, fmtSize } from '../api'
import { Badge, Empty, Modal, Switch, Spinner, useToast } from '../ui'

const SECTIONS = [
  { key: 'mt', label: '馒头账号' },
  { key: 'fnos', label: '飞牛下载中心' },
  { key: 'manual', label: '手动下载目录' },
  { key: 'auto', label: '自动下载目录' },
  { key: 'rules', label: '自动下载规则' },
  { key: 'cleanup', label: '自动删除规则' },
  { key: 'about', label: '关于' },
]

const CAT_NAMES: Record<string, string> = { movie: '电影', tv: '电视剧', anime: '动漫', show: '综艺', other: '其他' }
const CAT_KEYS = ['movie', 'tv', 'anime', 'show', 'other']

export default function SettingsPage() {
  const toast = useToast()
  const [section, setSection] = useState('mt')

  return (
    <div className="page">
      <h1 className="page-title">设置</h1>
      <div className="page-sub">所有账号、目录与自动化规则均保存在本机，升级不丢失</div>
      <div className="tabs">
        {SECTIONS.map(s => (
          <div key={s.key} className={`tab${section === s.key ? ' active' : ''}`}
            onClick={() => setSection(s.key)}>{s.label}</div>
        ))}
      </div>
      {section === 'mt' && <MtSection toast={toast} />}
      {section === 'fnos' && <FnosSection toast={toast} />}
      {section === 'manual' && <PathsSection kind="manual" toast={toast} />}
      {section === 'auto' && <PathsSection kind="auto" toast={toast} />}
      {section === 'rules' && <RulesSection toast={toast} />}
      {section === 'cleanup' && <CleanupSection toast={toast} />}
      {section === 'about' && <AboutSection />}
    </div>
  )
}

type ToastFn = (msg: string, kind?: 'ok' | 'warn' | 'error' | 'info') => void

/* ================= A. 馒头账号全局 ================= */
function MtSection({ toast }: { toast: ToastFn }) {
  const [cfg, setCfg] = useState({ request_interval: 4, account_sync_interval: 600, proxy: '' })
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    get('/api/settings/mteam').then(setCfg).catch(() => {})
  }, [])
  async function save() {
    setSaving(true)
    try {
      await put('/api/settings/mteam', cfg)
      toast('已保存', 'ok')
    } catch (e: any) { toast(e.message, 'error') } finally { setSaving(false) }
  }
  return (
    <div className="card">
      <div className="card-title">M-Team API 全局设置</div>
      <label className="field"><span>API 请求最小间隔（秒）</span>
        <input type="number" min="1" max="60" value={cfg.request_interval}
          onChange={e => setCfg({ ...cfg, request_interval: Number(e.target.value) })} />
        <div className="help">M-Team 官方限制约 100 次 / 5 分钟，默认 4 秒保守间隔，请勿调低</div></label>
      <label className="field"><span>账号资料自动同步间隔（秒）</span>
        <input type="number" min="60" max="86400" value={cfg.account_sync_interval}
          onChange={e => setCfg({ ...cfg, account_sync_interval: Number(e.target.value) })} /></label>
      <label className="field"><span>网络代理（可选，推荐 HTTP 代理）</span>
        <input value={cfg.proxy} placeholder="例如 http://192.168.0.1:7890"
          onChange={e => setCfg({ ...cfg, proxy: e.target.value.trim() })} />
        <div className="help">
          NAS 无法直连 M-Team（Cloudflare 间歇阻断）时，在此填写局域网代理地址（如
          Clash/v2ray 的 HTTP 端口）后保存；留空为直连。修改后账号连接测试、搜索与下载都会走代理。
        </div></label>
      <button className="btn btn-primary" disabled={saving} onClick={save}>保存</button>
      <div className="help" style={{ marginTop: 10 }}>
        账号的增删改请在「账号中心」操作；API Key 加密存储于 NAS 本地，不会暴露到浏览器或日志。
      </div>
    </div>
  )
}

/* ================= B. 飞牛下载中心 ================= */
function FnosSection({ toast }: { toast: ToastFn }) {
  const [cfg, setCfg] = useState({ ws_url: 'ws://127.0.0.1:5666/websocket', username: '', password: '' })
  const [info, setInfo] = useState<any>(null)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    get('/api/settings/fnos').then((d: any) => setCfg({ ...cfg, ...d, password: '' })).catch(() => {})
  }, [])

  async function save() {
    try {
      await put('/api/settings/fnos', cfg)
      toast('已保存', 'ok')
    } catch (e: any) { toast(e.message, 'error') }
  }

  async function test() {
    setTesting(true); setInfo(null)
    try {
      await save()
      const r = await post('/api/settings/fnos/test')
      setInfo(r)
      toast(r.ok ? '下载中心连接正常' : `连接失败：${r.error}`, r.ok ? 'ok' : 'error')
    } catch (e: any) {
      setInfo({ ok: false, error: e.message })
      toast(e.message, 'error')
    } finally { setTesting(false) }
  }

  return (
    <div className="card">
      <div className="card-title">飞牛下载中心对接
        {info && (info.ok
          ? <Badge kind="ok">已连接 · {info.latency_ms}ms</Badge>
          : <Badge kind="danger">连接失败</Badge>)}
      </div>
      <label className="field"><span>WebSocket 接口地址</span>
        <input value={cfg.ws_url} onChange={e => setCfg({ ...cfg, ws_url: e.target.value })} />
        <div className="help">应用运行在 NAS 内，默认本机 5666 端口，一般无需修改</div></label>
      <label className="field"><span>fnOS 用户名</span>
        <input value={cfg.username} onChange={e => setCfg({ ...cfg, username: e.target.value })}
          placeholder="用于登录 fnOS 下载中心的 NAS 账号" /></label>
      <label className="field"><span>fnOS 密码</span>
        <input type="password" value={cfg.password} onChange={e => setCfg({ ...cfg, password: e.target.value })}
          placeholder="留空表示不修改" />
        <div className="help">仅用于本机调用下载中心接口，加密存储，不会出现在日志中</div></label>
      <div className="row">
        <button className="btn btn-primary" onClick={save}>保存</button>
        <button className="btn" disabled={testing} onClick={test}>{testing ? '检测中…' : '保存并测试连接'}</button>
      </div>
      {info && (
        <div style={{ marginTop: 14, fontSize: 12.5 }}>
          {info.ok ? (
            <div style={{ color: 'var(--ok)' }}>
              ✓ 连接成功，延迟 {info.latency_ms}ms
              {info.default_save_dir && ` · 下载中心默认目录 ${info.default_save_dir}`}
              {info.stat && ` · 总下载 ${fmtSize(info.stat.dl_total_size)} / 总上传 ${fmtSize(info.stat.up_total_size)}`}
            </div>
          ) : (
            <div style={{ color: 'var(--danger)' }}>✗ {info.error}
              <div style={{ color: 'var(--text-3)', marginTop: 4 }}>
                常见原因：账号密码错误（errno 8192）、下载中心未启动、fnOS 版本接口变化。
                连接失败不影响其他功能浏览，但无法添加新任务。
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ================= C/D. 目录 ================= */
function PathsSection({ kind, toast }: { kind: 'manual' | 'auto'; toast: ToastFn }) {
  const [paths, setPaths] = useState<Record<string, string>>({})
  const [byCategory, setByCategory] = useState(true)
  const [saving, setSaving] = useState(false)
  const [picker, setPicker] = useState<{ field: string; path: string } | null>(null)
  const [pickerItems, setPickerItems] = useState<any[]>([])

  useEffect(() => {
    if (kind === 'manual') {
      get('/api/settings/manual-paths').then(setPaths).catch(() => {})
    } else {
      get('/api/settings/auto-paths').then((d: any) => {
        setPaths({ root: d.root || '' }); setByCategory(!!d.by_category)
      }).catch(() => {})
    }
  }, [kind])

  async function loadPicker(path: string) {
    try {
      let items: any[]
      if (path === '/') {
        items = (await get('/api/settings/fs/volumes')).items
      } else {
        items = (await get(`/api/settings/fs/list?path=${encodeURIComponent(path)}`)).items
        items = [{ name: '..', path: path.split('/').slice(0, -1).join('/') || '/', up: true }, ...items]
      }
      setPickerItems(items)
    } catch (e: any) {
      setPickerItems([])
      toast(e.message, 'error')
    }
  }

  function openPicker(field: string) {
    const cur = paths[field] || '/'
    setPicker({ field, path: cur })
    loadPicker(cur.startsWith('/vol') ? cur : '/')
  }

  async function save() {
    setSaving(true)
    try {
      if (kind === 'manual') {
        await put('/api/settings/manual-paths', paths)
      } else {
        await put('/api/settings/auto-paths', { root: paths.root || '', by_category: byCategory })
      }
      toast('目录设置已保存并验证', 'ok')
    } catch (e: any) { toast(e.message, 'error') } finally { setSaving(false) }
  }

  return (
    <div className="card">
      <div className="card-title">
        {kind === 'manual' ? '手动下载目录（长期保存，不参与自动清理）' : '自动下载目录（与手动下载完全隔离）'}
      </div>
      {kind === 'manual' ? (
        <>
          {CAT_KEYS.map(cat => (
            <div key={cat} className="row" style={{ marginBottom: 10 }}>
            <span style={{ width: 64, fontWeight: 560, fontSize: 13 }}>{CAT_NAMES[cat]}</span>
              <input style={{ flex: 1 }} value={paths[cat] || ''} readOnly
                placeholder="点击右侧选择 NAS 目录" onClick={() => openPicker(cat)} />
              <button className="btn btn-sm" onClick={() => openPicker(cat)}>选择</button>
            </div>
          ))}
          <div className="help" style={{ marginBottom: 10 }}>
            手动下载默认长期保存，永不参与自动删种。保存时将验证目录存在性与写入权限。
          </div>
        </>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            <span style={{ width: 64, fontWeight: 560, fontSize: 13 }}>根目录</span>
            <input style={{ flex: 1 }} value={paths.root || ''} readOnly
              placeholder="例如 /vol2/PT自动下载" onClick={() => openPicker('root')} />
            <button className="btn btn-sm" onClick={() => openPicker('root')}>选择</button>
          </div>
          <div className="row" style={{ marginBottom: 10 }}>
            <Switch checked={byCategory} onChange={setByCategory} />
            <span style={{ fontSize: 13 }}>按影视类别建立子目录（电影 / 电视剧 / 动漫 / 综艺）</span>
          </div>
          <div className="help" style={{ marginBottom: 10 }}>
            自动下载任务将记录专属来源、规则与实际保存路径；默认不加入飞牛影视媒体库。
          </div>
        </>
      )}
      <button className="btn btn-primary" disabled={saving} onClick={save}>
        {saving ? '验证中…' : '保存并验证'}</button>

      <Modal open={!!picker} onClose={() => setPicker(null)}
        title={`选择目录${picker ? ` → ${paths[picker.field] || '（未设置）'}` : ''}`}
        footer={<>
          <button className="btn" onClick={() => setPicker(null)}>取消</button>
          <button className="btn btn-primary" onClick={() => {
            if (picker) setPaths({ ...paths, [picker.field]: picker.path })
            setPicker(null)
          }}>使用当前目录</button>
        </>}>
        {pickerItems.length === 0 ? <Empty text="空目录或无权访问" /> : (
          <div style={{ maxHeight: 420, overflowY: 'auto' }}>
            {pickerItems.map((item: any) => (
              <div key={item.path} className="row" style={{
                justifyContent: 'space-between', padding: '8px 10px', borderRadius: 8,
                cursor: 'pointer', background: picker && item.path === picker.path ? 'var(--accent-soft)' : 'transparent',
              }} onClick={() => {
                setPicker({ field: picker!.field, path: item.path })
                if (!item.up) loadPicker(item.path)
                else loadPicker(item.path)
              }}>
                <span>{item.up ? '📁 ..（返回上级）' : `📁 ${item.name}`}</span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>{item.path}</span>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}

/* ================= E. 自动下载规则 ================= */
function RulesSection({ toast }: { toast: ToastFn }) {
  const [cfg, setCfg] = useState<any>(null)
  const [rules, setRules] = useState<any[]>([])
  const [editing, setEditing] = useState<any | null>(null)
  const [preview, setPreview] = useState<any>(null)
  const [previewing, setPreviewing] = useState(false)

  const load = useCallback(async () => {
    const [c, r] = await Promise.all([
      get('/api/settings/auto-download'), get('/api/settings/rules')])
    setCfg(c); setRules(r.items || [])
  }, [])
  useEffect(() => { load() }, [load])

  async function saveCfg() {
    try {
      await put('/api/settings/auto-download', cfg)
      toast('自动下载设置已保存', 'ok')
    } catch (e: any) { toast(e.message, 'error') }
  }

  async function saveRule(r: any) {
    try {
      const body = {
        name: r.name, kind: r.kind, enabled: !!r.enabled,
        categories: r.config?.categories || [], qualities: r.config?.qualities || [],
        min_size_gb: r.config?.min_size_gb || 0, max_size_gb: r.config?.max_size_gb || 0,
        promo_mode: r.config?.promo_mode || 'free_first', allow_paid: !!r.config?.allow_paid,
        min_seeders: r.config?.min_seeders || 0, min_leechers: r.config?.min_leechers || 0,
        keywords: r.config?.keywords || [], exclude_keywords: r.config?.exclude_keywords || [],
      }
      if (r.id) await put(`/api/settings/rules/${r.id}`, body)
      else await post('/api/settings/rules', body)
      toast('规则已保存', 'ok')
      setEditing(null)
      load()
    } catch (e: any) { toast(e.message, 'error') }
  }

  async function runPreview() {
    setPreviewing(true); setPreview(null)
    try {
      const r = await post('/api/settings/auto-download/preview')
      setPreview(r)
    } catch (e: any) { toast(e.message, 'error') } finally { setPreviewing(false) }
  }

  if (!cfg) return <Spinner />
  return (
    <>
      <div className="card">
        <div className="card-title">
          自动下载总开关
          <span className="spacer" />
          <Switch checked={!!cfg.enabled} onChange={v => setCfg({ ...cfg, enabled: v })} />
          <Badge kind={cfg.enabled ? 'ok' : 'gray'}>{cfg.enabled ? '已开启' : '默认关闭'}</Badge>
        </div>
        <div className="grid grid-2">
          <label className="field"><span>自动搜索间隔（分钟）</span>
            <input type="number" min="5" max="1440" value={cfg.interval_minutes}
              onChange={e => setCfg({ ...cfg, interval_minutes: Number(e.target.value) })} /></label>
          <label className="field"><span>每日最多新增任务</span>
            <input type="number" min="0" max="500" value={cfg.daily_max}
              onChange={e => setCfg({ ...cfg, daily_max: Number(e.target.value) })} /></label>
          <label className="field"><span>最大同时下载数</span>
            <input type="number" min="0" max="100" value={cfg.max_concurrent}
              onChange={e => setCfg({ ...cfg, max_concurrent: Number(e.target.value) })} /></label>
          <label className="field"><span>自动下载空间预算（GB）</span>
            <input type="number" min="0" value={cfg.space_budget_gb}
              onChange={e => setCfg({ ...cfg, space_budget_gb: Number(e.target.value) })} /></label>
          <label className="field"><span>NAS 最低剩余空间（GB）</span>
            <input type="number" min="0" value={cfg.min_free_gb}
              onChange={e => setCfg({ ...cfg, min_free_gb: Number(e.target.value) })} /></label>
          <label className="field"><span>运行时间段（留空为全天）</span>
            <input placeholder="例如 02:00-08:00" value={cfg.time_window}
              onChange={e => setCfg({ ...cfg, time_window: e.target.value })} /></label>
        </div>
        <div className="row">
          <button className="btn btn-primary" onClick={saveCfg}>保存设置</button>
          <button className="btn" disabled={previewing} onClick={runPreview}>
            {previewing ? '预览中…' : '规则预览（不实际下载）'}</button>
        </div>
        {preview && (
          <div style={{ marginTop: 14 }}>
            <b style={{ fontSize: 13 }}>预览结果</b>
            {preview.stopped_reason && <div style={{ color: 'var(--warn)', fontSize: 12.5, marginTop: 6 }}>
              ⚠ {preview.stopped_reason}</div>}
            {(preview.matched || []).length > 0 && (
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table className="table">
                  <thead><tr><th>种子</th><th>大小</th><th>促销</th><th>命中规则</th><th>原因</th></tr></thead>
                  <tbody>
                    {preview.matched.slice(0, 30).map((m: any) => (
                      <tr key={m.torrent_id}>
                        <td style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          title={m.name}>{m.name}</td>
                        <td>{fmtSize(m.size)}</td><td>{m.discount}</td>
                        <td>{m.rule}</td><td style={{ color: 'var(--text-3)' }}>{m.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(preview.skipped || []).length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 6 }}>
                另有 {preview.skipped.length} 个种子命中但被跳过（重复 / 风险 / 预算）
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">下载规则
          <span className="spacer" />
          <button className="btn btn-primary btn-sm"
            onClick={() => setEditing({ name: '', kind: 'preference', enabled: false, config: { promo_mode: 'free_only' } })}>
            + 新建规则</button>
        </div>
        {rules.length === 0 ? <Empty text="暂无规则。自动下载默认关闭，配置规则后可在上方开启。" /> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>名称</th><th>类型</th><th>条件</th><th>启用</th><th>操作</th></tr></thead>
              <tbody>
                {rules.map(r => (
                  <tr key={r.id}>
                    <td><b>{r.name}</b></td>
                    <td>{{ preference: '影视偏好', free: '免费资源', seeding: '保种' }[r.kind as string] || r.kind}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-3)', maxWidth: 400 }}>
                      {ruleSummary(r.config)}
                    </td>
                    <td>
                      <Switch checked={!!r.enabled} onChange={async v => {
                        await put(`/api/settings/rules/${r.id}`, { ...ruleToBody(r), enabled: v })
                        load()
                      }} />
                    </td>
                    <td>
                      <div className="row">
                        <button className="btn btn-sm" onClick={() => setEditing(r)}>编辑</button>
                        <button className="btn btn-sm btn-danger" onClick={async () => {
                          if (!window.confirm(`删除规则「${r.name}」？`)) return
                          await del(`/api/settings/rules/${r.id}`); load()
                        }}>删除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <RuleModal rule={editing} onClose={() => setEditing(null)} onSave={saveRule} />
    </>
  )
}

function ruleSummary(c: any): string {
  if (!c) return '-'
  const parts: string[] = []
  if (c.categories?.length) parts.push(`类别 ${c.categories.map((x: string) => CAT_NAMES[x] || x).join('/')}`)
  if (c.qualities?.length) parts.push(`画质 ${c.qualities.join('/')}`)
  if (c.min_size_gb || c.max_size_gb) parts.push(`大小 ${c.min_size_gb || 0}-${c.max_size_gb || '∞'}GB`)
  parts.push(c.promo_mode === 'free_only' ? '仅免费' : c.promo_mode === 'any' ? '不限促销' : '免费优先')
  if (c.min_seeders) parts.push(`做种≥${c.min_seeders}`)
  if (c.keywords?.length) parts.push(`关键词 ${c.keywords.join(',')}`)
  return parts.join(' · ') || '-'
}

function ruleToBody(r: any) {
  const c = r.config || {}
  return {
    name: r.name, kind: r.kind, enabled: !!r.enabled,
    categories: c.categories || [], qualities: c.qualities || [],
    min_size_gb: c.min_size_gb || 0, max_size_gb: c.max_size_gb || 0,
    promo_mode: c.promo_mode || 'free_first', allow_paid: !!c.allow_paid,
    min_seeders: c.min_seeders || 0, min_leechers: c.min_leechers || 0,
    keywords: c.keywords || [], exclude_keywords: c.exclude_keywords || [],
  }
}

function RuleModal({ rule, onClose, onSave }: {
  rule: any; onClose: () => void; onSave: (r: any) => void
}) {
  const [r, setR] = useState<any>(null)
  useEffect(() => { setR(rule ? JSON.parse(JSON.stringify(rule)) : null) }, [rule])
  if (!r) return null
  const c = r.config || (r.config = {})
  const toggleArr = (key: string, v: string) => {
    const arr = c[key] || []
    c[key] = arr.includes(v) ? arr.filter((x: string) => x !== v) : [...arr, v]
    setR({ ...r })
  }
  return (
    <Modal open onClose={onClose} title={r.id ? `编辑规则：${r.name}` : '新建规则'}
      footer={<>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn btn-primary" disabled={!r.name} onClick={() => onSave(r)}>保存</button>
      </>}>
      <label className="field"><span>规则名称</span>
        <input value={r.name} onChange={e => setR({ ...r, name: e.target.value })} placeholder="例如：热门免费电影" /></label>
      <label className="field"><span>规则类型</span>
        <select value={r.kind} onChange={e => setR({ ...r, kind: e.target.value })}>
          <option value="preference">影视偏好自动下载</option>
          <option value="free">免费资源自动下载</option>
          <option value="seeding">分享率维护型保种下载</option>
        </select></label>
      <label className="field"><span>影视类别</span>
        <div className="row">
          {['movie', 'tv', 'anime', 'show'].map(cat => (
            <button key={cat} className={`btn btn-sm${(c.categories || []).includes(cat) ? ' btn-primary' : ''}`}
              onClick={() => toggleArr('categories', cat)}>{CAT_NAMES[cat]}</button>
          ))}
        </div></label>
      <label className="field"><span>画质偏好</span>
        <div className="row">
          {['4k', '1080p', '720p'].map(q => (
            <button key={q} className={`btn btn-sm${(c.qualities || []).includes(q) ? ' btn-primary' : ''}`}
              onClick={() => toggleArr('qualities', q)}>{q.toUpperCase()}</button>
          ))}
          <span className="help" style={{ margin: 0 }}>不选 = 不限</span>
        </div></label>
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field" style={{ flex: 1, marginBottom: 0 }}><span>最小体积（GB）</span>
          <input type="number" min="0" value={c.min_size_gb || ''} placeholder="0"
            onChange={e => { c.min_size_gb = Number(e.target.value || 0); setR({ ...r }) } } /></label>
        <label className="field" style={{ flex: 1, marginBottom: 0 }}><span>最大体积（GB）</span>
          <input type="number" min="0" value={c.max_size_gb || ''} placeholder="不限"
            onChange={e => { c.max_size_gb = Number(e.target.value || 0); setR({ ...r }) } } /></label>
      </div>
      <label className="field"><span>促销策略</span>
        <select value={c.promo_mode || 'free_first'} onChange={e => { c.promo_mode = e.target.value; setR({ ...r }) }}>
          <option value="free_only">仅限免费资源</option>
          <option value="free_first">免费优先（允许非免费）</option>
          <option value="any">不限（需通过分享率风险检查）</option>
        </select></label>
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field" style={{ flex: 1, marginBottom: 0 }}><span>最低做种人数</span>
          <input type="number" min="0" value={c.min_seeders || ''} placeholder="0"
            onChange={e => { c.min_seeders = Number(e.target.value || 0); setR({ ...r }) } } /></label>
        <label className="field" style={{ flex: 1, marginBottom: 0 }}><span>最低下载数（活跃度）</span>
          <input type="number" min="0" value={c.min_leechers || ''} placeholder="0"
            onChange={e => { c.min_leechers = Number(e.target.value || 0); setR({ ...r }) } } /></label>
      </div>
      <label className="field"><span>包含关键词（逗号分隔，可空）</span>
        <input value={(c.keywords || []).join(',')} placeholder="例如：4K,HDR"
          onChange={e => { c.keywords = e.target.value.split(',').map(s => s.trim()).filter(Boolean); setR({ ...r }) } } /></label>
      <label className="field"><span>排除关键词（逗号分隔，可空）</span>
        <input value={(c.exclude_keywords || []).join(',')}
          onChange={e => { c.exclude_keywords = e.target.value.split(',').map(s => s.trim()).filter(Boolean); setR({ ...r }) } } /></label>
    </Modal>
  )
}

/* ================= F. 自动删除 ================= */
function CleanupSection({ toast }: { toast: ToastFn }) {
  const [cfg, setCfg] = useState<any>(null)
  const [preview, setPreview] = useState<any>(null)
  const [previewing, setPreviewing] = useState(false)

  useEffect(() => {
    get('/api/settings/auto-cleanup').then(setCfg).catch(() => {})
  }, [])

  async function save() {
    try {
      await put('/api/settings/auto-cleanup', cfg)
      toast('自动清理设置已保存', 'ok')
    } catch (e: any) { toast(e.message, 'error') }
  }

  async function runPreview() {
    setPreviewing(true); setPreview(null)
    try {
      setPreview(await post('/api/settings/auto-cleanup/preview'))
    } catch (e: any) { toast(e.message, 'error') } finally { setPreviewing(false) }
  }

  if (!cfg) return <Spinner />
  return (
    <>
      <div className="card" style={{ borderColor: 'var(--warn)' }}>
        <div className="card-title">
          自动清理开关
          <span className="spacer" />
          <Switch checked={!!cfg.enabled} onChange={v => setCfg({ ...cfg, enabled: v })} />
          <Badge kind={cfg.enabled ? 'ok' : 'gray'}>{cfg.enabled ? '已开启' : '默认关闭'}</Badge>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', background: 'var(--bg-soft)', borderRadius: 8, padding: '10px 14px', lineHeight: 1.8 }}>
          <b>安全边界</b>：仅清理本应用创建的自动下载任务；永久保留、手动下载、未完成任务绝不清理；
          H&R 风险标记或做种义务无法确认时禁止删除；无可安全删除资源时停止新增自动下载并告警。
          删除通过飞牛下载中心接口执行，保证做种状态一致。
        </div>
        <div className="grid grid-2" style={{ marginTop: 14 }}>
          <label className="field"><span>保留天数（完成后）</span>
            <input type="number" min="0" value={cfg.retain_days}
              onChange={e => setCfg({ ...cfg, retain_days: Number(e.target.value) })} /></label>
          <label className="field"><span>最短做种天数（义务保护）</span>
            <input type="number" min="0" step="0.5" value={cfg.min_seed_days}
              onChange={e => setCfg({ ...cfg, min_seed_days: Number(e.target.value) })} /></label>
          <label className="field"><span>单种分享率参考阈值</span>
            <input type="number" min="0" step="0.1" value={cfg.min_ratio}
              onChange={e => setCfg({ ...cfg, min_ratio: Number(e.target.value) })} /></label>
          <label className="field"><span>清理检查间隔（分钟）</span>
            <input type="number" min="10" value={cfg.interval_minutes}
              onChange={e => setCfg({ ...cfg, interval_minutes: Number(e.target.value) })} /></label>
          <label className="field"><span>自动目录空间配额（GB，0 不限）</span>
            <input type="number" min="0" value={cfg.quota_gb}
              onChange={e => setCfg({ ...cfg, quota_gb: Number(e.target.value) })} /></label>
          <label className="field"><span>NAS 最低剩余空间（GB，0 不限）</span>
            <input type="number" min="0" value={cfg.min_free_gb}
              onChange={e => setCfg({ ...cfg, min_free_gb: Number(e.target.value) })} /></label>
        </div>
        <div className="row" style={{ marginBottom: 10 }}>
          <Switch checked={!!cfg.min_idle_hours} onChange={v => setCfg({ ...cfg, min_idle_hours: v ? 24 : 0 })} />
          <span style={{ fontSize: 13 }}>仅清理上传不活跃的任务（停等 {cfg.min_idle_hours || 24} 小时）</span>
        </div>
        <div className="row">
          <button className="btn btn-primary" onClick={save}>保存设置</button>
          <button className="btn" disabled={previewing} onClick={runPreview}>
            {previewing ? '检查中…' : '清理候选预览（不实际删除）'}</button>
        </div>
        {preview && (
          <div style={{ marginTop: 14 }}>
            <b style={{ fontSize: 13 }}>候选预览（{preview.trigger || '未触发'}）</b>
            {preview.candidates && preview.candidates.length > 0 ? (
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table className="table">
                  <thead><tr><th>任务</th><th>大小</th><th>决定</th><th>原因</th></tr></thead>
                  <tbody>
                    {preview.candidates.map((x: any) => (
                      <tr key={x.id}>
                        <td style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          title={x.name}>{x.name}</td>
                        <td>{fmtSize(x.size)}</td>
                        <td>{x.decision === 'delete'
                          ? <Badge kind="danger">待清理</Badge>
                          : <Badge kind="gray">保留</Badge>}</td>
                        <td style={{ color: 'var(--text-3)' }}>{x.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 6 }}>当前没有自动下载任务需要评估</div>
            )}
          </div>
        )}
      </div>
    </>
  )
}

/* ================= 关于 ================= */
function AboutSection() {
  const [info, setInfo] = useState<any>(null)
  useEffect(() => { get('/api/settings/about').then(setInfo).catch(() => {}) }, [])
  if (!info) return <Spinner />
  return (
    <div className="card">
      <div className="card-title">关于馒头管家</div>
      <table className="table">
        <tbody>
          <tr><td style={{ width: 120, color: 'var(--text-3)' }}>应用名称</td><td><b>{info.app_name}</b>（{info.app_name_en}）</td></tr>
          <tr><td style={{ color: 'var(--text-3)' }}>作者</td><td>
            <a href={info.author_url} target="_blank" rel="noreferrer"
              style={{ color: 'var(--accent)', fontWeight: 600 }}>{info.author}</a></td></tr>
          <tr><td style={{ color: 'var(--text-3)' }}>官方网站</td><td>
            <a href={info.author_url} target="_blank" rel="noreferrer">{info.author_url}</a></td></tr>
          <tr><td style={{ color: 'var(--text-3)' }}>开源仓库</td><td>
            <a href={info.repo_url} target="_blank" rel="noreferrer">{info.repo_url}</a></td></tr>
          <tr><td style={{ color: 'var(--text-3)' }}>当前版本</td><td>v{info.version}</td></tr>
          <tr><td style={{ color: 'var(--text-3)' }}>开源许可证</td><td>{info.license}</td></tr>
        </tbody>
      </table>
      <div style={{ marginTop: 14 }}>
        <b style={{ fontSize: 13 }}>第三方项目致谢</b>
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 8, lineHeight: 1.9 }}>
          {info.third_party.map((t: any) => (
            <div key={t.name}>
              · <a href={t.url} target="_blank" rel="noreferrer">{t.name}</a>
              （{t.author}，{t.license}）— {t.usage}
            </div>
          ))}
          <div style={{ marginTop: 8, color: 'var(--text-3)' }}>
            本项目与 M-Team、飞牛 fnOS 官方无隶属或认证关系；M-Team 商标归其所有者。
          </div>
        </div>
      </div>
      <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border)', fontSize: 12.5, color: 'var(--text-3)' }}>
        © gswxy · <a href="https://gswxy.com" target="_blank" rel="noreferrer">gswxy.com</a> ·{' '}
        <a href="https://github.com/gswxy" target="_blank" rel="noreferrer">GitHub</a>
      </div>
    </div>
  )
}
