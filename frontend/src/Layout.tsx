import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useTheme } from './ui'

const icon = (d: string) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: d }} />
)

const NAV = [
  { to: '/discover', label: '影视发现',
    d: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M7 20h10M12 18v2M8 9l2.5 2L8 13m4 0h4"/>' },
  { to: '/downloads', label: '下载管理',
    d: '<path d="M12 4v11m0 0 4-4m-4 4-4-4M4 19h16"/>' },
  { to: '/account', label: '账号中心',
    d: '<circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5"/>' },
  { to: '/settings', label: '设置',
    d: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.06-.4.1-.8.1-1.2Z"/>' },
]

export default function Layout() {
  const { theme, toggle } = useTheme()
  const loc = useLocation()
  const navigate = useNavigate()
  const current = NAV.find(n => loc.pathname.startsWith(n.to))?.label || '影视发现'

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src="/favicon.svg" alt="logo" />
          <div>
            <div className="brand-name">馒头管家</div>
            <div className="brand-sub">M-Team Manager</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              {icon(n.d)}{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button className="btn btn-sm btn-ghost" onClick={toggle}
            style={{ marginBottom: 8, color: 'var(--text-3)' }}>
            {theme === 'light' ? '🌙 深色模式' : '☀️ 浅色模式'}
          </button>
          <div>© gswxy · <a href="https://gswxy.com" target="_blank" rel="noreferrer">gswxy.com</a></div>
          <div><a href="https://github.com/gswxy/mteam-manager-fnos" target="_blank" rel="noreferrer">GitHub</a></div>
        </div>
      </aside>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div className="topbar">
          <img src="/favicon.svg" style={{ width: 26, height: 26 }} alt="" />
          <select value={loc.pathname} onChange={e => navigate(e.target.value)}>
            {NAV.map(n => <option key={n.to} value={n.to}>{n.label}</option>)}
          </select>
          <button className="btn btn-sm btn-ghost" onClick={toggle}>{theme === 'light' ? '🌙' : '☀️'}</button>
        </div>
        <main className="main" data-page={current}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
