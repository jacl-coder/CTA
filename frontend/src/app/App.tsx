import { Button, Select, Alert } from 'antd';
import { useEffect } from 'react';
import { Link, NavLink, Route, Routes, useLocation } from 'react-router';
import { OverviewPage } from '../features/overview/OverviewPage';
import { WorkspaceProvider } from './WorkspaceProvider';
import { useWorkspace } from './workspaceContext';
import { phaseLabel, timeLabel } from '../components/format';
import { Icon, type IconName } from '../components/Icon';
import { PositionsPage } from '../features/positions/PositionsPage';
import { MarketPage } from '../features/market/MarketPage';
import { EventsPage } from '../features/events/EventsPage';
import { TradingPage } from '../features/trading/TradingPage';
import { ReportsPage } from '../features/reports/ReportsPage';

const pages: { path: string; label: string; icon: IconName; group: string }[] = [
  { path: '/', label: '账户总览', icon: 'overview', group: '交易工作台' },
  { path: '/positions', label: '持仓与成交', icon: 'positions', group: '交易工作台' },
  { path: '/trading', label: '模拟交易', icon: 'trade', group: '交易工作台' },
  { path: '/market', label: '行情监控', icon: 'market', group: '风控与清算' },
  { path: '/events', label: '风险事件', icon: 'risk', group: '风控与清算' },
  { path: '/reports', label: '日结与日报', icon: 'report', group: '风控与清算' },
];
export function App() {
  return <WorkspaceProvider><WorkspaceLayout /></WorkspaceProvider>;
}
function WorkspaceLayout() {
  const { data, fresh, error, loading, updatedAt, refresh, account, selectAccount } = useWorkspace();
  const location = useLocation();
  useEffect(() => { window.scrollTo(0, 0); }, [location.pathname]);
  const active = pages.find(page => page.path === location.pathname);
  const ready = fresh && !!data?.system.trading_available;
  const day = data?.settlement?.trading_day ?? data?.accounts[0]?.trading_day;
  return <div className="workspace">
    <a className="skip-link" href="#main-content">跳转到页面内容</a>
    <aside className="sidebar">
      <Link to="/" className="brand"><span className="brand-mark"><Icon name="market" size={25} /></span><span><strong>CTA</strong><small>期货风控工作台</small></span></Link>
      <div className="sidebar-caption">多账户 · 多品种 · 统一风控</div>
      <nav aria-label="工作台导航">{['交易工作台', '风控与清算'].map(group => <div className="nav-group" key={group}><p>{group}</p>{pages.filter(page => page.group === group).map(page => <NavLink key={page.path} to={page.path} aria-label={page.label} title={page.label} end={page.path === '/'}><Icon name={page.icon} /><span>{page.label}</span><span className="nav-active-mark" /></NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="simulation-note"><Icon name="shield" /><strong>模拟交易环境</strong><p>使用模拟资金与行情<br />所有操作均为本地演示</p></div><div className="sidebar-version">CTA RISK <span>v{data?.system.version ?? '—'}</span></div></div>
    </aside>
    <div className="workspace-main">
      <header className="topbar"><div className="breadcrumb"><span>工作空间</span><span>/</span><strong>{active?.label ?? '页面不存在'}</strong></div>
        <div className="topbar-status"><span className={`status-dot ${fresh ? 'live' : ''}`} /><span>{fresh ? '实时连接' : loading ? '正在连接' : '连接中断'}</span><span className="environment">本地模拟</span></div>
      </header>
      <main id="main-content" className="content">
        <div className="workspace-toolbar"><div className="toolbar"><label htmlFor="account-filter">查看账户</label>
          <Select id="account-filter" aria-label="策略账户筛选" value={account ?? ''} style={{ minWidth: 145 }} onChange={value => selectAccount(value || undefined)} options={[{ value: '', label: '全部账户' }, ...(data?.accounts ?? []).map(item => ({ value: item.account_id, label: `账户 ${item.account_id}` }))]} />
          <span className="simulation-day">模拟交易日 <b>{day ?? '—'}</b></span>
          <span className={`status-pill ${ready ? 'success' : 'pending'}`}><span className="status-dot" />{ready ? '行情就绪' : '交易暂停'}</span>
        </div><div className="toolbar sync-toolbar"><span className="muted"><Icon name="clock" size={14} /> 页面更新 {timeLabel(updatedAt || null)}</span><Button aria-label="刷新数据" icon={<Icon name="refresh" size={15} />} onClick={refresh} loading={loading && !data}>刷新</Button></div></div>
        {!fresh && !loading && <Alert className="connection-alert" showIcon type="error" message="连接中断，交易操作已暂停" description={`${error ?? '数据更新超时'}。${data ? '当前保留上次数据，恢复连接后自动更新。' : '请确认后端已启动。'}`} />}
        {fresh && data && !data.system.trading_available && <Alert className="connection-alert" showIcon type="info" message="正在等待可交易状态" description={data.system.reasons.join(' ')} />}
        <div key={data?.run_id ?? 'loading'} className="page-content"><Routes>
          <Route path="/" element={<OverviewPage />} /><Route path="/positions" element={<PositionsPage />} />
          <Route path="/market" element={<MarketPage />} /><Route path="/events" element={<EventsPage />} />
          <Route path="/trading" element={<TradingPage />} /><Route path="/reports" element={<ReportsPage />} />
          <Route path="*" element={<><h1>页面不存在</h1><Link to="/">返回工作台</Link></>} />
        </Routes></div>
        <footer className="workspace-footer"><span>模拟交易日 {day ?? '—'} · {phaseLabel(data?.settlement?.phase)}</span><span>金额单位：人民币元 <details className="run-details"><summary>运行信息</summary><code>{data?.run_id ?? '等待运行'}</code></details></span></footer>
      </main>
    </div>
  </div>;
}
