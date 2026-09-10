import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { browserMocks, context, dailyReport, wrap } from '../../test/workspace';
import { ReportsPage } from './ReportsPage';

beforeEach(browserMocks);
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('收盘缺帧禁止清算，补齐后提交冻结计划价格', async () => {
  const value = context();
  value.data!.settlement!.phase = 'CLOSING';
  value.data!.settlement!.applied_sequence = 0;
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(url === '/api/session' ? '{"token":"local"}' : '{}')));
  vi.stubGlobal('fetch', fetcher);
  const view = render(wrap(<ReportsPage />, value));
  expect(screen.getByRole('button', { name: '按计划结算价清算' })).toBeDisabled();
  value.data!.settlement!.applied_sequence = 1;
  value.data!.market!.prices['SHFE.rb2610'] = '9999';
  view.rerender(wrap(<ReportsPage />, { ...value }));
  fireEvent.click(screen.getByRole('button', { name: '按计划结算价清算' }));
  await screen.findByText('全账户清算已提交。');
  const call = fetcher.mock.calls.find(([url]) => url === '/api/settlement/settle') as unknown as [string, RequestInit];
  expect(JSON.parse(call[1].body as string)).toEqual({ trading_day: '2026-09-09', prices: value.data!.settlement_days[0].prices });
});
it('日报展示冻结结果和账户筛选，文件失败不显示下载成功', async () => {
  const value = context({ account: 'A' });
  value.data!.settlement!.settled_days = ['2026-09-09'];
  value.data!.settlement!.phase = 'SETTLED';
  value.data!.risks[0].equity = '999999';
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(
    url.endsWith('/download/html') ? new Response('{"detail":"文件导出失败"}', { status: 503 }) : new Response(JSON.stringify(dailyReport)),
  )));
  render(wrap(<ReportsPage />, value));
  await screen.findByText('100,292.00');
  expect(screen.queryByText('999,999.00')).not.toBeInTheDocument();
  expect(screen.queryByText('197,994.00')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下载 HTML' }));
  await screen.findByText('文件导出失败');
  expect(screen.queryByText('HTML 日报已下载')).not.toBeInTheDocument();
});
