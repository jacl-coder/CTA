import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Trade, Workspace } from '../../api/types';
import { WorkspaceContext, type WorkspaceContextValue } from '../../app/workspaceContext';
import { PositionsPage } from './PositionsPage';

const account = '账户 A&x=1+#';
const product = 'SHFE.rb&x=2';
const workspace = {
  instance_id: 'instance', run_id: 'run', revision: 1,
  capabilities: { ledger: true, trading: true, multi_source: false, manual_market: true, settlement: false },
  accounts: [{ account_id: account }, { account_id: 'B' }],
  instruments: [{ instrument_id: 'rb01', product_id: product }, { instrument_id: 'cu01', product_id: 'SHFE.cu' }],
  positions: [
    { account_id: account, instrument_id: 'rb01', product_id: product, side: 'LONG', quantity: 2, today_quantity: 1, yesterday_quantity: 1 },
    { account_id: 'B', instrument_id: 'cu01', product_id: 'SHFE.cu', side: 'SHORT', quantity: 3, today_quantity: 3, yesterday_quantity: 0 },
  ],
} as Workspace;

function context(selected: string | undefined = account): WorkspaceContextValue {
  return { data: workspace, account: selected, fresh: true, updatedAt: Date.now(), loading: false,
    refreshVersion: 0, refresh: vi.fn(), selectAccount: vi.fn() };
}
function page(value = context()) {
  return <WorkspaceContext.Provider value={value}><PositionsPage /></WorkspaceContext.Provider>;
}
function trades(count: number, selected = account, prefix = 'fill'): Trade[] {
  return Array.from({ length: count }, (_, index) => ({ fill_id: `${prefix}-${index}`, account_id: selected,
    instrument_id: 'rb01', trading_day: '2026-09-10', sequence: index + 1, side: 'LONG', offset: 'OPEN',
    quantity: 1, price: '9007199254740993.123456789', fee: '0.000000001', source: 'test' }));
}
async function chooseProduct() {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '品种筛选' }));
  fireEvent.click(await screen.findByText(product, { selector: '.ant-select-item-option-content' }));
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn() }));
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('持仓与真实成交分页', () => {
  it('持仓应用全局账户和品种筛选；汇总查询不携带账户', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('[]'));
    vi.stubGlobal('fetch', fetcher);
    render(page());
    expect(screen.getByText('rb01')).toBeInTheDocument();
    expect(screen.queryByText('cu01')).not.toBeInTheDocument();
    await chooseProduct();
    fireEvent.click(screen.getByRole('tab', { name: '跨账户汇总' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    const url = new URL(fetcher.mock.calls[0][0], 'http://localhost');
    expect(url.pathname).toBe('/api/positions/summary');
    expect(url.searchParams.get('product_id')).toBe(product);
    expect(url.searchParams.has('account_id')).toBe(false);
    expect(await screen.findByText('暂无汇总持仓')).toBeInTheDocument();
  });

  it('取21条仅展示20条；翻页、切换筛选时立即移除旧行并重置服务端页码', async () => {
    let resolveNext!: (response: Response) => void;
    const next = new Promise<Response>(resolve => { resolveNext = resolve; });
    const fetcher = vi.fn().mockImplementation((path: string) => {
      const url = new URL(path, 'http://localhost');
      if (url.searchParams.get('account_id') === 'B') return Promise.resolve(new Response(JSON.stringify(trades(1, 'B', 'new-account'))));
      if (url.searchParams.get('offset') === '20') return next;
      return Promise.resolve(new Response(JSON.stringify(trades(21))));
    });
    vi.stubGlobal('fetch', fetcher);
    const view = render(page());
    await chooseProduct();
    fireEvent.click(screen.getByRole('tab', { name: '成交记录' }));
    expect(await screen.findByText('fill-0')).toBeInTheDocument();
    expect(screen.queryByText('fill-20')).not.toBeInTheDocument();
    expect(screen.getAllByText('9,007,199,254,740,993.123456789')).toHaveLength(20);
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.queryByText('fill-0')).not.toBeInTheDocument();
    await waitFor(() => expect(fetcher.mock.calls.some(([path]) => new URL(path, 'http://localhost').searchParams.get('offset') === '20')).toBe(true));
    const request = new URL(fetcher.mock.calls.at(-1)![0], 'http://localhost');
    expect(Object.fromEntries(request.searchParams)).toEqual({ limit: '21', offset: '20', account_id: account, product_id: product });
    resolveNext(new Response(JSON.stringify(trades(1, account, 'second'))));
    expect(await screen.findByText('second-0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    view.rerender(page(context('B')));
    expect(screen.queryByText('second-0')).not.toBeInTheDocument();
    expect(await screen.findByText('new-account-0')).toBeInTheDocument();
    expect(screen.getByText('第 1 页 · 每页 20 条')).toBeInTheDocument();
    const changed = new URL(fetcher.mock.calls.at(-1)![0], 'http://localhost');
    expect(changed.searchParams.get('offset')).toBe('0');
    expect(changed.searchParams.get('product_id')).toBe(product);
  });

  it('刷新失败保留成交并明确标旧；初次失败不伪装为空记录', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(trades(1))))
      .mockRejectedValue(new Error('连接断开'));
    vi.stubGlobal('fetch', fetcher);
    const view = render(page());
    fireEvent.click(screen.getByRole('tab', { name: '成交记录' }));
    expect(await screen.findByText('fill-0')).toBeInTheDocument();
    view.rerender(page({ ...context(), refreshVersion: 1 }));
    expect(await screen.findByText('旧数据：刷新失败或已过期')).toBeInTheDocument();
    expect(screen.getByText('fill-0')).toBeInTheDocument();
    view.rerender(page(context('B')));
    expect(await screen.findByText('查询失败')).toBeInTheDocument();
    expect(screen.queryByText('fill-0')).not.toBeInTheDocument();
    expect(screen.queryByText('当前筛选下暂无成交记录')).not.toBeInTheDocument();
  });
});
