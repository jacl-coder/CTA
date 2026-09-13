import { afterEach, expect, it, vi } from 'vitest';
import { bindRun, writeApi } from './client';

afterEach(() => { bindRun(undefined); vi.restoreAllMocks(); });
it('获取新令牌期间切换场景仍然发送原运行编号', async () => {
  bindRun('old-run');
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url === '/api/session') {
      bindRun('new-run');
      return new Response(JSON.stringify({ token: 'new-token' }));
    }
    return new Response(JSON.stringify({ detail: '场景已切换' }), { status: 409 });
  });
  vi.stubGlobal('fetch', fetcher);
  await expect(writeApi('/orders', { request_id: 'old-order' })).rejects.toThrow('场景已切换');
  expect(fetcher.mock.calls[1][1].headers['X-CTA-Run-Id']).toBe('old-run');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
