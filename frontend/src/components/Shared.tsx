import { Alert, Empty, Skeleton, Tag } from 'antd';
import type { ReactNode } from 'react';
import { useWorkspace } from '../app/workspaceContext';
import { amountTone, money } from './format';

export function Amount({ value }: { value?: string | null }) { return <span className={`amount ${amountTone(value)}`}>{money(value)}</span>; }
export function PageHeading({ title, description, extra }: { title: string; description: string; extra?: ReactNode }) {
  return <div className="page-heading"><div><p className="eyebrow">CTA / 风控工作台</p><h1>{title}</h1><p className="description">{description}</p></div>{extra}</div>;
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
