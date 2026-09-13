import { Alert, Switch, Table, Tag } from 'antd';
import { useState, type Key } from 'react';
import { Link } from 'react-router';
import { useWorkspace } from '../../app/workspaceContext';
import { Amount, ContractLabel, MetricCard, ModeGate, PageHeading, Panel, RiskTags } from '../../components/Shared';
import { phaseLabel, reasonLabel } from '../../components/format';
import { ratioToPercent } from '../../components/percent';
import { Icon } from '../../components/Icon';

export function OverviewPage() {
  const { data, account, selectAccount, fresh } = useWorkspace();
  const [selected, setSelected] = useState<Key[]>([]);
  const [comparing, setComparing] = useState(false);
  const rows = (data?.accounts ?? []).filter(item => !account || item.account_id === account)
    .filter(item => !comparing || selected.includes(item.account_id)).map(item => ({ ...item,
      risk: data?.risks.find(risk => risk.account_id === item.account_id),
      comparison: data?.risk_comparison?.find(metric => metric.account_id === item.account_id),
      positions: data?.positions.filter(position => position.account_id === item.account_id).length ?? 0,
    }));
  const broken = data?.risks.filter(item => item.circuit_broken).length ?? 0;
  const warning = data?.risks.filter(item => item.warning).length ?? 0;
  const restricted = data?.risks.filter(item => item.restricted_products.length > 0).length ?? 0;
  const hint = (value?: string | null) => data?.market?.market_ready && fresh ? '按当前完整行情估值' : value == null ? '等待有效报价' : '历史行情估值';
  return <>
    <PageHeading title="账户与风险总览" description="先看资金与风险，再决定下一笔交易。这里汇总所有策略账户。" extra={<Link className="primary-link" to="/trading"><Icon name="trade" size={17} />模拟交易<Icon name="arrow" size={16} /></Link>} />
    <ModeGate capability="ledger">
      <div className="kpi-grid">
        <MetricCard label="全账户权益" value={data?.totals.equity} hint={hint(data?.totals.equity)} icon="wallet" emphasis />
        <MetricCard label="持仓浮动盈亏" value={data?.totals.floating_pnl} hint={hint(data?.totals.floating_pnl)} icon="market" />
        <MetricCard label="保证金占用" value={data?.totals.margin} hint={hint(data?.totals.margin)} icon="shield" />
        <MetricCard label="可用资金" value={data?.totals.available_funds} hint={hint(data?.totals.available_funds)} icon="positions" />
      </div>
      <div className="overview-strip"><span><span className={`status-dot ${fresh ? 'live' : ''}`} />{fresh ? '账户监控中' : '展示上次账户状态'}</span><span><b>{data?.accounts.length ?? 0}</b> 个账户</span><span><b>{data?.instruments.length ?? 0}</b> 个合约</span><span>模拟交易日 <b className="date-value">{data?.accounts[0]?.trading_day ?? '—'}</b></span><Link to="/reports">{phaseLabel(data?.settlement?.phase)}<Icon name="arrow" size={14} /></Link></div>
      <Panel title="账户对比" extra={<div className="toolbar"><span className="muted">已选 {selected.length} 个</span><Switch checked={comparing} onChange={setComparing} disabled={!selected.length && !comparing} checkedChildren="仅所选" unCheckedChildren="全部" aria-label="仅比较所选账户" /></div>}>
        {!data?.capabilities.trading && <Alert showIcon type="info" message="账本查询模式" description="已加载持仓与成交；浮动盈亏和风险状态等待启用行情与交易。" className="inline-alert" />}
        <Table size="middle" pagination={false} rowKey="account_id" dataSource={rows} scroll={{ x: 1150 }} rowSelection={{ selectedRowKeys: selected, onChange: setSelected, preserveSelectedRowKeys: false }} columns={[
          { title: '策略账户', dataIndex: 'account_id', fixed: 'left', width: 130, render: (value: string) => <span className="account-name"><span className="account-avatar" aria-hidden="true">{value.slice(0, 2)}</span><strong>账户 {value}</strong></span> },
          { title: '当前权益', align: 'right', render: (_, row) => <Amount value={row.risk?.equity} /> },
          { title: '浮动盈亏', align: 'right', render: (_, row) => <Amount value={row.risk?.floating_pnl} /> },
          { title: '可用资金', align: 'right', render: (_, row) => <Amount value={row.risk?.available_funds} /> },
          { title: '浮亏 / 熔断阈值', width: 180, render: (_, row) => row.comparison ? <div className="risk-meter-cell"><span>{row.comparison.loss_percent ?? '—'}% / {row.comparison.breaker_percent}%</span><div className="risk-meter" aria-hidden="true"><i style={{ width: `${Math.min(100, Number(row.comparison.loss_percent ?? 0) / Number(row.comparison.breaker_percent) * 100)}%` }} /></div></div> : '—' },
          { title: '保证金 / 权益', width: 135, render: (_, row) => row.comparison?.margin_percent != null ? `${row.comparison.margin_percent}%` : '—' },
          { title: '风险状态', width: 150, render: (_, row) => !fresh ? <Tag>待重新确认</Tag> : row.risk ? <RiskTags broken={row.risk.circuit_broken} warning={row.risk.warning} restricted={row.risk.restricted_products} /> : <Tag>待评估</Tag> },
          { title: '开仓状态', width: 125, render: (_, row) => <span className={`table-status ${fresh && data?.system.trading_available && row.risk?.opening_allowed ? 'allowed' : ''}`}>{fresh && data?.system.trading_available && row.risk?.opening_allowed ? '可申请开仓' : '暂不可开仓'}</span> },
          { title: '', width: 66, render: (_, row) => <Link to="/positions" onClick={() => selectAccount(row.account_id)}>持仓</Link> },
        ]} expandable={{ expandedRowRender: row => <div className="account-detail"><span>期初余额 <Amount value={row.opening_balance} /></span><span>已实现盈亏 <Amount value={row.realized_pnl} /></span><span>手续费 <Amount value={row.fees} /></span><span>保证金 <Amount value={row.risk?.margin} /></span><span>总敞口 <Amount value={row.risk?.gross_exposure} /></span><span>{row.positions} 个持仓方向</span><span>浮亏熔断金额 <Amount value={row.comparison?.loss_limit} /></span>{row.comparison?.exposures.map(item => <span key={item.product_id}>{item.product_id} 敞口 <Amount value={item.gross_exposure} /> / 限额 <Amount value={item.limit} />（{item.usage_percent}%）</span>)}<p>{row.risk?.blocking_reasons.map(reasonLabel).join('；') || '无账户限制；每笔订单仍需检查预计敞口和保证金。'}</p></div> }} />
        <p className="table-note">上方资金指标始终汇总全部账户。勾选账户进行对比，展开行查看费用、敞口额度与限制原因。浮亏比例以各账户初始资金为分母；保证金比例以当前权益为分母，权益非正时不显示比例。</p>
      </Panel>
      {data?.policy && <details className="active-policy"><summary>当前生效规则与合约参数</summary><p>浮亏告警 {ratioToPercent(data.policy.warning_ratio)}%，熔断 {ratioToPercent(data.policy.loss_ratio)}%，默认品种敞口限额 <Amount value={data.policy.default_product_limit} />。参数来自配置文件；已有运行冻结规则，修改配置需使用新的运行目录。<Link to="/replay">先用历史回放对照规则</Link></p><Table size="small" rowKey="instrument_id" pagination={false} scroll={{ x: 650 }} dataSource={data.instruments} columns={[
        { title: '合约', dataIndex: 'instrument_id' }, { title: '乘数', dataIndex: 'multiplier' }, { title: '最小价位', dataIndex: 'tick_size' }, { title: '保证金率', dataIndex: 'margin_rate' }, { title: '每手手续费', dataIndex: 'fee_per_lot', render: (value: string) => <Amount value={value} /> },
      ]} /></details>}
      <div className="two-columns overview-bottom">
        <Panel title="风险关注" extra={<Link className="text-link" to="/events">查看事件<Icon name="arrow" size={15} /></Link>}>
          <div className="risk-counts"><div><span className="risk-symbol danger"><Icon name="risk" /></span><strong>{fresh ? broken : '—'}</strong><span>熔断账户</span></div><div><span className="risk-symbol warning"><Icon name="market" /></span><strong>{fresh ? warning : '—'}</strong><span>浮亏告警</span></div><div><span className="risk-symbol neutral"><Icon name="positions" /></span><strong>{fresh ? restricted : '—'}</strong><span>敞口受限</span></div></div>
          <div className="soft-note"><Icon name="shield" size={17} /><span>{!fresh ? '连接恢复后将重新核对账户风险。' : broken ? '有账户已熔断，请先查看限制原因，再决定是否平仓。' : '当前没有熔断账户。每笔新订单仍需通过敞口与保证金检查。'}</span></div>
        </Panel>
        <Panel title="关注行情" extra={<Link className="text-link" to="/market">全部行情<Icon name="arrow" size={15} /></Link>}>
          <div className="watch-list">{data?.instruments.map(item => <div key={item.instrument_id}><ContractLabel instrument={item.instrument_id} product={item.product_id} /><Amount value={data.market?.prices[item.instrument_id]} /><span className="muted">{fresh && data.market?.market_ready ? '最新价' : '参考快照'}</span></div>)}</div>
          <p className="table-note">{data?.sources ? `${data.sources.sources.filter(item => item.connected).length} / ${data.sources.sources.length} 个行情源${fresh ? '已连接' : '上次连接'}，缺失数据补齐后才允许交易。` : data?.capabilities.manual_market ? '当前为手工报价模式，在模拟交易页更新价格。' : '等待行情接入。'}</p>
        </Panel>
      </div>
    </ModeGate>
  </>;
}
