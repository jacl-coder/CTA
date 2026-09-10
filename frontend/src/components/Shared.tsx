import { Alert, Empty, Skeleton, Tag } from 'antd';
import type { ReactNode } from 'react';
import { useWorkspace } from '../app/workspaceContext';
import { amountTone, money, productLabel, recordTimeLabel } from './format';
import { Icon, type IconName } from './Icon';

export function Amount({ value }: { value?: string | null }) { return <span className={`amount ${amountTone(value)}`}>{money(value)}</span>; }
export function RecordTime({ value }: { value?: number | null }) {
  const label = recordTimeLabel(value);
  if (label === '未记录' || value == null) return <span className="muted">未记录</span>;
  const [day, clock] = label.split(' ');
  return <time className="record-time" dateTime={new Date(value).toISOString()} title={`${label} 北京时间`}><span>{day}</span><strong>{clock}</strong></time>;
}
export function PageHeading({ title, description, extra }: { title: string; description: string; extra?: ReactNode }) {
  return <div className="page-heading"><div><h1>{title}</h1><p className="description">{description}</p></div>{extra}</div>;
}
export function Panel({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) {
  return <section className="panel"><div className="panel-heading"><h2>{title}</h2>{extra}</div>{children}</section>;
}
export function ModeGate({ capability, children }: { capability: 'ledger' | 'trading' | 'multi_source' | 'settlement'; children: ReactNode }) {
  const { data, loading, error } = useWorkspace();
  if (!data) return loading ? <Skeleton active paragraph={{ rows: 5 }} /> : <Alert type="error" message="无法读取工作台" description={error} showIcon />;
  if (!data.capabilities[capability]) return <Empty description={{ ledger: '当前服务尚未加载账户账本', trading: '当前为账本查询模式，尚未启用交易', multi_source: '当前未启用独立行情源', settlement: '当前运行未启用日结与日报' }[capability]} />;
  return children;
}
export function RiskTags({ broken, warning, restricted }: { broken: boolean; warning: boolean; restricted: string[] }) {
  return <div className="tags">{broken && <Tag color="red">已熔断</Tag>}{warning && <Tag color="orange">浮亏告警</Tag>}
    {restricted.length > 0 && <Tag color="gold">敞口受限</Tag>}{!broken && !warning && !restricted.length && <Tag color="green">规则正常</Tag>}</div>;
}

export function MetricCard({ label, value, hint, icon, emphasis = false }: { label: string; value?: string | null; hint: string; icon: IconName; emphasis?: boolean }) {
  return <div className={`metric ${emphasis ? 'metric-featured' : ''}`}><div className="metric-label"><span>{label}</span><Icon name={icon} size={18} /></div><strong><Amount value={value} /></strong><small>{hint}</small></div>;
}
export function ContractLabel({ instrument, product }: { instrument: string; product?: string }) {
  const name = productLabel(product ?? instrument.split('.').slice(0, -1).join('.') + '.' + (instrument.split('.').at(-1)?.match(/^[a-zA-Z]+/)?.[0] ?? ''));
  return <span className="contract-label">{name && <strong>{name}</strong>}<span>{instrument}</span></span>;
}
export function PageGuide({ children }: { children: ReactNode }) {
  return <div className="page-guide"><Icon name="shield" size={17} /><span>{children}</span></div>;
}
