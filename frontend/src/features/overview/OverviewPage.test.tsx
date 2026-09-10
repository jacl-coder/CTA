import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { browserMocks, context, wrap } from '../../test/workspace';
import { OverviewPage } from './OverviewPage';

beforeEach(browserMocks);
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('账户筛选独立于全账户指标，金额保持十进制精度', () => {
  const value = context({ account: 'A' });
  value.data!.totals.equity = '9007199254740993.123456789';
  render(wrap(<OverviewPage />, value));
  expect(screen.getByText('9,007,199,254,740,993.123456789')).toBeInTheDocument();
  expect(screen.getByText('账户 A')).toBeInTheDocument();
  expect(screen.queryByText('账户 B')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: '持仓' }));
  expect(value.selectAccount).toHaveBeenCalledWith('A');
});
it('无报价不会伪装成零，断线保留估值但禁止显示可开仓', () => {
  const value = context({ fresh: false });
  value.data!.totals.equity = null;
  render(wrap(<OverviewPage />, value));
  expect(screen.getByText('等待有效报价')).toBeInTheDocument();
  expect(screen.getAllByText('历史行情估值')).toHaveLength(3);
  expect(screen.queryByText('可申请开仓')).not.toBeInTheDocument();
  expect(screen.getAllByText('暂不可开仓')).toHaveLength(3);
});
it('勾选两个账户后可以集中对比', () => {
  render(wrap(<OverviewPage />));
  const boxes = screen.getAllByRole('checkbox');
  fireEvent.click(boxes[1]); fireEvent.click(boxes[3]);
  fireEvent.click(screen.getByRole('switch', { name: '仅比较所选账户' }));
  expect(screen.getByText('账户 A')).toBeInTheDocument();
  expect(screen.getByText('账户 C')).toBeInTheDocument();
  expect(screen.queryByText('账户 B')).not.toBeInTheDocument();
});
