import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { App } from './App';
import { browserMocks, dailyReport, workspace } from '../test/workspace';

beforeEach(() => { browserMocks(); vi.spyOn(window, 'scrollTo').mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('唯一顶部刷新会重读失败的冻结日报及工作台，保留当前账户且不触发写操作', async () => {
  const snapshot = structuredClone(workspace);
  snapshot.settlement!.settled_days = ['2026-09-09'];
  let reportRequests = 0;
  const fetcher = vi.fn().mockImplementation((url: string) => {
    if (url === '/api/workspace') return Promise.resolve(new Response(JSON.stringify(snapshot)));
    if (url === '/api/reports/2026-09-09') {
      reportRequests += 1;
      return reportRequests === 1 ? Promise.reject(new Error('temporary failure'))
        : Promise.resolve(new Response(JSON.stringify(dailyReport)));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MemoryRouter initialEntries={['/reports']}><App /></MemoryRouter>);
  await screen.findByText('日报读取失败');
  expect(screen.getAllByRole('button', { name: /刷新|重新读取/ })).toHaveLength(1);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '策略账户筛选' }));
  fireEvent.click(await screen.findByText('账户 A', { selector: '.ant-select-item-option-content' }));
  const priorWorkspaceReads = fetcher.mock.calls.filter(([url]) => url === '/api/workspace').length;
  fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
  await screen.findByText('100,292.00');
  expect(screen.queryByText('197,994.00')).not.toBeInTheDocument();
  expect(reportRequests).toBe(2);
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url === '/api/workspace').length).toBeGreaterThan(priorWorkspaceReads));
  expect(fetcher.mock.calls.every(([url]) => url === '/api/workspace' || url === '/api/reports/2026-09-09')).toBe(true);
});
