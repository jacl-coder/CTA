import { useEffect, useState } from 'react';
import { fetchSystemStatus, type SystemStatus } from '../api/client';

type Connection =
  | { kind: 'loading' }
  | { kind: 'connected'; data: SystemStatus }
  | { kind: 'error'; message: string };

export function useSystemStatus() {
  const [connection, setConnection] = useState<Connection>({ kind: 'loading' });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    let active = true;
    fetchSystemStatus(controller.signal)
      .then((data) => { if (active) setConnection({ kind: 'connected', data }); })
      .catch((error: unknown) => {
        if (active) setConnection({ kind: 'error', message: error instanceof Error && error.name !== 'AbortError'
          ? error.message : '连接超时，请确认服务已启动。' });
      })
      .finally(() => window.clearTimeout(timer));
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [revision]);
  return {
    connection,
    refresh: () => { setConnection({ kind: 'loading' }); setRevision((value) => value + 1); },
  };
}
