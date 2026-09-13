// Move the decimal point as text so configuration values never pass through binary floats.
export function percentToRatio(value: string): string {
  if (!/^\d+(\.\d{1,6})?$/.test(value)) throw new Error('比例请输入非负十进制数，最多六位小数');
  const [whole, fraction = ''] = value.split('.');
  const digits = whole.padStart(3, '0');
  return `${digits.slice(0, -2).replace(/^0+(?=\d)/, '')}.${digits.slice(-2)}${fraction}`;
}
export function ratioToPercent(value: string): string {
  const match = /^\+?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(value);
  if (!match) throw new Error('风险比例必须是非负十进制数');
  const exponent = Number(match[3] ?? 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 30) throw new Error('风险比例超出显示范围');
  const digits = match[1] + (match[2] ?? '');
  const point = match[1].length + exponent + 2;
  const integer = (point <= 0 ? '0' : digits.slice(0, point).padEnd(point, '0')).replace(/^0+(?=\d)/, '');
  const fraction = (point < 0 ? '0'.repeat(-point) + digits : digits.slice(point)).replace(/0+$/, '');
  return `${integer}${fraction ? `.${fraction}` : ''}`;
}
