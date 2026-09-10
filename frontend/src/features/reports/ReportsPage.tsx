import { Alert, Button, Descriptions, Select, Steps, Table, Tag } from 'antd';
import { useRef, useState } from 'react';
import { useWorkspace } from '../../app/workspaceContext';
import { messageOf, writeApi } from '../../api/client';
import type { DailyReport } from '../../api/types';
import { Amount, ModeGate, PageGuide, PageHeading, Panel, RiskTags } from '../../components/Shared';
import { Icon } from '../../components/Icon';
import { phaseLabel, sideLabel } from '../../components/format';
import { useWorkspacePolling } from '../../hooks/useWorkspacePolling';

type Notice = { success: boolean; message: string };
export function ReportsPage() {
  const { data, account, fresh, updatedAt, refresh } = useWorkspace();
  const [selected, setSelected] = useState<string>();
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [notice, setNotice] = useState<Notice>();
  const days = data?.settlement?.settled_days ?? [];
  const selectedDay = selected && days.includes(selected) ? selected : days.at(-1);
  const report = useWorkspacePolling<DailyReport>(selectedDay ? `/reports/${encodeURIComponent(selectedDay)}` : null, 0);
  const state = data?.settlement;
  const planIndex = data?.settlement_days.findIndex(day => day.trading_day === state?.trading_day) ?? -1;
  const plan = data?.settlement_days[planIndex];
  const nextDay = data?.settlement_days[planIndex + 1];
  const mutate = async (path: string, body: unknown, success: string) => {
    if (locked.current || !fresh || Date.now() - updatedAt >= 3000) return;
    locked.current = true; setBusy(true); setNotice(undefined);
    try { await writeApi(path, body); setNotice({ success: true, message: success }); refresh(); }
    catch (error) { setNotice({ success: false, message: `${messageOf(error)}。请刷新阶段核对结果；相同交易日与结算价重试不会重复入账。` }); refresh(); }
    finally { locked.current = false; setBusy(false); }
  };
  const download = async (format: 'html' | 'json') => {
    if (!selectedDay || locked.current) return;
    locked.current = true; setBusy(true);
    try {
      const response = await fetch(`/api/reports/${encodeURIComponent(selectedDay)}/download/${format}`, { signal: AbortSignal.timeout(5000) });
      if (!response.ok) { const failure = await response.json() as { detail?: string }; throw new Error(failure.detail ?? '文件暂不可用，请重新导出'); }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `risk-${selectedDay}.${format}`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice({ success: true, message: `${format.toUpperCase()} 日报已下载` });
    } catch (error) { setNotice({ success: false, message: messageOf(error) }); }
    finally { locked.current = false; setBusy(false); }
  };
  return <><PageHeading title="日结与风控日报" description="查看一天结束后的盈亏、资金与保证金，下载可核对的风控日报。" />
    <ModeGate capability="settlement">
      <PageGuide>日结按结算价计算当日盈亏，剩余持仓结转到下一日。已生成的日报固定保存，不随后续行情变化。</PageGuide>
      <Panel title="交易日进度" extra={<Tag>{state?.trading_day}</Tag>}>
        <Steps className="settlement-steps" size="small" current={state?.phase === 'OPEN' ? 0 : state?.phase === 'CLOSING' ? 1 : 2} items={[{ title: '盘中交易' }, { title: '封账并补齐行情' }, { title: '清算完成' }]} />
        <div className="facts inline-facts"><div><span>当前阶段</span><b>{phaseLabel(state?.phase)}</b></div><div><span>日结方式</span><b>{data?.automatic_settlement ? '自动清算' : '手工清算'}</b></div><div><span>已生成日报</span><b>{days.length} 份</b></div><div><span>下一交易日</span><b>{nextDay?.trading_day ?? '本场景最后一日'}</b></div></div>
        <p className="description">{data?.automatic_settlement ? '自动日结已开启，你无需手工操作。收盘后系统会依次封账、补齐行情、生成日报并结转。' : '当前使用手工日结。封账会立即停止本日新增交易，清算需等待收盘帧完整。'}</p>
        <details className="details" open={!data?.automatic_settlement}><summary>{data?.automatic_settlement ? '手工日结操作（按需展开）' : '执行手工日结'}</summary><div className="toolbar section-action">
          <Button disabled={!fresh || busy || state?.phase !== 'OPEN'} onClick={() => void mutate('/settlement/close', { trading_day: state?.trading_day }, '封账已提交，新开平仓已暂停。')}>封账并停止交易</Button>
          <Button type="primary" disabled={!fresh || busy || state?.phase !== 'CLOSING' || state.applied_sequence !== state.close_sequence || !plan}
            onClick={() => void mutate('/settlement/settle', { trading_day: state?.trading_day, prices: plan?.prices }, '全账户清算已提交。')}>按计划结算价清算</Button>
          <Button disabled={!fresh || busy || state?.phase !== 'SETTLED' || !nextDay} onClick={() => void mutate('/settlement/open', { trading_day: nextDay?.trading_day }, '已结转到下一交易日，等待有效行情。')}>结转下一交易日</Button>
        </div></details>
        <details className="details"><summary>查看本日冻结结算价</summary><div className="price-plan">{Object.entries(plan?.prices ?? {}).map(([key, value]) => <span key={key}>{key} <Amount value={value} /></span>)}</div><p className="muted">结算价来自本次运行的冻结配置，可能与收盘前行情不同。</p></details>
        <details className="details"><summary>行情处理进度</summary><p className="muted">已处理行情帧 {state?.applied_sequence ?? '—'} / 收盘帧 {state?.close_sequence ?? '—'}。收盘行情补齐后才可完成清算。</p></details>
      </Panel>
      {notice && <Alert className="inline-alert" showIcon type={notice.success ? 'success' : 'warning'} message={notice.message} role="status" />}
      {state && Object.entries(state.report_errors).map(([day, error]) => <Alert className="inline-alert" key={day} type="warning" showIcon message={`${day} 日报文件未导出`} description={`${error}。账本清算已保留，可以重试导出。`} />)}
      <Panel title="冻结风控日报" extra={<Select aria-label="日报交易日" placeholder="等待首份日报" value={selectedDay} onChange={setSelected} style={{ width: 165 }} options={days.map(day => ({ value: day, label: day }))} />}>
        {!selectedDay ? <div className="report-empty"><Icon name="report" size={35} /><h3>尚无已提交日报，完成首日日结后在此查看</h3><p>{data?.automatic_settlement ? '首日日结完成后，日报会自动出现在这里。' : '请先完成上方的封账和清算操作。'}<br />日报包含账户盈亏、保证金和风险敞口，支持下载 HTML / JSON。</p></div> : <>
          <div className="toolbar report-actions"><Button onClick={() => void download('html')} disabled={!fresh || busy}>下载 HTML</Button><Button onClick={() => void download('json')} disabled={!fresh || busy}>下载 JSON</Button><Button loading={busy} disabled={!fresh} onClick={() => void mutate(`/reports/${encodeURIComponent(selectedDay)}/export`, {}, '日报文件已重新导出，清算未重复入账。')}>重新导出</Button></div>
          {report.error && <Alert className="inline-alert" type="error" showIcon message="日报读取失败" description={report.error} />}
          {report.data ? <ReportBody report={report.data} account={account} /> : report.loading && <p role="status">正在读取冻结日报…</p>}
        </>}
      </Panel>
    </ModeGate>
  </>;
}
function ReportBody({ report, account }: { report: DailyReport; account?: string }) {
  const rows = report.accounts.filter(item => !account || item.account_id === account);
  return <>
    <Descriptions size="small" className="report-meta" column={{ xs: 1, sm: 2, lg: 3 }} items={[{ key: 'run', label: '运行', children: report.run_id }, { key: 'day', label: '交易日', children: report.trading_day }, { key: 'frame', label: '收盘帧', children: report.close_sequence }]} />
    <div className="kpi-grid compact">
      {([['全账户净盈亏', report.totals.net_pnl], ['期末结算余额', report.totals.closing_balance], ['结算保证金', report.totals.margin], ['总名义敞口', report.totals.gross_exposure]] as const).map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong><Amount value={value} /></strong></div>)}
    </div>
    <Table rowKey="account_id" pagination={false} dataSource={rows} scroll={{ x: 1250 }} columns={[
      { title: '账户', dataIndex: 'account_id', fixed: 'left', width: 95 },
      ...(['opening_balance', 'realized_pnl', 'holding_pnl', 'fees', 'net_pnl', 'closing_balance', 'margin', 'available'] as const).map((key, index) => ({ title: ['期初余额', '平仓盈亏', '持仓盯市盈亏', '手续费', '净盈亏', '期末余额', '保证金', '可用资金'][index], key, align: 'right' as const, render: (_: unknown, row: DailyReport['accounts'][number]) => <Amount value={row.settlement[key]} /> })),
      { title: '盘中风险', width: 170, render: (_, row) => <RiskTags broken={row.risk.circuit_broken} warning={row.risk.warning} restricted={row.risk.restricted_products} /> },
    ]} expandable={{ expandedRowRender: row => <div><h3>剩余持仓结算明细</h3><Table size="small" rowKey="lot_id" pagination={false} dataSource={row.settlement.lines} scroll={{ x: 650 }} columns={[
      { title: '合约', dataIndex: 'instrument_id' }, { title: '方向', dataIndex: 'side', render: sideLabel }, { title: '手数', dataIndex: 'quantity' },
      { title: '当日基准', dataIndex: 'basis', render: (value: string) => <Amount value={value} /> }, { title: '结算价', dataIndex: 'settlement_price', render: (value: string) => <Amount value={value} /> },
      { title: '盯市盈亏', dataIndex: 'pnl', render: (value: string) => <Amount value={value} /> },
    ]} /><p className="muted">本日风险事件 {row.risk_event_count} 条 · 总敞口 <Amount value={row.valuation.gross_exposure} /> · 净敞口 <Amount value={row.valuation.net_exposure} /></p></div> }} />
    <p className="table-note">金额按冻结结算价核算；风险状态来自本日收盘前行情。上方指标始终为全部账户汇总。</p>
    <details className="details"><summary>配置追溯</summary><p className="mono">账户配置 {report.config_hash}</p><p className="mono">风控规则 {report.policy_hash}</p><p className="mono">清算计划 {report.settlement_plan_hash}</p></details>
  </>;
}
