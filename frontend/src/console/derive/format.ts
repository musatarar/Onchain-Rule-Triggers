/** Display formatting. Amounts stay strings end to end: nothing here goes through a float. */

/** Groups the integer part only: "1234567.891" → "1,234,567.891". */
export function groupDigits(value: string): string {
  const [whole, fraction] = value.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction === undefined ? grouped : `${grouped}.${fraction}`;
}

export const short = (address: string | null | undefined): string =>
  address ? `${address.slice(0, 6)}…${address.slice(-4)}` : '—';

/**
 * `raw ÷ 10^decimals`, grouped, cut (not rounded) to `maxFraction` digits. Below 1 it
 * keeps two significant digits, so 0.000123 does not read as 0.
 */
export function formatUnits(raw: string, decimals: number, maxFraction = 2): string {
  const digits = BigInt(raw).toString();
  const padded = decimals ? digits.padStart(decimals + 1, '0') : digits;
  const whole = decimals ? padded.slice(0, -decimals) : padded;
  let fraction = decimals ? padded.slice(-decimals) : '';
  let keep = maxFraction;
  if (/^0+$/.test(whole) && /[1-9]/.test(fraction)) keep = Math.max(maxFraction, fraction.search(/[1-9]/) + 2);
  fraction = fraction.slice(0, keep).replace(/0+$/, '');
  return groupDigits(whole) + (fraction ? `.${fraction}` : '');
}

/** An unscaled amount in a journal row: every digit up to 9, then "4.332e10". */
export function rawMagnitude(raw: string): string {
  return raw.length <= 9 ? groupDigits(raw) : `${raw[0]}.${raw.slice(1, 4)}e${raw.length - 1}`;
}

/** "2023-08-26T16:22:23Z" → "16:22:23". */
export const utcClock = (iso: string): string => new Date(iso).toISOString().slice(11, 19);

/** "2023-08-26T16:22:23Z" → "2023-08-26 16:22:23 UTC". */
export const utcDateTime = (iso: string): string =>
  `${new Date(iso).toISOString().slice(0, 19).replace('T', ' ')} UTC`;

export const plural = (count: number, one: string, many = `${one}s`): string => (count === 1 ? one : many);
