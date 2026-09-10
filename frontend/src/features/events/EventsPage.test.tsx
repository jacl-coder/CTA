import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RiskEvent, Workspace } from '../../api/types';
import { WorkspaceContext, type WorkspaceContextValue } from '../../app/workspaceContext';
import { EventsPage } from './EventsPage';

const account = 'A &+#账户';
const workspace = { instance_id: 'instance', run_id: 'run', capabilities: { trading: true },
  accounts: [{ account_id: account }, { account_id: 'B' }] } as Workspace;
function page(selected = account, refreshVersion = 0) {
  const value: WorkspaceContextValue = { data: workspace, account: selected, loading: false,
    fresh: true, updatedAt: Date.now(), refreshVersion, refresh: vi.fn(), selectAccount: vi.fn() };
  return <WorkspaceContext.Provider value={value}><EventsPage /></WorkspaceContext.Provider>;
}
const events: RiskEvent[] = Array.from({ length: 21 }, (_, index) => ({ event_id: index + 1,
  account_id: account, frame_sequence: index + 10, kind: index === 1 ? 'LOSS_WARNING_EXIT' : 'LOSS_WARNING_ENTER',
  product_id: null, trading_day: '2026-09-10', occurred_ms: 1789000000000, detected_ms: 1789000005000, recovered: index === 0 }));

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn() }));
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('风险事件', () => {
  it('保留每次状态转换、补数发现与原发生帧，使用真实分页和安全账户参数', async () => {
    const fetcher = vi.fn().mockImplementation((path: string) => {
      const url = new URL(path, 'http://localhost');
      const rows = url.searchParams.get('account_id') === 'B' ? []
        : url.searchParams.get('offset') === '20' ? events.slice(20) : events;
      return Promise.resolve(new Response(JSON.stringify(rows)));
    });
    vi.stubGlobal('fetch', fetcher);
    const view = render(page());
    expect(await screen.findByText('补数发现', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(screen.getByText('浮亏告警解除')).toBeInTheDocument();
    expect(screen.getAllByText('浮亏告警', { selector: '.ant-tag' })).toHaveLength(19);
    expect(screen.getAllByText('正常发现', { selector: '.ant-tag' })).toHaveLength(19);
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.queryByText('浮亏告警解除')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText('浮亏告警', { selector: '.ant-tag' })).toHaveLength(1));
    const url = new URL(fetcher.mock.calls.at(-1)![0], 'http://localhost');
    expect(url.pathname).toBe('/api/risk-events');
    expect(Object.fromEntries(url.searchParams)).toEqual({ account_id: account, limit: '21', offset: '20' });
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    view.rerender(page('B'));
    expect(screen.queryByText('浮亏告警', { selector: '.ant-tag' })).not.toBeInTheDocument();
    expect(await screen.findByText('当前筛选下暂无风险事件')).toBeInTheDocument();
    expect(screen.getByText('第 1 页 · 每页 20 条')).toBeInTheDocument();
    expect(new URL(fetcher.mock.calls.at(-1)![0], 'http://localhost').searchParams.get('offset')).toBe('0');
  });

  it('失败显示错误；已有事件刷新失败时保留并标旧', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('服务不可用'))
      .mockResolvedValueOnce(new Response(JSON.stringify(events.slice(0, 1))))
      .mockRejectedValue(new Error('刷新超时'));
    vi.stubGlobal('fetch', fetcher);
    const view = render(page());
    expect(await screen.findByText('风险事件查询失败')).toBeInTheDocument();
    expect(screen.queryByText('当前筛选下暂无风险事件')).not.toBeInTheDocument();
    view.rerender(page(account, 1));
    expect(await screen.findByText('浮亏告警', { selector: '.ant-tag' })).toBeInTheDocument();
    view.rerender(page(account, 2));
    expect(await screen.findByText('旧数据：刷新失败或已过期')).toBeInTheDocument();
    expect(screen.getByText('浮亏告警', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  });
});
