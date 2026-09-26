/** Exact decimal-string arithmetic for uint256 values, the way the server does it. */

export const DECIMAL_RE = /^\d+(\.\d+)?$/;

/** `raw ÷ 10^decimals` as a plain decimal string: ("397092712", 6) → "397.092712". */
export function scaled(raw: string, decimals: number): string {
  const digits = BigInt(raw).toString();
  if (decimals === 0) return digits;
  const padded = digits.padStart(decimals + 1, '0');
  const whole = padded.slice(0, -decimals);
  const fraction = padded.slice(-decimals).replace(/0+$/, '');
  return fraction ? `${whole}.${fraction}` : whole;
}

/** Compares `raw ÷ 10^decimals` with a decimal string such as "250" or "0.5": -1, 0 or 1. */
export function compareScaled(raw: string, decimals: number, threshold: string): number {
  const [whole, fraction = ''] = threshold.split('.');
  const left = BigInt(raw) * 10n ** BigInt(fraction.length);
  const right = BigInt(whole + fraction) * 10n ** BigInt(decimals);
  return left < right ? -1 : left > right ? 1 : 0;
}

/** "1.5" shifted by 10^3 → "1500"; normalises "0010" → "10" and "2.50" → "2.5". */
export function shiftDecimal(value: string, power: number): string {
  const [whole, fraction = ''] = value.split('.');
  const point = whole.length + power;
  const digits = (whole + fraction).padEnd(point, '0');
  const integer = digits.slice(0, point).replace(/^0+(?=\d)/, '') || '0';
  const rest = digits.slice(point).replace(/0+$/, '');
  return rest ? `${integer}.${rest}` : integer;
}
