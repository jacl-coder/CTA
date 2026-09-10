import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Select, Table, Tabs, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import type { components } from '../../api/schema';
import type { Position, Trade } from '../../api/types';
import { useWorkspace } from '../../app/workspaceContext';
import { Amount, ModeGate, PageHeading, Panel } from '../../components/Shared';
import { money, offsetLabel, sideLabel, timeLabel } from '../../components/format';
import { usePolling } from '../../hooks/usePolling';

type Summary = components['schemas']['PositionAggregateResponse'];
const PAGE_SIZE = 20;
const positionColumns = [
  { title: '合约', dataIndex: 'instrument_id', width: 160 },
  { title: '品种', dataIndex: 'product_id', width: 140 },
  { title: '方向', dataIndex: 'side', render: sideLabel, width: 90 },
  { title: '总持仓（手）', dataIndex: 'quantity', align: 'right', width: 140 },
  { title: '今仓（手）', dataIndex: 'today_quantity', align: 'right', width: 140 },
  { title: '昨仓（手）', dataIndex: 'yesterday_quantity', align: 'right', width: 140 },
] satisfies TableColumnsType<Summary>;
const tradeColumns: TableColumnsType<Trade> = [
  { title: '交易日', dataIndex: 'trading_day', width: 120 },
  { title: '成交编号', dataIndex: 'fill_id', width: 190 },
  { title: '账户', dataIndex: 'account_id', width: 150 },
  { title: '合约', dataIndex: 'instrument_id', width: 150 },
  { title: '成交序号', dataIndex: 'sequence', width: 110 },
  { title: '方向', dataIndex: 'side', render: sideLabel, width: 90 },
  { title: '开平', dataIndex: 'offset', render: offsetLabel, width: 90 },
  { title: '数量（手）', dataIndex: 'quantity', align: 'right', width: 110 },
  { title: '成交价格', dataIndex: 'price', align: 'right', render: money, width: 160 },
  { title: '手续费', dataIndex: 'fee', align: 'right', render: value => <Amount value={value} />, width: 140 },
  { title: '来源', dataIndex: 'source', render: value => value ?? '—', width: 160 },
];

function useHistory<T>(path: string) {
  const resource = usePolling<T>(path, 1000);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, []);
  return { ...resource, fresh: resource.data !== undefined && !resource.error && now - resource.updatedAt < 3000 };
}

function HistoryStatus({ error, fresh, updatedAt, hasData }: {
  error?: string; fresh: boolean; updatedAt: number; hasData: boolean;
}) {
  return <>
    {error && <Alert type="error" showIcon message="查询失败" description={error} />}
    {hasData && <p className="muted" role="status"><Tag color={fresh ? 'green' : 'orange'}>
      {fresh ? '数据已更新' : '旧数据：刷新失败或已过期'}</Tag>上次成功更新：{timeLabel(updatedAt)}</p>}
  </>;
}

function SummaryTable({ product }: { product?: string }) {
  const params = new URLSearchParams();
  if (product !== undefined) params.set('product_id', product);
  const resource = useHistory<Summary[]>(`/positions/summary?${params}`);
  return <Panel title="跨账户汇总" extra={<Button onClick={resource.refresh} loading={resource.loading}>刷新汇总</Button>}>
    <p className="muted">汇总全部账户的同合约、同方向持仓；仅按品种筛选。多空持仓分别展示。</p>
    <HistoryStatus {...resource} hasData={resource.data !== undefined} />
    {(!resource.error || resource.data !== undefined) && <Table<Summary> columns={positionColumns}
      rowKey={row => JSON.stringify([row.instrument_id, row.side])} dataSource={resource.data}
      loading={resource.loading} pagination={false} scroll={{ x: 810 }}
      locale={{ emptyText: resource.loading ? '正在读取汇总…' : <Empty description="暂无汇总持仓" /> }} />}
  </Panel>;
}

