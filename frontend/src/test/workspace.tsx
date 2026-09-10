import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router';
import { vi } from 'vitest';
import type { Workspace, DailyReport } from '../api/types';
import { WorkspaceContext, type WorkspaceContextValue } from '../app/workspaceContext';
import snapshot from './fixtures/workspace.json';
import frozen from './fixtures/report.json';

// Captured from the local ASGI app with a temporary database; no live services in unit tests.
export const workspace = snapshot as Workspace;
export const dailyReport = frozen as unknown as DailyReport;
export function context(overrides: Partial<WorkspaceContextValue> = {}): WorkspaceContextValue {
  return { data: structuredClone(workspace), fresh: true, loading: false, updatedAt: Date.now(),
    refresh: vi.fn(), selectAccount: vi.fn(), ...overrides };
}
export function wrap(children: ReactNode, value = context()) {
  return <MemoryRouter><WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider></MemoryRouter>;
}
export function browserMocks() {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn() }));
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => getStyle(element));
}
