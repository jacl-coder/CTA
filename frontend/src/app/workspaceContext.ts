import { createContext, useContext } from 'react';
import type { Workspace } from '../api/types';

export type WorkspaceContextValue = {
  data?: Workspace; error?: string; loading: boolean; updatedAt: number;
  fresh: boolean; refresh: () => void; account?: string; selectAccount: (account?: string) => void;
  refreshVersion: number;
};
export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('页面需要工作台数据上下文');
  return context;
}
