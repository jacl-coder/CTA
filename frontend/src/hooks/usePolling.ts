import { useCallback, useEffect, useState } from 'react';
import { messageOf, request } from '../api/client';

type Resource<T> = { path: string; data?: T; error?: string; updatedAt: number; loading: boolean };

export function usePolling<T>(path: string | null, interval = 1000, refreshVersion = 0) {
  const [state, setState] = useState<Resource<T>>({ path: '', updatedAt: 0, loading: true });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    if (path === null) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const poll = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2500);
      try {
        const data = await request<T>(path, { signal: controller.signal });
        if (active) setState({ path, data, updatedAt: Date.now(), loading: false });
      } catch (error) {
        if (active) setState(previous => ({ path, data: previous.path === path ? previous.data : undefined,
          updatedAt: previous.path === path ? previous.updatedAt : 0, error: messageOf(error), loading: false }));
      } finally {
        clearTimeout(timeout);
        if (active && interval > 0) timer = setTimeout(poll, interval);
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); controller?.abort(); };
  }, [path, interval, revision, refreshVersion]);
  const current = state.path === path ? state : { updatedAt: 0, loading: path !== null, data: undefined, error: undefined };
  return { ...current, refresh };
}
