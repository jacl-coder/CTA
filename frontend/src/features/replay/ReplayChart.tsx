import type { components } from '../../api/schema';
import { ratioToPercent } from '../../components/percent';

// Number conversion is only for SVG coordinates; authoritative risk values come from Python Decimal.
export function ReplayChart({ result, account, index }: { result: components['schemas']['ReplayComparison']; account: string; index: number }) {
  const runs = [result.baseline, result.candidate];
  const series = runs.map(run => run.points.map(point => {
    const value = point.accounts.find(item => item.account_id === account)?.loss_percent;
    return value == null ? null : Number(value);
  }));
  const thresholds = runs.map(run => Number(ratioToPercent(run.policy.loss_ratio)));
  const maximum = Math.max(1, ...thresholds, ...series.flat().filter((item): item is number => item !== null)) * 1.15;
  const x = (step: number) => 45 + step * 900 / Math.max(1, result.baseline.points.length - 1);
  const y = (value: number) => 195 - value / maximum * 165;
  const colors = ['#255fce', '#c36a22'];
  return <div className="replay-chart"><svg viewBox="0 0 980 230" role="img" aria-label={`账户 ${account} 原规则与候选规则的浮亏比例历史，虚线为各自熔断阈值`}>
    <title>账户 {account} · 浮亏比例（%）</title>
    <text x="10" y="15">浮亏比例 %</text><path d="M45 25 V195 H950" stroke="#c8cdd5" fill="none" />
    <text x="25" y="200">0</text><text x="850" y="222">历史步骤 →</text>
    {series.map((values, i) => { let gap = true; const path = values.map((value, step) => { if (value == null) { gap = true; return ''; } const command = gap ? 'M' : 'L'; gap = false; return `${command}${x(step)},${y(value)}`; }).join(' ');
      return <g key={i}><path d={path} fill="none" stroke={colors[i]} strokeWidth="2.5" /><path d={`M45 ${y(thresholds[i])} H950`} stroke={colors[i]} strokeDasharray="6 5" /><text x="48" y={y(thresholds[i]) - (i ? -14 : 5)} fill={colors[i]}>{i ? '候选' : '原规则'}熔断 {thresholds[i]}%</text></g>;
    })}
    <path d={`M${x(index)} 25 V195`} stroke="#777" strokeDasharray="2 3" />
  </svg><div className="toolbar chart-legend"><span className="baseline-color">蓝色：原规则</span><span className="candidate-color">橙色：候选规则</span><span>灰线：当前步骤；空白段表示尚无当日有效行情。</span></div></div>;
}
