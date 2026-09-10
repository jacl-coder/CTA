import type { OrderInput } from '../../api/types';

export function attemptKey(run: string) { return `cta-pending-order:${run}`; }
export function readAttempt(run: string): OrderInput | undefined {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(attemptKey(run)) ?? 'null');
    if (!raw || typeof raw !== 'object') return;
    const item = raw as Partial<OrderInput>;
    if (typeof item.request_id === 'string' && typeof item.account_id === 'string' && typeof item.instrument_id === 'string'
      && typeof item.trading_day === 'string' && (item.side === 'LONG' || item.side === 'SHORT')
      && ['OPEN', 'CLOSE_TODAY', 'CLOSE_YESTERDAY'].includes(item.offset ?? '')
      && Number.isSafeInteger(item.quantity) && item.quantity! > 0) return item as OrderInput;
  } catch { /* Storage may be unavailable; same-page retry still keeps the request identity. */ }
}
export function saveAttempt(run: string, value?: OrderInput) {
  try {
    if (value) sessionStorage.setItem(attemptKey(run), JSON.stringify(value));
    else sessionStorage.removeItem(attemptKey(run));
  } catch { /* No credentials or financial authority are stored here. */ }
}
