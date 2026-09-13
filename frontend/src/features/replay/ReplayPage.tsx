import { Alert, Button, Input, Select, Slider, Table, Tag } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { messageOf, request, writeApi } from '../../api/client';
import type { components } from '../../api/schema';
import { useWorkspace } from '../../app/workspaceContext';
import { Amount, PageHeading, Panel, RiskTags } from '../../components/Shared';
import { phaseLabel, reasonLabel } from '../../components/format';
import { ratioToPercent, percentToRatio } from '../../components/percent';
import { ReplayChart } from './ReplayChart';

type Dataset = components['schemas']['ReplayDataset'];
type Result = components['schemas']['ReplayComparison'];
type Policy = components['schemas']['TradingSettings'];
const kinds: Record<string, string> = { frame: '历史行情', order: '历史委托', close: '收盘封账', settle: '日结清算', open_day: '结转次日' };
function download(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function ReplayPage() {
  const { fresh, data: workspace } = useWorkspace();
  const [dataset, setDataset] = useState<Dataset>();
  const [policy, setPolicy] = useState<Policy>();
  const [warning, setWarning] = useState('2');
  const [loss, setLoss] = useState('5');
  const [result, setResult] = useState<Result>();
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [error, setError] = useState('');
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [account, setAccount] = useState('');
  const load = (value: Dataset) => {
    const warn = ratioToPercent(value.policy.warning_ratio), limit = ratioToPercent(value.policy.loss_ratio);
    setDataset(value); setPolicy(value.policy); setWarning(warn);
    setLoss(limit); setResult(undefined); setIndex(0); setPlaying(false);
    setAccount(value.seed.accounts[0].account_id);
  };
  const work = async (action: () => Promise<void>) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setPlaying(false);
    try { await action(); } catch (failure) { setError(messageOf(failure)); }
    finally { locked.current = false; setBusy(false); }
  };
  const fetchDataset = (source: 'example' | 'current') => void work(async () => {
    load(await request<Dataset>(`/replay/${source}`, { signal: AbortSignal.timeout(15000) }));
  });
  const upload = (file?: File) => {
    if (!file) return;
    void work(async () => {
      if (file.size > 8 * 1024 * 1024) throw new Error('历史文件不能超过 8 MiB');
      const value = await writeApi<Dataset>('/replay/validate', JSON.parse(await file.text()), 15000);
      load(value);
    });
  };
  const execute = () => void work(async () => {
    if (!dataset || !policy) return;
    setResult(undefined);
    const comparison = await writeApi<Result>('/replay/run', { dataset, candidate_policy: {
      ...policy, warning_ratio: percentToRatio(warning), loss_ratio: percentToRatio(loss),
    } }, 60000);
    setResult(comparison); setIndex(0);
  });
  useEffect(() => {
    if (!playing || !result) return;
    const timer = setInterval(() => setIndex(value => {
      if (value >= result.baseline.points.length - 1) return value;
      return value + 1;
    }), 600);
    return () => clearInterval(timer);
  }, [playing, result]);
  const ended = !!result && index === result.baseline.points.length - 1;
  const point = result?.baseline.points[index];
  const other = result?.candidate.points[index];
  const invalidate = () => { setResult(undefined); setPlaying(false); };
  const comparisonRows = point?.accounts.map(item => ({ ...item, candidate: other?.accounts.find(row => row.account_id === item.account_id) }));
  return <>
    <PageHeading title="历史回放与规则验证" description="选择历史输入，用原规则和候选规则分别重算，查看熔断、拒单与资金变化。" />
    <Panel title="1. 选择历史数据">
      <div className="toolbar replay-actions">
        <Button disabled={busy} onClick={() => fetchDataset('example')}>载入两日历史样例</Button>
        <Button disabled={busy || !fresh || !workspace?.capabilities.trading} onClick={() => fetchDataset('current')}>读取当前运行历史</Button>
        <label className="file-input-label">导入历史 JSON<input type="file" aria-label="导入历史 JSON" accept=".json,application/json" disabled={busy} onChange={event => { upload(event.target.files?.[0]); event.target.value = ''; }} /></label>
        <Button disabled={!dataset || busy} onClick={() => download('cta-history.json', dataset)}>下载历史数据</Button>
      </div>
      <p className="table-note">样例包含下跌、反弹、熔断后的开仓尝试、两日日结和平昨。也可保存当前运行的历史，再导入复验。最多 5000 步、8 MiB。</p>
      {dataset && <div className="soft-note">{dataset.name} · {dataset.steps.length} 步 · {dataset.seed.accounts.length} 个账户 · 起始交易日 {dataset.seed.trading_day}</div>}
    </Panel>
    {error && <Alert className="inline-alert" showIcon type="error" message={error} />}
    {dataset && policy && <Panel title="2. 对照风控规则">
      <p className="description">原规则：浮亏告警 {ratioToPercent(dataset.policy.warning_ratio)}%，熔断 {ratioToPercent(dataset.policy.loss_ratio)}%。可先按原值验证，再将候选熔断改为 5% 对照。</p>
      <div className="replay-policy">
        <label>候选告警（%）<Input aria-label="候选告警（%）" value={warning} disabled={busy} onChange={event => { setWarning(event.target.value); invalidate(); }} /></label>
        <label>候选熔断（%）<Input aria-label="候选熔断（%）" value={loss} disabled={busy} onChange={event => { setLoss(event.target.value); invalidate(); }} /></label>
        <label>默认品种敞口限额（元）<Input aria-label="默认品种敞口限额（元）" value={policy.default_product_limit} disabled={busy} onChange={event => { setPolicy({ ...policy, default_product_limit: event.target.value }); invalidate(); }} /></label>
      </div>
      {!!policy.product_limits?.length && <details className="replay-limits"><summary>账户专属敞口限额（优先于默认值）</summary><div className="replay-policy">{policy.product_limits.map((limit, i) => <label key={`${limit.account_id}:${limit.product_id}`}>账户 {limit.account_id} · {limit.product_id}<Input aria-label={`账户 ${limit.account_id} ${limit.product_id} 限额`} value={limit.amount} disabled={busy} onChange={event => { setPolicy({ ...policy, product_limits: policy.product_limits!.map((item, j) => j === i ? { ...item, amount: event.target.value } : item) }); invalidate(); }} /></label>)}</div></details>}
      <div className="toolbar"><Button type="primary" loading={busy} disabled={!fresh} onClick={execute}>运行规则对照</Button><Button disabled={busy} onClick={() => { setPolicy(dataset.policy); setWarning(ratioToPercent(dataset.policy.warning_ratio)); setLoss(ratioToPercent(dataset.policy.loss_ratio)); invalidate(); }}>恢复原规则</Button></div>
      <p className="table-note">回放复用线上交易与清算计算，在独立内存中执行。候选参数不会修改当前账户。不同规则可能改变成交和后续持仓；历史记录的行情不可用状态仍会拒单。</p>
    </Panel>}
    {result && point && other && <>
      <Panel title="3. 全段验证结果" extra={<Button onClick={() => download('cta-replay-result.json', result)}>下载对照结果</Button>}>
        {result.baseline.matches_recording != null && <Alert className="inline-alert" showIcon type={result.baseline.matches_recording ? 'success' : 'error'} message={result.baseline.matches_recording ? '原规则重算与导出时的完整账本状态一致' : '原规则重算与历史摘要不一致，请检查数据和规则版本'} />}
        <div className="two-columns"><div className="soft-note">原规则：{result.baseline.events.filter(item => item.kind === 'CIRCUIT_BREAK').length} 次熔断 · {result.baseline.points.filter(item => item.order?.status === 'REJECTED' && !item.order.duplicate).length} 笔拒单 · {result.baseline.report_count} 日日结</div><div className="soft-note">候选规则：{result.candidate.events.filter(item => item.kind === 'CIRCUIT_BREAK').length} 次熔断 · {result.candidate.points.filter(item => item.order?.status === 'REJECTED' && !item.order.duplicate).length} 笔拒单 · {result.candidate.report_count} 日日结</div></div>
        <p className="table-note">历史输入摘要：<code className="digest">{result.dataset_digest}</code>。样例为固定模拟历史；结果用于比较规则行为，单一样例不代表策略收益或参数最优。</p>
      </Panel>
      <Panel title="4. 逐步查看" extra={<Select aria-label="回放图表账户" value={account} onChange={setAccount} options={dataset?.seed.accounts.map(item => ({ value: item.account_id, label: `账户 ${item.account_id}` }))} />}>
        <ReplayChart result={result} account={account} index={index} />
        <div className="toolbar"><Button onClick={() => { if (ended) setIndex(0); setPlaying(!playing || ended); }}>{playing && !ended ? '暂停回放' : '播放回放'}</Button><Button disabled={index === 0} onClick={() => { setPlaying(false); setIndex(index - 1); }}>上一步</Button><Button disabled={ended} onClick={() => { setPlaying(false); setIndex(index + 1); }}>下一步</Button><span>第 {index + 1} / {result.baseline.points.length} 步 · {kinds[point.kind]} · 交易日 {point.trading_day} · 行情帧 {point.frame_sequence ?? '—'} · {phaseLabel(point.phase)}</span></div>
        <Slider aria-label="回放步骤" min={1} max={result.baseline.points.length} value={index + 1} onChange={value => { setPlaying(false); setIndex(value - 1); }} />
        {point.order && <div className="two-columns"><Alert type={point.order.status === 'REJECTED' ? 'warning' : 'success'} message={`原规则 · ${reasonLabel(point.order.reason)}`} description={`账户 ${point.order.account_id} · ${point.order.request_id}`} /><Alert type={other.order?.status === 'REJECTED' ? 'warning' : 'success'} message={`候选规则 · ${reasonLabel(other.order?.reason ?? '')}`} /></div>}
        <Table rowKey="account_id" size="small" pagination={false} scroll={{ x: 1000 }} dataSource={comparisonRows} columns={[
          { title: '账户', dataIndex: 'account_id' },
          { title: '浮亏比例（原 / 候选）', render: (_, row) => `${row.loss_percent ?? '—'}% / ${row.candidate?.loss_percent ?? '—'}%` },
          { title: '行情估值权益（原 / 候选）', render: (_, row) => <><Amount value={row.equity} /> / <Amount value={row.candidate?.equity} /></> },
          { title: '原规则风险', render: (_, row) => <RiskTags broken={row.circuit_broken} warning={row.warning} restricted={row.restricted_products} /> },
          { title: '候选规则风险', render: (_, row) => row.candidate && <RiskTags broken={row.candidate.circuit_broken} warning={row.candidate.warning} restricted={row.candidate.restricted_products} /> },
          { title: '日结余额（原 / 候选）', render: (_, row) => <><Amount value={row.settled_balance} /> / <Amount value={row.candidate?.settled_balance} /></> },
        ]} />
        <p className="table-note">权益按该步最近行情估值；日结余额按独立结算价计算，仅清算阶段显示。跨日后等待本日行情时不显示旧日估值。</p>
      </Panel>
      <Panel title="风险事件对照"><Table size="small" rowKey={row => `${row.rule}:${row.step}:${row.account_id}:${row.kind}:${row.product_id}`} pagination={{ pageSize: 10 }} scroll={{ x: 720 }} dataSource={[
        ...result.baseline.events.map(item => ({ ...item, rule: '原规则' })), ...result.candidate.events.map(item => ({ ...item, rule: '候选规则' })),
      ].sort((a, b) => a.step - b.step)} columns={[
        { title: '规则', dataIndex: 'rule', render: (v: string) => <Tag>{v}</Tag> }, { title: '步骤', dataIndex: 'step' }, { title: '交易日', dataIndex: 'trading_day' }, { title: '账户', dataIndex: 'account_id' }, { title: '行情帧', dataIndex: 'frame_sequence' }, { title: '事件', dataIndex: 'kind', render: reasonLabel },
      ]} /></Panel>
    </>}
  </>;
}