function TradeHistory({ account, product }: { account?: string; product?: string }) {
  const [page, setPage] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String(page * PAGE_SIZE) });
  if (account !== undefined) params.set('account_id', account);
  if (product !== undefined) params.set('product_id', product);
  const resource = useHistory<Trade[]>(`/trades?${params}`);
  return <Panel title="成交记录" extra={<Button onClick={resource.refresh} loading={resource.loading}>刷新成交</Button>}>
    <HistoryStatus {...resource} hasData={resource.data !== undefined} />
    {(!resource.error || resource.data !== undefined) && <Table<Trade> columns={tradeColumns}
      rowKey={row => JSON.stringify([row.account_id, row.fill_id])} dataSource={resource.data?.slice(0, PAGE_SIZE)}
      loading={resource.loading} pagination={false} scroll={{ x: 1570 }}
      locale={{ emptyText: resource.loading ? '正在读取成交…' : <Empty description="当前筛选下暂无成交记录" /> }} />}
    <div className="toolbar" aria-label="成交分页">
      <Button disabled={page === 0 || resource.loading} onClick={() => setPage(value => value - 1)}>上一页</Button>
      <span>第 {page + 1} 页 · 每页 {PAGE_SIZE} 条</span>
      <Button disabled={!resource.fresh || (resource.data?.length ?? 0) <= PAGE_SIZE}
        onClick={() => setPage(value => value + 1)}>下一页</Button>
    </div>
  </Panel>;
}

function PositionsContent() {
  const { data, account, selectAccount } = useWorkspace();
  const [product, setProduct] = useState<string>();
  const [tab, setTab] = useState('positions');
  if (!data) return null;
  const products = [...new Set(data.instruments.map(item => item.product_id))].sort();
  const rows = data.positions.filter(row => (account === undefined || row.account_id === account)
    && (product === undefined || row.product_id === product));
  return <>
    <div className="toolbar">
      <Select aria-label="账户筛选" placeholder="全部账户" allowClear value={account} onChange={selectAccount}
        style={{ minWidth: 200 }} options={data.accounts.map(item => ({ label: item.account_id, value: item.account_id }))} />
      <Select aria-label="品种筛选" placeholder="全部品种" allowClear value={product} onChange={setProduct}
        style={{ minWidth: 180 }} options={products.map(value => ({ label: value, value }))} />
    </div>
    <Tabs activeKey={tab} onChange={setTab} destroyOnHidden items={[
      { key: 'positions', label: '当前持仓', children: tab === 'positions' && <Panel title="当前持仓">
        <Table<Position> columns={[{ title: '账户', dataIndex: 'account_id', width: 160 }, ...positionColumns]}
          rowKey={row => JSON.stringify([row.account_id, row.instrument_id, row.side])}
          dataSource={rows} pagination={false} scroll={{ x: 970 }} locale={{ emptyText: <Empty description="当前筛选下暂无持仓" /> }} />
      </Panel> },
      { key: 'summary', label: '跨账户汇总', children: tab === 'summary' && <SummaryTable key={product ?? ''} product={product} /> },
      { key: 'trades', label: '成交记录', children: tab === 'trades'
        && <TradeHistory key={JSON.stringify([account, product])} account={account} product={product} /> },
    ]} />
  </>;
}

export function PositionsPage() {
  const { data, error, loading, fresh, updatedAt, refresh } = useWorkspace();
  return <>
    <PageHeading title="持仓与成交" description="查看账户持仓、跨账户汇总和真实成交记录。"
      extra={<Button onClick={refresh} loading={loading}>刷新快照</Button>} />
    {data && (!fresh || error) && <Alert type={error ? 'error' : 'warning'} showIcon message="工作台快照已过期，正在展示旧数据"
      description={`${error ?? '等待最新快照'}；上次成功更新：${updatedAt ? timeLabel(updatedAt) : '—'}`} />}
    <ModeGate capability="ledger"><PositionsContent key={JSON.stringify([data?.instance_id, data?.run_id])} /></ModeGate>
  </>;
}
