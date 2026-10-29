import { useCallback, useEffect, useState } from 'react'
import { get, patch, post, put, fmtSize, fmtRate } from '../api'
import { Badge, Empty, Modal, Spinner, useInterval, useToast, statCard } from '../ui'

interface Account {
  id: number; name: string; api_url: string; api_key_masked: string; uid?: string
  username?: string; is_default: number; enabled: number
  uploaded: number; downloaded: number; share_rate: number; bonus: number
  seeding_count: number; seeding_size: number; last_sync?: string; last_error?: string
}

export default function AccountPage() {
  const toast = useToast()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [addOpen, setAddOpen] = useState(false)
  const [form, setForm] = useState({ name: '', api_key: '', api_url: 'https://api.m-team.cc' })
  const [protection, setProtection] = useState(2.0)
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    try {
      const d = await get('/api/accounts')
      setAccounts(d.items || [])
      const pd = await get('/api/accounts/protection').catch(() => null)
      if (pd && pd.min_share_rate) setProtection(pd.min_share_rate)
    } catch (e: any) {
      if (e.status !== 401) toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function addAccount() {
    setSubmitting(true)
    try {
      await post('/api/accounts', form)
      toast('账号添加成功，已通过 API 验证', 'ok')
      setAddOpen(false)
      setForm({ name: '', api_key: '', api_url: 'https://api.m-team.cc' })
      load()
    } catch (e: any) {
      toast(e.message, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  async function setDefault(id: number) {
    await patch(`/api/accounts/${id}`, { is_default: true })
    toast('已设为默认账号', 'ok')
    load()
  }

  async function sync(id: number) {
    try {
      const r = await post(`/api/accounts/${id}/sync`)
      toast(r.ok ? `同步成功：${r.username}` : `同步失败：${r.error}`, r.ok ? 'ok' : 'error')
      load()
    } catch (e: any) {
      toast(e.message, 'error')
    }
  }

  async function del(id: number, name: string) {
    if (!window.confirm(`确定删除账号「${name}」？已下载任务记录保留。`)) return
    await fetch(`/api/accounts/${id}`, { method: 'DELETE' })
    toast('已删除', 'ok')
    load()
  }

  async function saveProtection() {
    await put('/api/accounts/protection', { min_share_rate: protection })
    toast('分享率保护目标已保存', 'ok')
  }

  const def = accounts.find(a => a.is_default) || accounts[0]

  return (
    <div className="page">
      <h1 className="page-title">账号中心</h1>
      <div className="page-sub">数据来自 M-Team 官方 API 实时同步 · 每 10 分钟自动刷新</div>

      {def && (
        <div className="grid grid-stats" style={{ marginBottom: 16 }}>
          {statCard('当前账号', def.username || def.name, def.is_default ? '默认账号' : '')}
          {statCard('分享率', fmtRate(def.share_rate), '真实站点数据')}
          {statCard('上传量', fmtSize(def.uploaded))}
          {statCard('下载量', fmtSize(def.downloaded))}
          {statCard('魔力值', def.bonus ? Math.round(def.bonus).toLocaleString() : '-')}
          {statCard('做种', `${def.seeding_count} 个`, fmtSize(def.seeding_size))}
        </div>
      )}

      <div className="card">
        <div className="card-title">
          我的账号
          <span className="spacer" />
          <button className="btn btn-primary btn-sm" onClick={() => setAddOpen(true)}>+ 添加账号</button>
        </div>
        {loading ? <Spinner /> : accounts.length === 0 ? (
          <Empty text="尚未添加 M-Team 账号，点击右上角添加（需要馒头站 API 存取令牌）" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>账号</th><th>站点用户</th><th>API Key</th><th>分享率</th><th>上传 / 下载</th>
                  <th>做种</th><th>状态</th><th>操作</th></tr>
              </thead>
              <tbody>
                {accounts.map(a => (
                  <tr key={a.id}>
                    <td>
                      <b>{a.name}</b>
                      {a.is_default ? <span className="badge badge-ok" style={{ marginLeft: 6 }}>默认</span> : null}
                    </td>
                    <td>{a.username || '-'}</td>
                    <td className="mono" style={{ color: 'var(--text-3)' }}>{a.api_key_masked}</td>
                    <td><b style={{ color: a.share_rate >= 2 ? 'var(--ok)' : a.share_rate >= 1 ? 'var(--warn)' : 'var(--danger)' }}>
                      {fmtRate(a.share_rate)}</b></td>
                    <td style={{ fontSize: 12 }}>{fmtSize(a.uploaded)} / {fmtSize(a.downloaded)}</td>
                    <td style={{ fontSize: 12 }}>{a.seeding_count} 个 · {fmtSize(a.seeding_size)}</td>
                    <td>
                      {a.last_error ? <Badge kind="danger">连接异常</Badge> :
                        a.enabled ? <Badge kind="ok">正常</Badge> : <Badge kind="gray">已禁用</Badge>}
                      {a.last_error && <div style={{ fontSize: 11, color: 'var(--text-3)', maxWidth: 160 }}>{a.last_error}</div>}
                    </td>
                    <td>
                      <div className="row">
                        <button className="btn btn-sm" onClick={() => sync(a.id)}>同步</button>
                        {!a.is_default && <button className="btn btn-sm" onClick={() => setDefault(a.id)}>设默认</button>}
                        <button className="btn btn-sm btn-danger" onClick={() => del(a.id, a.name)}>删除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">分享率保护</div>
        <div className="row">
          <label className="field" style={{ marginBottom: 0, flex: 1 }}>
            <span>目标分享率下限（自动下载与风险提示参考）</span>
            <div className="row">
              <input type="number" step="0.1" min="0" style={{ width: 120 }} value={protection}
                onChange={e => setProtection(Number(e.target.value))} />
              <button className="btn btn-primary btn-sm" onClick={saveProtection}>保存</button>
            </div>
          </label>
        </div>
        <div className="help">
          非免费资源下载前，系统会预测计量下载量与下载后的分享率，低于目标将给出风险警告；
          高风险（预测 &lt; 1.0）的自动下载会被直接阻止。真实分享率取决于实际做种贡献。
        </div>
      </div>

      <div className="card" style={{ background: 'var(--bg-soft)', border: 'none' }}>
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.8 }}>
          <b>使用原则</b>：本应用不伪造上传数据、不篡改 Tracker 统计、不伪装客户端行为；
          免费资源优先下载，长期正常做种；自动下载不能保证提高分享率，请以真实贡献与风险控制为先。
          如账号存在站方警告（H&R / leech 警告等），会在状态列提示。
        </div>
      </div>

      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="添加 M-Team 账号"
        footer={
          <>
            <button className="btn" onClick={() => setAddOpen(false)}>取消</button>
            <button className="btn btn-primary" disabled={submitting || !form.name || !form.api_key}
              onClick={addAccount}>{submitting ? '验证中…' : '添加并验证'}</button>
          </>
        }>
        <label className="field"><span>备注名称</span>
          <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
            placeholder="例如：主账号" /></label>
        <label className="field"><span>API 地址</span>
          <select value={form.api_url} onChange={e => setForm({ ...form, api_url: e.target.value })}>
            <option value="https://api.m-team.cc">api.m-team.cc（默认）</option>
            <option value="https://kp.m-team.cc">kp.m-team.cc</option>
            <option value="https://xp.m-team.cc">xp.m-team.cc</option>
          </select>
          <div className="help">与你在馒头站使用的站点域名对应</div></label>
        <label className="field"><span>API Key（存取令牌）</span>
          <input value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })}
            placeholder="馒头站 → 控制面板 → 实验室 → 存取令牌" />
          <div className="help">仅保存在 NAS 本地并加密存储，不会出现在日志或界面中</div></label>
      </Modal>
    </div>
  )
}
