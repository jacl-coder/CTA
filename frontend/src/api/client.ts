import type { components } from './schema';

let activeRun: string | undefined;
export function bindRun(run: string | undefined) { activeRun = run; }
export function runHeaders(): Record<string, string> {
  return activeRun ? { 'X-CTA-Run-Id': activeRun } : {};
}

export type SystemStatus = components['schemas']['SystemResponse'];

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function errorDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return;
  const detail = payload.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((item: { msg?: string }) => item.msg ?? '参数错误').join('；');
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { ...runHeaders(), ...options.headers }, cache: 'no-store' });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorDetail(payload) ?? `请求失败（HTTP ${response.status}）`);
  if (payload === null) throw new Error('服务返回的内容不是有效数据');
  return payload as T;
}

export async function writeApi<T>(path: string, body: unknown, timeout = 5000): Promise<T> {
  // Capture before fetching the token: a scenario may switch during that request.
  const scope = { 'X-CTA-Run-Id': activeRun ?? '' };
  const execute = async () => {
    const { token } = await request<{ token: string }>('/session', { signal: AbortSignal.timeout(5000) });
    return request<T>(path, { method: 'POST', headers: { ...scope, 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(body), signal: AbortSignal.timeout(timeout) });
  };
  try { return await execute(); }
  catch (error) {
    // 401 proves that this attempt was not accepted. Never automatically retry network failures.
    if (error instanceof ApiError && error.status === 401) return execute();
    throw error;
  }
}

export function messageOf(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === 'AbortError' || error.name === 'TimeoutError') return '连接超时，请确认服务状态';
    return error.message;
  }
  return '请求未完成，请稍后重试';
}
