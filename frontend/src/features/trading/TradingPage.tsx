import { Alert, Button, Form, Input, InputNumber, Select, Table, Tag } from 'antd';
import { useRef, useState } from 'react';
import { useWorkspace } from '../../app/workspaceContext';
import { ApiError, messageOf, request, writeApi } from '../../api/client';
import type { OrderInput, OrderResult } from '../../api/types';
import { Amount, ContractLabel, ModeGate, PageGuide, PageHeading, Panel, RecordTime } from '../../components/Shared';
import { offsetLabel, productLabel, reasonLabel, sideLabel } from '../../components/format';
import { useWorkspacePolling } from '../../hooks/useWorkspacePolling';
import { readAttempt, saveAttempt } from './orderAttempt';

type Draft = Pick<OrderInput, 'account_id' | 'instrument_id' | 'side' | 'offset' | 'quantity'>;
export function TradingPage() {
  const { data } = useWorkspace();
  return <><PageHeading title="模拟交易" description="选择账户与合约，提交模拟订单。成交后可在持仓与成交页核对。" />
    <ModeGate capability="trading">{data?.run_id && <TradingDesk key={data.run_id} run={data.run_id} />}</ModeGate></>;
}
function TradingDesk({ run }: { run: string }) {
  const { data, fresh, updatedAt, account, refresh } = useWorkspace();
  const [form] = Form.useForm<Draft>();
  const [attempt, setAttempt] = useState<OrderInput | undefined>(() => readAttempt(run));
  const [result, setResult] = useState<OrderResult>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [pagination, setPagination] = useState({ account, page: 0 });
  const page = pagination.account === account ? pagination.page : 0;
  const setPage = (update: (page: number) => number) => setPagination({ account, page: update(page) });
  const history = useWorkspacePolling<OrderResult[]>(`/orders?${new URLSearchParams({ limit: '21', offset: String(page * 20), ...(account ? { account_id: account } : {}) })}`);
  const chosenAccount = Form.useWatch('account_id', form) ?? account ?? data?.accounts[0]?.account_id;
  const offset = Form.useWatch('offset', form) ?? 'OPEN';
  const instrumentId = Form.useWatch('instrument_id', form) ?? data?.instruments[0]?.instrument_id;
  const side = Form.useWatch('side', form) ?? 'LONG';
  const instrument = data?.instruments.find(item => item.instrument_id === instrumentId);
  const position = data?.positions.find(item => item.account_id === chosenAccount && item.instrument_id === instrumentId && item.side === side);
  const quote = data?.market?.prices[instrumentId ?? ''];
  const execution = offset === 'OPEN' ? (side === 'LONG' ? '买入开仓' : '卖出开仓') : (side === 'LONG' ? '卖出平仓' : '买入平仓');
  const risk = data?.risks.find(item => item.account_id === chosenAccount);
  const available = fresh && !!data?.system.trading_available && (offset !== 'OPEN' || !!risk?.opening_allowed);
  const complete = (value: OrderResult) => {
    setResult(value); setError(''); setAttempt(undefined); saveAttempt(run); refresh();
  };
  const send = async (order: OrderInput) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setResult(undefined);
    // Retain the exact payload before network I/O, including across page reloads.
    setAttempt(order); saveAttempt(run, order);
    try { complete(await writeApi<OrderResult>('/orders', order)); }
    catch (failure) {
      if (failure instanceof ApiError && [401, 403, 404, 422, 429].includes(failure.status)) {
        setAttempt(undefined); saveAttempt(run); setError(messageOf(failure));
      } else setError(`结果尚未确认：${messageOf(failure)}。请核对原订单，勿新建重复委托。`);
      refresh();
    } finally { locked.current = false; setBusy(false); }
  };
  const submit = (values: Draft) => {
    if (attempt || !available || Date.now() - updatedAt >= 3000) { setError('行情或连接状态已变化，请刷新后重新检查。'); return; }
    const day = data?.accounts.find(item => item.account_id === values.account_id)?.trading_day;
    if (!day) return;
    void send({ ...values, request_id: crypto.randomUUID(), trading_day: day });
  };
  const lookup = async () => {
    if (!attempt || locked.current) return;
    locked.current = true; setBusy(true);
    try { complete(await request<OrderResult>(`/orders/${encodeURIComponent(attempt.account_id)}/${encodeURIComponent(attempt.request_id)}`, { signal: AbortSignal.timeout(5000) })); }
    catch (failure) { setError(failure instanceof ApiError && failure.status === 404 ? '尚未查到该订单。可以重试原订单，编号和参数保持不变。' : messageOf(failure)); }
    finally { locked.current = false; setBusy(false); }
  };
  return <>
    {data?.capabilities.manual_market && <ManualPrices />}
    <PageGuide>开仓是新增持仓；平今关闭今天的持仓，平昨关闭前日持仓。所有订单使用模拟资金。</PageGuide>
    <div className="two-columns trade-columns"><Panel title="提交模拟订单" extra={<Tag>模拟交易日 {data?.accounts[0]?.trading_day}</Tag>}>
      {attempt && <Alert className="inline-alert" showIcon type="warning" message="有一笔订单需要核对" description={<><p>账户 {attempt.account_id} · {attempt.instrument_id} · {sideLabel(attempt.side)}{offsetLabel(attempt.offset)} {attempt.quantity} 手</p><p className="mono">请求编号 {attempt.request_id}</p><div className="toolbar"><Button onClick={() => void lookup()} loading={busy}>核对订单结果</Button><Button onClick={() => void send(attempt)} disabled={busy || !fresh}>重试原订单</Button></div></>} />}
      {error && <Alert role="alert" className="inline-alert" showIcon type="warning" message={error} />}
      {result && <Alert role="status" className="inline-alert" showIcon type={result.status === 'FILLED' ? 'success' : 'warning'} message={result.status === 'FILLED' ? '模拟成交成功' : '订单已拒绝，未产生成交'} description={<>{reasonLabel(result.reason)}{result.price && <> · 成交价 <Amount value={result.price} /> · 手续费 <Amount value={result.fee} /></>}{result.duplicate && ' · 已返回原订单结果'}</>} />}
      <Form form={form} layout="vertical" onFinish={submit} disabled={busy || !!attempt}
        initialValues={{ account_id: account ?? data?.accounts[0]?.account_id, instrument_id: data?.instruments[0]?.instrument_id, side: 'LONG', offset: 'OPEN', quantity: 1 }}>
        <div className="form-grid"><Form.Item name="account_id" label="下单账户" rules={[{ required: true }]}><Select options={data?.accounts.map(item => ({ value: item.account_id, label: `账户 ${item.account_id}` }))} /></Form.Item>
          <Form.Item name="instrument_id" label="交易合约" rules={[{ required: true }]}><Select options={data?.instruments.map(item => ({ value: item.instrument_id, label: `${productLabel(item.product_id)} ${item.instrument_id}`.trim() }))} /></Form.Item>
          <Form.Item name="side" label="持仓方向" rules={[{ required: true }]}><Select options={[{ value: 'LONG', label: '多头（买开 / 卖平）' }, { value: 'SHORT', label: '空头（卖开 / 买平）' }]} /></Form.Item>
          <Form.Item name="offset" label="开平意图" rules={[{ required: true }]}><Select options={[{ value: 'OPEN', label: '开仓' }, { value: 'CLOSE_TODAY', label: '平今仓' }, { value: 'CLOSE_YESTERDAY', label: '平昨仓' }]} /></Form.Item>
          <Form.Item name="quantity" label="数量（手）" rules={[{ required: true }, { validator: (_, value: number) => Number.isSafeInteger(value) && value > 0 ? Promise.resolve() : Promise.reject(new Error('请输入正整数手数')) }]}><InputNumber min={1} max={1000000} precision={0} style={{ width: '100%' }} /></Form.Item>
          <div className="order-action-caption"><span>本次操作</span><strong>{execution}</strong></div>
        </div>
        <Button className="order-submit" type="primary" htmlType="submit" loading={busy} disabled={!available || !!attempt}>提交模拟订单</Button>
        <p className="order-form-note">按提交时的有效行情模拟成交。平仓请选择已有持仓的方向，例如平多仓仍选择“多头”。</p>
        {!available && <p className="blocking-note">{!fresh ? '连接中断，暂不可提交' : !data?.system.trading_available ? '行情不可用或交易日已封账' : risk?.blocking_reasons.map(reasonLabel).join('；') || '当前账户不可开仓'}</p>}
      </Form>
    </Panel><Panel title={`账户 ${chosenAccount ?? '—'} · 交易前参考`}>
      <div className="order-preview"><div className="order-preview-heading">{instrumentId && <ContractLabel instrument={instrumentId} product={instrument?.product_id} />}<Tag>{fresh && data?.market?.market_ready ? '行情有效' : '参考快照'}</Tag></div><div className="order-preview-price"><span>最新报价</span><Amount value={quote} /></div></div>
      <div className="facts"><div><span>权益</span><Amount value={risk?.equity} /></div><div><span>可用资金</span><Amount value={risk?.available_funds} /></div><div><span>持仓浮盈亏</span><Amount value={risk?.floating_pnl} /></div><div><span>总名义敞口</span><Amount value={risk?.gross_exposure} /></div><div><span>该方向今仓 / 昨仓</span><b>{position?.today_quantity ?? 0} / {position?.yesterday_quantity ?? 0} 手</b></div><div><span>每手手续费</span><Amount value={instrument?.fee_per_lot} /></div></div>
      <Alert showIcon type={risk?.circuit_broken ? 'warning' : 'info'} message={risk?.circuit_broken ? '当日熔断已生效' : '下单前将再次核对风险'} description={risk?.circuit_broken ? '价格反弹不会解除当日熔断；有效行情下仍可提交合法平仓。' : '最新报价仅供参考。成交时会再次检查行情、账户限制及可用保证金，以订单结果为准。'} />
    </Panel></div>
    <Panel title="订单结果">
      <p className="table-note">实际处理时间记录服务端处理订单的北京时间，精确到毫秒；成交与拒单均记录，重试沿用原时间。历史订单未记录时间的显示“未记录”。</p>
      {history.error && <Alert type="error" showIcon message="订单记录更新失败" description={history.error} className="inline-alert" />}
      <Table rowKey={row => `${row.account_id}:${row.request_id}`} size="small" pagination={false} loading={history.loading} scroll={{ x: 1120 }} dataSource={history.data?.slice(0, 20)} columns={[
        { title: '实际处理时间（北京时间）', dataIndex: 'processed_at_ms', width: 190, render: (value?: number | null) => <RecordTime value={value} /> },
        { title: '账户', dataIndex: 'account_id' },
        { title: '结果', dataIndex: 'status', render: (value: string) => <Tag color={value === 'FILLED' ? 'green' : 'orange'}>{value === 'FILLED' ? '已成交' : '已拒绝'}</Tag> },
        { title: '原因', dataIndex: 'reason', render: reasonLabel },
        { title: '成交价', dataIndex: 'price', render: (value: string | null) => <Amount value={value} /> }, { title: '手续费', dataIndex: 'fee', render: (value: string | null) => <Amount value={value} /> },
        { title: '行情帧', dataIndex: 'frame_sequence' }, { title: '请求编号', dataIndex: 'request_id', ellipsis: true, width: 240 },
      ]} />
      <div className="pagination"><Button disabled={page === 0} onClick={() => setPage(value => value - 1)}>上一页</Button><span>第 {page + 1} 页</span><Button disabled={!history.data || history.data.length <= 20 || !!history.error} onClick={() => setPage(value => value + 1)}>下一页</Button></div>
    </Panel>
  </>;
}
function ManualPrices() {
  const { data, fresh, updatedAt, refresh } = useWorkspace();
  const [form] = Form.useForm<{ prices: Record<string, string> }>();
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [notice, setNotice] = useState('');
  const publish = async (values: { prices: Record<string, string> }) => {
    if (locked.current || !fresh || Date.now() - updatedAt > 3000 || data?.settlement?.phase === 'SETTLED') return;
    locked.current = true; setBusy(true); setNotice('');
    const sequence = (data?.market?.sequence ?? 0) + 1;
    try {
      await writeApi('/simulation/frames', { sequence, trading_day: data?.accounts[0]?.trading_day, prices: values.prices });
      setNotice(`第 ${sequence} 帧已提交，账户风险已重新计算。`); refresh();
    } catch (error) { setNotice(`${messageOf(error)}。请刷新行情进度后再操作。`); refresh(); }
    finally { locked.current = false; setBusy(false); }
  };
  return <Panel title="手工模拟行情" extra={<Tag>仅手工模式可用</Tag>}>
    <p className="description">填写全部合约价格并提交下一帧。输入十进制价格，行情将直接触发风险计算。</p>
    {notice && <Alert className="inline-alert" showIcon type="info" message={notice} />}
    {data?.demo && <div className="toolbar section-action">
      <Button onClick={() => form.setFieldsValue({ prices: data.demo!.initial_prices })}>填入初始行情</Button>
      <span className="muted">首次已填好全部价格，提交后为 A 开多 1 手；再将螺纹钢改为 3400，提交即可观察熔断。</span>
    </div>}
    <Form form={form} layout="vertical" onFinish={values => void publish(values)}><div className="price-inputs">
      {data?.instruments.map(item => <Form.Item key={item.instrument_id} name={['prices', item.instrument_id]} label={item.instrument_id} initialValue={data.market?.prices[item.instrument_id] ?? data.demo?.initial_prices[item.instrument_id]}
        rules={[{ required: true, message: '请输入价格' }, { pattern: /^\d+(\.\d+)?$/, message: '请输入十进制价格' }]}><Input inputMode="decimal" placeholder={`最小变动 ${item.tick_size}`} /></Form.Item>)}
    </div><Button htmlType="submit" loading={busy} disabled={!fresh || data?.settlement?.phase === 'SETTLED'}>提交完整价格帧</Button></Form>
  </Panel>;
}
