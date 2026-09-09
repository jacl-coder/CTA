import { Alert, Button, Tag } from 'antd';
import { useSystemStatus } from '../../hooks/useSystemStatus';

export function OverviewPage() {
  const { connection, refresh } = useSystemStatus();
  return (
    <>
      <div className="page-heading">
        <div><p className="eyebrow">交易监控</p><h1>账户与风险总览</h1>
          <p className="description">集中查看账户持仓、资金风险和每日清算结果。</p></div>
        <Button onClick={refresh} loading={connection.kind === 'loading'}>刷新状态</Button>
      </div>
      <section className="status-panel" aria-label="服务状态">
        <div className="panel-heading"><h2>服务状态</h2>
          <Tag color={connection.kind === 'connected' ? 'cyan' : 'default'}>
            {connection.kind === 'connected' ? '后端已连接' : connection.kind === 'loading' ? '连接中' : '连接失败'}
          </Tag>
        </div>
        {connection.kind === 'loading' && <p role="status">正在读取服务状态…</p>}
        {connection.kind === 'error' && <Alert type="error" showIcon message="无法连接服务" description={connection.message} />}
        {connection.kind === 'connected' && <>
          <Alert type={connection.data.trading_available ? 'success' : 'warning'} showIcon
            message={connection.data.trading_available ? '交易服务已就绪' : '交易服务尚未就绪'}
            description={connection.data.reasons.join(' ')} />
          <p className="version">服务版本 {connection.data.version}</p>
        </>}
      </section>
      <p className="footnote">接入行情与账户后，这里将展示实时风险状态。当前不提供交易操作。</p>
    </>
  );
}
