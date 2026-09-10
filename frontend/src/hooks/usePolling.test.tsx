import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { usePolling } from './usePolling';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it('切换查询立即隐藏旧数据，迟到的旧响应不能覆盖新账户', async () => {
  let resolveOld!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => url === '/api/A'
    ? new Promise<Response>(resolve => { resolveOld = resolve; }) : Promise.resolve(new Response('{"account":"B"}'))));
  const { result, rerender } = renderHook(({ path }) => usePolling<{ account: string }>(path, 0), { initialProps: { path: '/A' } });
  rerender({ path: '/B' });
  expect(result.current.data).toBeUndefined();
  await waitFor(() => expect(result.current.data?.account).toBe('B'));
  await act(async () => { resolveOld(new Response('{"account":"A"}')); });
  expect(result.current.data?.account).toBe('B');
});
it('刷新失败保留上次数据及原始时间，并返回错误', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{"amount":"1.23"}')).mockRejectedValue(new Error('offline')));
  const { result } = renderHook(() => usePolling<{ amount: string }>('/workspace', 0));
  await waitFor(() => expect(result.current.data?.amount).toBe('1.23'));
  const timestamp = result.current.updatedAt;
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.error).toBe('offline'));
  expect(result.current.data?.amount).toBe('1.23');
  expect(result.current.updatedAt).toBe(timestamp);
});
