import { expect, it } from 'vitest';
import { recordTimeLabel } from './format';

it('记录时间固定使用北京时间，保留日期和毫秒，与模拟交易日无关', () => {
  expect(recordTimeLabel(Date.parse('2026-09-10T16:00:00.123Z'))).toBe('2026-09-11 00:00:00.123');
  expect(recordTimeLabel(0)).toBe('1970-01-01 08:00:00.000');
});

it('缺失或无效时间不伪造为当前时间或零点', () => {
  expect(recordTimeLabel(undefined)).toBe('未记录');
  expect(recordTimeLabel(null)).toBe('未记录');
  expect(recordTimeLabel(NaN)).toBe('未记录');
});
