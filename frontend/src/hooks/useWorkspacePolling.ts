import { useWorkspace } from '../app/workspaceContext';
import { usePolling } from './usePolling';

// The shared refresh also reloads mounted page queries, including non-polling reports.
export function useWorkspacePolling<T>(path: string | null, interval = 1000) {
  const { refreshVersion } = useWorkspace();
  return usePolling<T>(path, interval, refreshVersion);
}
