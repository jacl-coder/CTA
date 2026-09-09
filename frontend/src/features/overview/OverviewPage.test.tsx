import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OverviewPage } from './OverviewPage';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('服务就绪状态', () => {
  it('后端可连接时仍显示明确的交易阻止原因', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      version: '0.1.0', stage: 'foundation', trading_available: false, reasons: ['行情尚未接入'],
    }))));
    render(<OverviewPage />);
    expect(await screen.findByText('后端已连接')).toBeInTheDocument();
    expect(screen.getByText('交易服务尚未就绪')).toBeInTheDocument();
    expect(screen.getByText('行情尚未接入')).toBeInTheDocument();
  });

  it('网络失败不会被显示成可交易状态', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('网络不可用')));
    render(<OverviewPage />);
    expect(await screen.findByText('无法连接服务')).toBeInTheDocument();
    expect(screen.queryByText('交易服务已就绪')).not.toBeInTheDocument();
  });
});
