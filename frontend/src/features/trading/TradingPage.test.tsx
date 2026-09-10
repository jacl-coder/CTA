import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { browserMocks, context, wrap } from '../../test/workspace';
import { TradingPage } from './TradingPage';
import { attemptKey } from './orderAttempt';

beforeEach(() => { browserMocks(); sessionStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('断线与熔断禁止开仓，熔断仍允许选择平今', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response('[]'))));
  const value = context({ fresh: false });
  const view = render(wrap(<TradingPage />, value));
  expect(screen.getByRole('button', { name: '提交模拟订单' })).toBeDisabled();
  value.fresh = true; value.data!.risks[0].circuit_broken = true; value.data!.risks[0].opening_allowed = false;
  view.rerender(wrap(<TradingPage />, { ...value }));
  expect(screen.getByRole('button', { name: '提交模拟订单' })).toBeDisabled();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '开平意图' }));
  fireEvent.click(await screen.findByText('平今仓', { selector: '.ant-select-item-option-content' }));
  expect(screen.getByRole('button', { name: '提交模拟订单' })).toBeEnabled();
});
it('提交回包丢失后刷新页面仍保留原编号，核对与重试不生成新订单', async () => {
  const value = context();
  const bodies: string[] = [];
  const fetcher = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    if (url === '/api/session') return Promise.resolve(new Response('{"token":"local-test"}'));
    if (url === '/api/orders' && options?.method === 'POST') {
      bodies.push(options.body as string);
      if (bodies.length === 1) return Promise.reject(new Error('连接中断'));
      const order = JSON.parse(bodies[0]);
      return Promise.resolve(new Response(JSON.stringify({ ...order, status: 'FILLED', reason: 'FILLED', price: '3510', fee: '2', duplicate: true, frame_sequence: 1 })));
    }
    if (url.startsWith('/api/orders/A/')) return Promise.resolve(new Response('{"detail":"missing"}', { status: 404 }));
    return Promise.resolve(new Response('[]'));
  });
  vi.stubGlobal('fetch', fetcher);
  const first = render(wrap(<TradingPage />, value));
  fireEvent.click(screen.getByRole('button', { name: '提交模拟订单' }));
  expect(await screen.findByText('有一笔订单需要核对')).toBeInTheDocument();
  await screen.findByText(/结果尚未确认/);
  expect(bodies).toHaveLength(1);
  expect(sessionStorage.getItem(attemptKey(value.data!.run_id!))).toBe(bodies[0]);
  first.unmount(); render(wrap(<TradingPage />, value));
  expect(screen.getByRole('button', { name: '提交模拟订单' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '核对订单结果' }));
  await screen.findByText(/尚未查到该订单/);
  fireEvent.click(screen.getByRole('button', { name: '重试原订单' }));
  await screen.findByText('模拟成交成功');
  expect(bodies).toEqual([bodies[0], bodies[0]]);
  expect(sessionStorage.getItem(attemptKey(value.data!.run_id!))).toBeNull();
  expect(sessionStorage.length).toBe(0);
});
it('提交瞬间再次检查快照年龄，过期时不发送订单', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('[]')));
  vi.stubGlobal('fetch', fetcher);
  const value = context({ updatedAt: Date.now() - 5000 });
  render(wrap(<TradingPage />, value));
  fireEvent.click(screen.getByRole('button', { name: '提交模拟订单' }));
  await screen.findByText('行情或连接状态已变化，请刷新后重新检查。');
  await waitFor(() => expect(fetcher.mock.calls.every(([url]) => url.startsWith('/api/orders?'))).toBe(true));
});
