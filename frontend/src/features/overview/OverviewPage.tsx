import { Alert, Switch, Table, Tag } from 'antd';
import { useState, type Key } from 'react';
import { Link } from 'react-router';
import { useWorkspace } from '../../app/workspaceContext';
import { Amount, ModeGate, PageHeading, Panel, RiskTags } from '../../components/Shared';
import { phaseLabel, reasonLabel } from '../../components/format';

export function OverviewPage() {
  const { data, account, selectAccount, fresh } = useWorkspace();
  const [selected, setSelected] = useState<Key[]>([]);
  const [comparing, setComparing] = useState(false);
  const rows = (data?.accounts ?? []).filter(item => !account || item.account_id === account)
    .filter(item => !comparing || selected.includes(item.account_id)).map(item => ({ ...item,
      risk: data?.risks.find(risk => risk.account_id === item.account_id),
      positions: data?.positions.filter(position => position.account_id === item.account_id).length ?? 0,
    }));
  return <>
    <PageHeading title="账户与风险总览" description="资金、持仓与限制状态，集中核对每个策略账户。" extra={<Tag>{data?.accounts[0]?.trading_day ?? '等待交易日'} · {phaseLabel(data?.settlement?.phase)}</Tag>} />
    <ModeGate capability="ledger">
      <div className="kpi-grid">
        {([['全账户权益', data?.totals.equity], ['持仓浮动盈亏', data?.totals.floating_pnl], ['保证金占用', data?.totals.margin], ['可用资金', data?.totals.available_funds]] as const).map(([label, value]) =>
          <div className="metric" key={label}><span>{label}</span><strong><Amount value={value} /></strong><small>{data?.market?.market_ready && fresh ? '按当前完整行情估值' : value == null ? '等待有效报价' : '历史行情估值'}</small></div>)}
      </div>
      <div className="overview-strip"><span><b>{data?.accounts.length ?? 0}</b> 个账户</span><span><b>{data?.instruments.length ?? 0}</b> 个合约</span><span><b className="negative">{data?.risks.filter(item => item.circuit_broken).length ?? 0}</b> 个熔断账户</span><span>完整行情帧 <b>{data?.market?.sequence ?? '—'}</b></span><Link to="/market">查看行情源 →</Link></div>
      <Panel title="账户对比" extra={<div className="toolbar"><span className="muted">已选 {selected.length} 个</span><Switch checked={comparing} onChange={setComparing} disabled={!selected.length && !comparing} checkedChildren="仅所选" unCheckedChildren="全部" aria-label="仅比较所选账户" /></div>}>
        {!data?.capabilities.trading && <Alert showIcon type="info" message="账本查询模式" description="已加载持仓与成交；浮动盈亏和风险状态等待启用行情与交易。" className="inline-alert" />}
        <Table size="middle" pagination={false} rowKey="account_id" dataSource={rows} scroll={{ x: 1200 }} rowSelection={{ selectedRowKeys: selected, onChange: setSelected, preserveSelectedRowKeys: false }} columns={[
          { title: '策略账户', dataIndex: 'account_id', fixed: 'left', width: 120, render: (value: string) => <strong>账户 {value}</strong> },
          { title: '期初余额', dataIndex: 'opening_balance', align: 'right', render: (value: string) => <Amount value={value} /> },
          { title: '当前权益', align: 'right', render: (_, row) => <Amount value={row.risk?.equity} /> },
          { title: '浮动盈亏', align: 'right', render: (_, row) => <Amount value={row.risk?.floating_pnl} /> },
          { title: '可用资金', align: 'right', render: (_, row) => <Amount value={row.risk?.available_funds} /> },
          { title: '品种总敞口', align: 'right', render: (_, row) => <Amount value={row.risk?.gross_exposure} /> },
          { title: '风险状态', width: 210, render: (_, row) => row.risk ? <RiskTags broken={row.risk.circuit_broken} warning={row.risk.warning} restricted={row.risk.restricted_products} /> : <Tag>待评估</Tag> },
          { title: '开仓状态', width: 130, render: (_, row) => <Tag color={fresh && row.risk?.opening_allowed ? 'green' : 'default'}>{fresh && row.risk?.opening_allowed ? '可申请开仓' : '暂不可开仓'}</Tag> },
          { title: '查看', width: 80, render: (_, row) => <Link to="/positions" onClick={() => selectAccount(row.account_id)}>持仓</Link> },
        ]} expandable={{ expandedRowRender: row => <div className="account-detail"><span>已实现盈亏 <Amount value={row.realized_pnl} /></span><span>手续费 <Amount value={row.fees} /></span><span>保证金 <Amount value={row.risk?.margin} /></span><span>{row.positions} 个持仓方向</span><p>{row.risk?.blocking_reasons.map(reasonLabel).join('；') || '无账户限制；每笔订单仍需检查预计敞口和保证金。'}</p></div> }} />
        <p className="table-note">上方指标为全部账户汇总。勾选账户可集中对比；展开行查看费用及限制原因。</p>
      </Panel>
      <div className="two-columns"><Panel title="行情接入"><p className="description">{data?.sources ? `${data.sources.sources.filter(item => item.connected).length} / ${data.sources.sources.length} 个源已连接，已按序处理至第 ${data.sources.applied_sequence} 帧。` : data?.capabilities.manual_market ? '当前使用手工模拟价格，可在交易页面提交完整报价。' : '尚未启用行情。'}</p><Link className="section-action" to="/market">查看行情状态 →</Link></Panel>
        <Panel title="收盘与清算"><p className="description">{data?.settlement ? `${phaseLabel(data.settlement.phase)} · 已生成 ${data.settlement.settled_days.length} 份日报` : '当前运行未启用日结计划。'}</p><Link className="section-action" to="/reports">查看日结与日报 →</Link></Panel></div>
    </ModeGate>
  </>;
}
