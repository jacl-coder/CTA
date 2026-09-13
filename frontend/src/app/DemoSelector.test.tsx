import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DemoSelector } from './DemoSelector';
import { bindRun } from '../api/client';
import { browserMocks, context, wrap } from '../test/workspace';

beforeEach(browserMocks);
afterEach(() => { cleanup(); bindRun(undefined); vi.restoreAllMocks(); });

it('选择场景不会直接写入，点击进入后提交绑定原运行的切换请求', async () => {
  const value = context();
  value.data!.demo = { mode: 'continuous', initial_prices: {} };
  bindRun(value.data!.run_id!);
  const fetcher = vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(
    url === '/api/session' ? { token: 'test' } : { mode: 'manual', initial_prices: {} },
  )));
  vi.stubGlobal('fetch', fetcher);
  render(wrap(<DemoSelector />, value));
  expect(screen.getByRole('button', { name: '进入场景' })).toBeDisabled();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择演示场景' }));
  fireEvent.click(screen.getByText('手工演示'));
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '进入场景' }));
  await waitFor(() => expect(value.refresh).toHaveBeenCalled());
  const call = fetcher.mock.calls.find(([url]) => url === '/api/demo/switch')!;
  expect(JSON.parse(call[1].body)).toEqual({ mode: 'manual' });
  expect(call[1].headers['X-CTA-Run-Id']).toBe(value.data!.run_id);
});

it('连接过期时不允许重开场景，普通测试配置不显示场景选择', () => {
  const value = context({ fresh: false });
  value.data!.demo = { mode: 'fault', initial_prices: {} };
  const view = render(wrap(<DemoSelector />, value));
  expect(screen.getByRole('button', { name: '重新开始当前场景' })).toBeDisabled();
  value.data!.demo = null;
  view.rerender(wrap(<DemoSelector />, value));
  expect(screen.queryByRole('region', { name: '演示场景' })).not.toBeInTheDocument();
});
