import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Select, Table, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import type { RiskEvent } from '../../api/types';
import { useWorkspace } from '../../app/workspaceContext';
import { ModeGate, PageGuide, PageHeading, Panel, RecordTime, RiskTags } from '../../components/Shared';
import { reasonLabel, timeLabel } from '../../components/format';
import { useWorkspacePolling } from '../../hooks/useWorkspacePolling';

const PAGE_SIZE = 20;
const eventColumns: TableColumnsType<RiskEvent> = [
  { title: '账户', dataIndex: 'account_id', width: 80 },
  { title: '事件', dataIndex: 'kind', width: 180, render: (kind: string) => <Tag
    color={kind.endsWith('_EXIT') ? 'green' : kind === 'CIRCUIT_BREAK' ? 'red' : 'orange'}>{reasonLabel(kind)}</Tag> },
  { title: '品种', dataIndex: 'product_id', render: value => value ?? '—', width: 130 },
  { title: '交易日', dataIndex: 'trading_day', render: value => value ?? '—', width: 120 },
  { title: '发生时间（北京时间）', dataIndex: 'occurred_ms', render: (value?: number | null) => <RecordTime value={value} />, width: 170 },
  { title: '发现时间（北京时间）', dataIndex: 'detected_ms', render: (value?: number | null) => <RecordTime value={value} />, width: 170 },
  { title: '是否补数发现', dataIndex: 'recovered', width: 140, render: (value?: boolean | null) =>
    value == null ? '—' : <Tag color={value ? 'gold' : 'default'}>{value ? '补数发现' : '正常发现'}</Tag> },
  { title: '源发生帧', dataIndex: 'frame_sequence', width: 110 },
  { title: '事件编号', dataIndex: 'event_id', width: 100 },
];

function EventHistory({ account }: { account?: string }) {
  const [page, setPage] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String(page * PAGE_SIZE) });
  if (account !== undefined) params.set('account_id', account);
  const resource = useWorkspacePolling<RiskEvent[]>(`/risk-events?${params}`, 1000);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, []);
  const fresh = resource.data !== undefined && !resource.error && now - resource.updatedAt < 3000;
  return <Panel title="风险事件记录">
    <p className="table-note">同一风险持续期间只记录一次；解除后再次触发会新增事件。补数发现的事件保留原始发生时间。</p>
    {resource.error && <Alert type="error" showIcon message="风险事件查询失败" description={resource.error} />}
    {resource.data !== undefined && <p className="muted" role="status"><Tag color={fresh ? 'green' : 'orange'}>
      {fresh ? '数据已更新' : '旧数据：刷新失败或已过期'}</Tag>上次成功更新：{timeLabel(resource.updatedAt)}</p>}
    {(!resource.error || resource.data !== undefined) && <Table<RiskEvent> columns={eventColumns} rowKey="event_id"
      dataSource={resource.data?.slice(0, PAGE_SIZE)} loading={resource.loading} pagination={false} scroll={{ x: 1300 }}
      locale={{ emptyText: resource.loading ? '正在读取风险事件…' : <Empty description="当前筛选下暂无风险事件" /> }} />}
    <div className="toolbar" aria-label="风险事件分页">
      <Button disabled={page === 0 || resource.loading} onClick={() => setPage(value => value - 1)}>上一页</Button>
      <span>第 {page + 1} 页 · 每页 {PAGE_SIZE} 条</span>
      <Button disabled={!fresh || (resource.data?.length ?? 0) <= PAGE_SIZE}
        onClick={() => setPage(value => value + 1)}>下一页</Button>
    </div>
  </Panel>;
}

export function EventsPage() {
  const { data, account, selectAccount, error, fresh, updatedAt } = useWorkspace();
  return <>
    <PageHeading title="风险事件" description="先处理当前限制，再查看风险发生和解除的完整记录。" />
    {data && (!fresh || error) && <Alert type={error ? 'error' : 'warning'} showIcon message="工作台快照已过期，正在展示旧数据"
      description={`${error ?? '等待最新快照'}；上次成功更新：${updatedAt ? timeLabel(updatedAt) : '—'}`} />}
    <ModeGate capability="trading">
      <PageGuide>告警提醒关注亏损；熔断禁止当日继续开仓；敞口限制约束对应品种。行情有效时，仍可提交合法平仓。</PageGuide>
      <div className="risk-account-grid">{(data?.risks ?? []).filter(item => !account || item.account_id === account).map(item => <div className={`risk-account-card ${fresh && (item.circuit_broken || item.warning || item.restricted_products.length) ? 'attention' : ''}`} key={item.account_id}><div className="account-name"><span className="account-avatar" aria-hidden="true">{item.account_id.slice(0, 2)}</span><strong>账户 {item.account_id}</strong></div>{fresh ? <RiskTags broken={item.circuit_broken} warning={item.warning} restricted={item.restricted_products} /> : <Tag>待重新确认</Tag>}<p>{!fresh ? '当前快照已过期，连接恢复后重新确认风险。' : item.blocking_reasons.map(reasonLabel).join('；') || '当前未触发账户限制，新订单仍需通过风控检查。'}</p></div>)}</div>
      <div className="filter-bar"><span>筛选事件</span><Select aria-label="账户筛选" placeholder="全部账户" allowClear value={account}
        onChange={selectAccount} style={{ minWidth: 200 }}
        options={data?.accounts.map(item => ({ label: item.account_id, value: item.account_id }))} /></div>
      <EventHistory key={JSON.stringify([data?.instance_id, data?.run_id, account])} account={account} />
    </ModeGate>
  </>;
}
