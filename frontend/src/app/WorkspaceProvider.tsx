import { useEffect, useState, type ReactNode } from 'react';
import { WorkspaceContext } from './workspaceContext';
import { usePolling } from '../hooks/usePolling';
import type { Workspace } from '../api/types';

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const resource = usePolling<Workspace>('/workspace', 500);
  const [account, selectAccount] = useState<string>();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, []);
  const fresh = !!resource.data && !resource.error && now - resource.updatedAt < 3000;
  const selected = resource.data?.accounts.some(item => item.account_id === account) ? account : undefined;
  return <WorkspaceContext.Provider value={{ ...resource, fresh, account: selected, selectAccount }}>
    {children}
  </WorkspaceContext.Provider>;
}
