import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { request, writeApi } from '../../api/client';
import { browserMocks, wrap } from '../../test/workspace';
import example from '../../../../backend/src/cta_risk/replay/sample.json';
import result from '../../test/fixtures/replay-result.json';
import { ReplayPage } from './ReplayPage';
import { percentToRatio, ratioToPercent } from '../../components/percent';
vi.mock('../../api/client', async importOriginal => ({ ...await importOriginal<typeof import('../../api/client')>(), request: vi.fn(), writeApi: vi.fn() }));
beforeEach(() => { browserMocks(); vi.mocked(request).mockResolvedValue(structuredClone(example)); vi.mocked(writeApi).mockResolvedValue(result); });
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('载入历史后比较候选规则，逐步查看拒单并在编辑参数后清除旧结果', async () => {
  render(wrap(<ReplayPage />));
  fireEvent.click(screen.getByRole('button', { name: '载入两日历史样例' }));
  await screen.findByText(/两日历史样例：浮亏熔断/);
  fireEvent.change(screen.getByRole('textbox', { name: '候选熔断（%）' }), { target: { value: '5' } });
  fireEvent.click(screen.getByRole('button', { name: '运行规则对照' }));
  await screen.findByText(/原规则：1 次熔断/);
  expect(writeApi).toHaveBeenCalledWith('/replay/run', expect.objectContaining({
    dataset: expect.objectContaining({ policy: expect.objectContaining({ loss_ratio: '0.03' }) }),
    candidate_policy: expect.objectContaining({ loss_ratio: '0.05', warning_ratio: '0.02' }),
  }), 60000);
  expect(screen.getByRole('img', { name: /原规则与候选规则的浮亏比例历史/ })).toBeInTheDocument();
  for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole('button', { name: '下一步' }));
  expect(screen.getByText('原规则 · 当日浮亏熔断，禁止开仓')).toBeInTheDocument();
  expect(screen.getByText('候选规则 · 已模拟成交')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '候选熔断（%）' }), { target: { value: '4' } });
  expect(screen.queryByText('3. 全段验证结果')).not.toBeInTheDocument();
}, 10000);

it('读取当前历史失败时保留已选择的历史，并展示明确错误', async () => {
  render(wrap(<ReplayPage />));
  fireEvent.click(screen.getByRole('button', { name: '载入两日历史样例' }));
  await screen.findByText(/两日历史样例：浮亏熔断/);
  vi.mocked(request).mockRejectedValueOnce(new Error('历史超过 5000 步'));
  fireEvent.click(screen.getByRole('button', { name: '读取当前运行历史' }));
  await screen.findByText('历史超过 5000 步');
  expect(screen.getByText(/两日历史样例：浮亏熔断/)).toBeInTheDocument();
  expect(writeApi).not.toHaveBeenCalled();
});

it('导入文件由后端验证，格式错误不会替换已加载的数据', async () => {
  render(wrap(<ReplayPage />));
  vi.mocked(writeApi).mockRejectedValueOnce(new Error('历史输入格式非法'));
  const file = { size: 50, text: async () => '{"format_version":99}' };
  fireEvent.change(screen.getByLabelText('导入历史 JSON'), { target: { files: [file] } });
  await screen.findByText('历史输入格式非法');
  await waitFor(() => expect(writeApi).toHaveBeenCalledWith('/replay/validate', { format_version: 99 }, 15000));
  expect(screen.queryByText('2. 对照风控规则')).not.toBeInTheDocument();
});

it('百分比配置精确移动小数点，支持合法指数形式且拒绝非法输入', () => {
  expect(percentToRatio('3.125')).toBe('0.03125');
  expect(ratioToPercent('0.03125')).toBe('3.125');
  expect(ratioToPercent('1E-8')).toBe('0.000001');
  expect(ratioToPercent('1.0')).toBe('100');
  expect(() => percentToRatio('NaN')).toThrow();
  expect(() => percentToRatio('-3')).toThrow();
});
