import { Alert, Empty, Progress, Table, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import { useWorkspace } from '../../app/workspaceContext';
import { Amount, ContractLabel, ModeGate, PageGuide, PageHeading, Panel } from '../../components/Shared';
import { Icon } from '../../components/Icon';
import { timeLabel } from '../../components/format';

type PriceRow = { instrument_id: string; product_id?: string; price?: string };
const priceColumns: TableColumnsType<PriceRow> = [
  { title: '交易合约', dataIndex: 'instrument_id', width: 260, render: (value, row) => <ContractLabel instrument={value} product={row.product_id} /> },
  { title: '品种代码', dataIndex: 'product_id', render: value => <span className="muted">{value ?? '—'}</span>, width: 170 },
  { title: '最新价格', dataIndex: 'price', render: (value?: string) => <Amount value={value} />, align: 'right', width: 250 },
];
function MarketContent() {
  const { data, fresh, error } = useWorkspace();
  if (!data) return null;
  const { market, sources, capabilities } = data;
  const current = fresh && !error;
  const ready = current && !!market?.market_ready && (!capabilities.multi_source || !!sources?.market_ready) && !sources?.completed;
  const instruments = new Map(data.instruments.map(item => [item.instrument_id, item]));
  const rows: PriceRow[] = [...new Set([...instruments.keys(), ...Object.keys(market?.prices ?? {})])].sort()
    .map(instrument_id => ({ instrument_id, product_id: instruments.get(instrument_id)?.product_id, price: market?.prices[instrument_id] }));
  const progress = sources && Number.isSafeInteger(sources.applied_sequence) && Number.isSafeInteger(sources.expected_sequence)
    && sources.expected_sequence > 0 ? Math.max(0, Math.min(100, Math.floor(sources.applied_sequence / sources.expected_sequence * 100))) : 0;
  return <>
    <PageGuide>行情价格用于计算持仓盈亏和模拟成交。任何一个行情源断开，系统会暂停交易，补齐数据后恢复。</PageGuide>
    {capabilities.manual_market && <Alert className="inline-alert" type="info" showIcon message="手工价格模式" description="这里展示最近一次手工发布的价格快照。价格输入与发布位于交易页，本页只读。" />}
    {!ready && <Alert className="inline-alert" type="warning" showIcon message={!current ? '行情快照已过期' : sources?.completed ? '行情场景已完成' : '行情尚未就绪'} description={!current ? '以下为上次成功读取的快照，连接状态和价格不代表当前状态。' : sources?.reason || '当前价格仅作为历史快照展示，不能据此判断可交易。'} />}
    <Panel title="合约报价" extra={<Tag color={ready ? 'green' : 'default'}>{ready ? capabilities.manual_market ? '手工价格已就绪' : '行情已就绪' : '历史快照 / 不可交易'}</Tag>}>
      <div className="market-meta"><span>模拟交易日 <b>{market?.trading_day ?? '—'}</b></span><span>数据来源 <b>{capabilities.manual_market ? '手工发布' : capabilities.multi_source ? '独立模拟行情源' : market?.source ?? '—'}</b></span><span>价格单位：元</span></div>
      <Table<PriceRow> className="market-price-table" rowKey="instrument_id" columns={priceColumns} dataSource={rows} pagination={false} scroll={{ x: 680 }} locale={{ emptyText: <Empty description="暂无合约与价格快照" /> }} />
    </Panel>
    {capabilities.multi_source && <Panel title="行情源与应用进度" extra={<span className="muted">连接状态 · 缺口补全</span>}>
      {sources ? <>
        <div className="feed-progress"><div className="progress-heading"><strong>{!current ? '等待恢复连接' : progress === 100 ? '已跟上当前行情' : '正在补齐历史行情'}</strong><span>{sources.applied_sequence} / {sources.expected_sequence}</span></div><Progress percent={progress} strokeColor="#438d77" trailColor="#e5ede9" size="small" format={() => sources.expected_sequence > 0 ? `${progress}%` : '—'} /><p>已处理进度相对于当前行情计算，不代表整个演示的完成比例。</p></div>
        <div className="source-grid">{sources.sources.map(source => <div className="source-card" key={source.source_id}><div className="source-heading"><Icon name="market" /><strong>{source.source_id}</strong><Tag color={current ? source.connected ? 'green' : 'red' : 'default'}>{current ? '' : '上次'}{source.connected ? '已连接' : '未连接'}</Tag></div><div className="facts"><div><span>已接收数据</span><strong>{source.received_count} 帧</strong></div><div><span>已连续补齐至</span><strong>第 {source.contiguous_sequence} 帧</strong></div><div><span>行情源最新位置</span><strong>第 {source.head} 帧</strong></div></div>{source.reason && <p className="blocking-note">{source.reason}</p>}</div>)}</div>
        <details className="details"><summary>查看行情处理明细</summary><div className="inline-facts facts"><div><span>完整价格帧序号</span><b>{market?.sequence ?? '—'}</b></div><div><span>已去重数据</span><b>{sources.duplicate_frames} 帧</b></div><div><span>固定场景结束标记{!current && '（上次）'}</span><Tag>{sources.completed ? '已完成' : '未完成'}</Tag></div><div><span>内部价格来源</span><b>{market?.source ?? '—'}</b></div></div><p className="muted">{sources.reason || '每个序号只处理一次，重复数据不会重复触发风控。'}</p></details>
      </> : <Alert type="warning" showIcon message="行情源状态暂不可用" description="等待完整工作台快照。" />}
    </Panel>}
  </>;
}
export function MarketPage() {
  const { data, error, fresh, updatedAt } = useWorkspace();
  return <><PageHeading title="行情监控" description="查看合约最新价格，确认数据是否完整、连接是否正常。" />
    {data && (!fresh || error) && <Alert className="inline-alert" type={error ? 'error' : 'warning'} showIcon message="工作台快照已过期，正在展示旧数据" description={`${error ?? '等待最新快照'}；上次成功更新：${updatedAt ? timeLabel(updatedAt) : '—'}`} />}
    <ModeGate capability="trading"><MarketContent /></ModeGate></>;
}
