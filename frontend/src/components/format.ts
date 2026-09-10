// Financial amounts stay strings. This formatter only inserts separators, never rounds or sums.
export function money(value: string | null | undefined): string {
  if (value == null) return '—';
  if (!/^-?\d+(\.\d+)?$/.test(value)) return value;
  const [whole, fraction] = value.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${(fraction ?? '').padEnd(2, '0')}`;
}
export function amountTone(value: string | null | undefined): string {
  return value && /^-/.test(value) && !/^-0(\.0+)?$/.test(value) ? 'negative' : '';
}
export const sideLabel = (side: string) => side === 'LONG' ? '多头' : '空头';
export const offsetLabel = (offset: string) => ({ OPEN: '开仓', CLOSE_TODAY: '平今', CLOSE_YESTERDAY: '平昨' }[offset] ?? offset);
export const phaseLabel = (phase?: string) => ({ OPEN: '盘中', CLOSING: '封账补数中', SETTLED: '已清算' }[phase ?? ''] ?? '未启用日结');
const reasons: Record<string, string> = {
  MARKET_UNAVAILABLE: '行情未就绪或已过期', CIRCUIT_BROKEN: '当日浮亏熔断，禁止开仓', EXPOSURE_LIMIT: '品种敞口超限',
  PROJECTED_EXPOSURE_LIMIT: '开仓后预计敞口超限', INSUFFICIENT_MARGIN: '预计可用资金不足',
  TRADING_DAY_MISMATCH: '订单交易日不匹配', INVALID_POSITION: '指定方向或今昨仓可平数量不足',
  TRADING_DAY_CLOSED: '交易日已封账或清算', EXECUTED: '已模拟成交',
  LOSS_WARNING_ENTER: '浮亏告警', LOSS_WARNING_EXIT: '浮亏告警解除', CIRCUIT_BREAK: '触发当日熔断',
  EXPOSURE_LIMIT_ENTER: '进入敞口限制', EXPOSURE_LIMIT_EXIT: '敞口限制解除',
};
export const reasonLabel = (reason: string) => reasons[reason] ?? reason;
export const timeLabel = (timestamp?: number | null) => timestamp == null ? '—' : new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false });
