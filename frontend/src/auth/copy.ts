/**
 * Pure helpers for the signed-out pages. The server is the authority on every
 * rule; these only shape what the UI shows before and after it answers.
 * Run with `npm test`.
 */

export type AuthField = 'username' | 'password' | 'email';

/** Which field a server error belongs to. Anything else lands in the panel's banner. */
const FIELD_FOR_CODE: Record<string, AuthField> = {
  invalid_username: 'username',
  username_taken: 'username',
  weak_password: 'password',
  invalid_email: 'email',
};

export function fieldForCode(code: string): AuthField | null {
  return FIELD_FOR_CODE[code] ?? null;
}

/** Headline for a banner error, by code. The server's own sentence always goes under it. */
export function bannerHeading(code: string): string {
  if (code === 'invalid_credentials') return 'LOGIN INCORRECT';
  if (code === 'rate_limited') return 'TOO MANY ATTEMPTS';
  if (code === 'csrf_failed') return 'SESSION CHECK FAILED';
  return 'NO SIGNAL';
}

/** The message on any thrown value, with a plain fallback for network failures. */
export function messageOf(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : 'Something went wrong. Check your connection and try again.';
}

export function minutesFrom(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}

/** Deliberately permissive: this only catches empty and obviously malformed input. */
export function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

/** Mirrors services.accounts: 3 to 150 characters of letters, digits, '.', '_' and '-'. */
export const USERNAME_MIN = 3;
export const USERNAME_MAX = 150;
const USERNAME_PATTERN = /^[A-Za-z0-9._-]+$/;

/** Guidance shown after the field is left; the server still decides. */
export function usernameProblem(value: string): string | null {
  const name = value.trim();
  if (!name) return 'Choose a username.';
  if (name.includes('@')) return "Usernames can't contain @. Email addresses sign in by link instead.";
  if (name.length < USERNAME_MIN || name.length > USERNAME_MAX) {
    return `Usernames are ${USERNAME_MIN} to ${USERNAME_MAX} characters.`;
  }
  if (!USERNAME_PATTERN.test(name)) return "Usernames may use letters, numbers, '.', '_' and '-' only.";
  return null;
}

/** How long the picture takes to roll and lock after a successful sign-in. */
export const LOCK_MS = 900;

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
