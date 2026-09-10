import { Alert, Button, Empty, Progress, Table, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import type { components } from '../../api/schema';
import { useWorkspace } from '../../app/workspaceContext';
import { ModeGate, PageHeading, Panel } from '../../components/Shared';
import { money, timeLabel } from '../../components/format';

type Source = components['schemas']['SourceResponse'];
type PriceRow = { instrument_id: string; product_id?: string; price?: string };
const priceColumns: TableColumnsType<PriceRow> = [
  { title: '合约', dataIndex: 'instrument_id', width: 200 },
  { title: '品种', dataIndex: 'product_id', render: value => value ?? '—', width: 180 },
  { title: '价格', dataIndex: 'price', render: money, align: 'right', width: 240 },
];

function MarketContent() {
  const { data, fresh, error } = useWorkspace();
  if (!data) return null;
  const { market, sources, capabilities } = data;
  const current = fresh && !error;
  const ready = current && !!market?.market_ready && (!capabilities.multi_source || !!sources?.market_ready)
    && !sources?.completed;
  const instruments = new Map(data.instruments.map(item => [item.instrument_id, item]));
  const rows: PriceRow[] = [...new Set([...instruments.keys(), ...Object.keys(market?.prices ?? {})])].sort()
    .map(instrument_id => ({ instrument_id, product_id: instruments.get(instrument_id)?.product_id,
      price: market?.prices[instrument_id] }));
  // Progress is based exclusively on integer frame sequences, never financial amounts.
  const progress = sources && Number.isSafeInteger(sources.applied_sequence) && Number.isSafeInteger(sources.expected_sequence)
    && sources.expected_sequence > 0 ? Math.max(0, Math.min(100, Math.floor(sources.applied_sequence / sources.expected_sequence * 100))) : 0;
  const sourceColumns: TableColumnsType<Source> = [
    { title: '行情源', dataIndex: 'source_id', width: 150 },
    { title: '连接状态', dataIndex: 'connected', width: 160, render: (connected: boolean) => <Tag
      color={current ? connected ? 'green' : 'red' : 'default'}>
      {current ? '' : '上次'}{connected ? '已连接' : '未连接'}</Tag> },
    { title: '源头序号（head）', dataIndex: 'head', width: 160 },
    { title: '连续序号（contiguous）', dataIndex: 'contiguous_sequence', width: 190 },
    { title: '已接收帧数', dataIndex: 'received_count', width: 130 },
    { title: '连接说明', dataIndex: 'reason', render: value => value || '—', width: 280 },
  ];
  return <>
    {capabilities.manual_market && <Alert type="info" showIcon message="手工价格模式"
      description="这里展示最近一次手工发布的价格快照。价格输入与发布位于交易页，本页只读。" />}
    {!ready && <Alert type="warning" showIcon
      message={!current ? '行情快照已过期' : sources?.completed ? '行情场景已完成' : '行情尚未就绪'}
      description={!current ? '以下为上次成功读取的快照，连接状态和价格不代表当前状态。'
        : sources?.reason || '当前价格仅作为历史快照展示，不能据此判断可交易。'} />}
    <Panel title="完整价格表" extra={<Tag color={ready ? 'green' : 'default'}>
      {ready ? capabilities.manual_market ? '手工价格已就绪' : '行情已就绪' : '历史快照 / 不可交易'}</Tag>}>
      <div className="kpi-grid">
        <div className="metric"><span className="muted">交易日</span><strong>{market?.trading_day ?? '—'}</strong></div>
        <div className="metric"><span className="muted">价格帧序号</span><strong>{market?.sequence ?? '—'}</strong></div>
        <div className="metric"><span className="muted">价格来源</span><strong>{capabilities.manual_market ? '手工发布' : market?.source ?? '—'}</strong></div>
      </div>
      <Table<PriceRow> rowKey="instrument_id" columns={priceColumns} dataSource={rows} pagination={false}
        scroll={{ x: 720 }} locale={{ emptyText: <Empty description="暂无合约与价格快照" /> }} />
    </Panel>
    {capabilities.multi_source && <Panel title="行情源与应用进度">
      {sources ? <>
        <div className="kpi-grid">
          <div className="metric"><span className="muted">全局已应用序号</span><strong>{sources.applied_sequence}</strong></div>
          <div className="metric"><span className="muted">当前预计序号</span><strong>{sources.expected_sequence}</strong></div>
          <div className="metric"><span className="muted">重复帧数</span><strong>{sources.duplicate_frames}</strong></div>
          <div className="metric"><span className="muted">场景完成状态{!current && '（上次）'}</span>
            <Tag color={current && sources.completed ? 'blue' : 'default'}>{sources.completed ? '已完成' : '未完成'}</Tag></div>
        </div>
        <p className="muted">应用进度：{sources.applied_sequence} / {sources.expected_sequence}（截至当前预计序号，不代表场景总进度）</p>
        <Progress percent={progress} status={current && sources.completed ? 'success' : 'normal'}
          format={() => sources.expected_sequence > 0 ? `${progress}%` : '—'} />
        <p className="muted">行情说明：{sources.reason || '—'}</p>
        <Table<Source> rowKey="source_id" columns={sourceColumns} dataSource={sources.sources} pagination={false}
          scroll={{ x: 1070 }} locale={{ emptyText: <Empty description="暂无行情源状态" /> }} />
      </> : <Alert type="warning" showIcon message="行情源状态暂不可用" description="等待完整工作台快照。" />}
    </Panel>}
  </>;
}

export function MarketPage() {
  const { data, error, loading, fresh, updatedAt, refresh } = useWorkspace();
  return <>
    <PageHeading title="行情监控" description="只读查看完整价格、行情源连接与接收和应用进度。"
      extra={<Button onClick={refresh} loading={loading}>刷新行情</Button>} />
    {data && (!fresh || error) && <Alert type={error ? 'error' : 'warning'} showIcon message="工作台快照已过期，正在展示旧数据"
      description={`${error ?? '等待最新快照'}；上次成功更新：${updatedAt ? timeLabel(updatedAt) : '—'}`} />}
    {data && <p className="muted">上次成功更新：{updatedAt ? timeLabel(updatedAt) : '—'}</p>}
    <ModeGate capability="trading"><MarketContent /></ModeGate>
  </>;
}
