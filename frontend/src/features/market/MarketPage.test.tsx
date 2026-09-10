import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Workspace } from '../../api/types';
import { WorkspaceContext } from '../../app/workspaceContext';
import { MarketPage } from './MarketPage';

function workspace(): Workspace {
  return {
    instance_id: 'instance', run_id: 'run', revision: 1, server_time_ms: 1789000000000,
    accounts: [], positions: [], risks: [], settlement: null, settlement_days: [], automatic_settlement: false,
    system: { stage: 'trading', version: 'test', ledger_available: true, trading_available: true, reasons: [] },
    totals: { equity: null, floating_pnl: null, margin: null, available_funds: null, gross_exposure: null },
    capabilities: { ledger: true, trading: true, manual_market: false, multi_source: true, settlement: false },
    instruments: [
      { instrument_id: 'rb01', product_id: 'SHFE.rb', multiplier: 10, tick_size: '1', margin_rate: '0.1', fee_per_lot: '1' },
      { instrument_id: 'cu01', product_id: 'SHFE.cu', multiplier: 5, tick_size: '10', margin_rate: '0.1', fee_per_lot: '1' },
    ],
    market: { sequence: 12, trading_day: '2026-09-10', source: 'merged', market_ready: true,
      prices: { rb01: '9007199254740993.123456789', extra: '0.000000001' } },
    sources: { applied_sequence: 12, expected_sequence: 24, market_ready: true, completed: false,
      duplicate_frames: 3, epoch_ms: 1789000000000, reason: '',
      sources: [{ source_id: 'source-A', connected: true, head: 24, contiguous_sequence: 12, received_count: 18, reason: '' }] },
  };
}
function page(data = workspace(), fresh = true, error?: string) {
  return <WorkspaceContext.Provider value={{ data, fresh, error, loading: false, updatedAt: Date.now(),
    refresh: vi.fn(), selectAccount: vi.fn() }}><MarketPage /></WorkspaceContext.Provider>;
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn() }));
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('只读行情监控', () => {
  it('完整价格表保留Decimal精度与缺失价格，进度仅按序号计算', () => {
    render(page());
    expect(screen.getByText('9,007,199,254,740,993.123456789')).toBeInTheDocument();
    expect(screen.getByText('0.000000001')).toBeInTheDocument();
    expect(screen.getByText('cu01').closest('tr')).toHaveTextContent('—');
    expect(screen.getByText('extra')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText('已连接')).toBeInTheDocument();
    expect(screen.getByText('未完成')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('market_ready=false及完成场景只标历史；快照失败不继续宣称当前连接正常', () => {
    const data = workspace();
    data.market!.market_ready = false;
    const view = render(page(data));
    expect(screen.getByText('历史快照 / 不可交易')).toBeInTheDocument();
    expect(screen.queryByText('行情已就绪')).not.toBeInTheDocument();
    data.sources!.completed = true;
    view.rerender(page(data));
    expect(screen.getByText('行情场景已完成')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    view.rerender(page(data, false, '网络断开'));
    expect(screen.getByText('工作台快照已过期，正在展示旧数据')).toBeInTheDocument();
    expect(screen.getByText('上次已连接')).toBeInTheDocument();
    expect(screen.queryByText('已连接')).not.toBeInTheDocument();
    expect(screen.getByText('9,007,199,254,740,993.123456789')).toBeInTheDocument();
  });

  it('手工模式明确手工快照且没有价格输入或行情源假数据', () => {
    const data = workspace();
    data.capabilities.manual_market = true;
    data.capabilities.multi_source = false;
    data.sources = null;
    render(page(data));
    expect(screen.getByText('手工价格模式')).toBeInTheDocument();
    expect(screen.getByText('手工价格已就绪')).toBeInTheDocument();
    expect(screen.getByText('手工发布')).toBeInTheDocument();
    expect(screen.queryByText('行情源与应用进度')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  });
});
