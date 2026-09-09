import type { components } from './schema';

export type SystemStatus = components['schemas']['SystemResponse'];

export async function fetchSystemStatus(signal: AbortSignal): Promise<SystemStatus> {
  const response = await fetch('/api/system', { signal });
  if (!response.ok) throw new Error(`服务请求失败（HTTP ${response.status}）`);
  return response.json() as Promise<SystemStatus>;
}
