import { Button, Select, Tag, Alert } from 'antd';
import { Link, NavLink, Route, Routes } from 'react-router';
import { OverviewPage } from '../features/overview/OverviewPage';
import { WorkspaceProvider } from './WorkspaceProvider';
import { useWorkspace } from './workspaceContext';
import { timeLabel } from '../components/format';
import { PositionsPage } from '../features/positions/PositionsPage';
import { MarketPage } from '../features/market/MarketPage';
import { EventsPage } from '../features/events/EventsPage';
import { TradingPage } from '../features/trading/TradingPage';
import { ReportsPage } from '../features/reports/ReportsPage';

export function App() {
  return <WorkspaceProvider><WorkspaceLayout /></WorkspaceProvider>;
}
function WorkspaceLayout() {
  const { data, fresh, error, loading, updatedAt, refresh, account, selectAccount } = useWorkspace();
  return <div className="workspace">
    <header className="topbar">
      <Link to="/" className="brand"><strong>CTA</strong><span>期货风控工作台</span></Link>
      <div className="topbar-status"><span className={`status-dot ${fresh ? 'live' : ''}`} /><span>{fresh ? '服务已连接' : loading ? '正在连接' : '连接中断'}</span><span className="environment">模拟交易 · 本地环境</span></div>
    </header>
    <div className="navigation"><nav aria-label="工作台导航">
      {[['/', '账户总览'], ['/positions', '持仓与成交'], ['/market', '行情监控'], ['/events', '风险事件'], ['/trading', '模拟交易'], ['/reports', '日结与日报']].map(([path, label]) => <NavLink key={path} to={path} end={path === '/'}>{label}</NavLink>)}
    </nav><div className="run-label">{data?.run_id ?? '等待运行'}</div></div>
    <main className="content">
      <div className="workspace-toolbar"><div className="toolbar"><label htmlFor="account-filter">策略账户</label>
        <Select id="account-filter" aria-label="策略账户筛选" value={account ?? ''} style={{ minWidth: 155 }} onChange={value => selectAccount(value || undefined)} options={[{ value: '', label: '全部账户' }, ...(data?.accounts ?? []).map(item => ({ value: item.account_id, label: `账户 ${item.account_id}` }))]} />
        <Tag color={fresh && data?.system.trading_available ? 'green' : 'default'}>{fresh && data?.system.trading_available ? '行情有效' : '交易暂停'}</Tag>
      </div><div className="toolbar muted"><span>最近同步 {timeLabel(updatedAt || null)}</span><Button onClick={refresh} loading={loading}>刷新数据</Button></div></div>
      {!fresh && !loading && <Alert className="connection-alert" showIcon type="error" message="实时连接不可用，操作已暂停" description={`${error ?? '数据更新超时'}。${data ? '以下保留上次成功读取的数据，不能作为当前行情。' : '请确认后端已启动。'}连接恢复后自动刷新。`} />}
      {fresh && data && !data.system.trading_available && <Alert className="connection-alert" showIcon type="info" message="当前不能交易" description={data.system.reasons.join(' ')} />}
      <div key={data?.run_id ?? 'loading'}><Routes>
        <Route path="/" element={<OverviewPage />} /><Route path="/positions" element={<PositionsPage />} />
        <Route path="/market" element={<MarketPage />} /><Route path="/events" element={<EventsPage />} />
        <Route path="/trading" element={<TradingPage />} /><Route path="/reports" element={<ReportsPage />} />
        <Route path="*" element={<><h1>页面不存在</h1><Link to="/">返回工作台</Link></>} />
      </Routes></div>
      <footer className="workspace-footer"><span>CTA · 多品种期货风控与清算</span><span>服务版本 {data?.system.version ?? '—'} · 金额单位：元</span></footer>
    </main>
  </div>;
}
