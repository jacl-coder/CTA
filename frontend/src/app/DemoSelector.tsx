import { Alert, Button, Select } from 'antd';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useWorkspace } from './workspaceContext';
import { messageOf, writeApi } from '../api/client';

type Mode = 'continuous' | 'manual' | 'fault';
const scenarios: { value: Mode; label: string; description: string; path: string }[] = [
  { value: 'continuous', label: '正常运行', description: '两个行情源持续报价，可直接开平仓；每 30 分钟自动日结并进入下一模拟日。', path: '/' },
  { value: 'manual', label: '手工演示', description: '提交初始行情 → A 开多 1 手螺纹钢 → 降至 3400 触发熔断 → 反弹至 3500 → 日结与日报。A 初始已有 2 手，开仓后共 3 手。', path: '/trading' },
  { value: 'fault', label: '故障与清算', description: '自动运行约 96 秒：双源断线 → 恢复补数 → 追溯熔断 → 两日日结。结束后可查看风险事件和日报，或重新开始。', path: '/market' },
];
export function DemoSelector() {
  const { data, fresh, refresh } = useWorkspace();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Mode>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  if (!data?.demo) return null;
  const mode = data.demo.mode;
  const choice = selected ?? mode;
  const current = scenarios.find(item => item.value === mode)!;
  const change = async (target: Mode) => {
    if (locked.current || !fresh) return;
    locked.current = true; setBusy(true); setError('');
    try {
      await writeApi('/demo/switch', { mode: target }, 20000);
      setSelected(undefined);
      navigate(scenarios.find(item => item.value === target)!.path);
    } catch (failure) {
      setError(`${messageOf(failure)}。请等待页面更新确认当前场景，勿连续重复切换。`);
    } finally {
      refresh(); setBusy(false); locked.current = false;
    }
  };
  return <section className="demo-selector" aria-label="演示场景">
    <div className="demo-selector-heading"><div><span className="muted">当前场景</span><strong>{current.label}</strong></div>
      <div className="toolbar">
        <Select aria-label="选择演示场景" value={choice} onChange={setSelected} disabled={busy || !fresh} options={scenarios.map(({ value, label }) => ({ value, label }))} style={{ minWidth: 145 }} />
        <Button type="primary" loading={busy} disabled={!fresh || choice === mode} onClick={() => void change(choice)}>进入场景</Button>
        <Button disabled={!fresh || busy} onClick={() => void change(mode)}>重新开始当前场景</Button>
      </div>
    </div>
    <p>{busy ? '正在关闭旧场景并启动新场景，请稍候…' : current.description}</p>
    <small>切换或重新开始会新建演示数据，旧记录保留。停止服务后再启动，将恢复上次场景与进度。</small>
    {error && <Alert className="inline-alert" showIcon type="warning" message={error} />}
  </section>;
}
