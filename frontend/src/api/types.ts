/** Mirrors the DRF serializers in project/app/serializers/ (frozen contract). */

// ===== magic-link auth =============================================

export interface AuthMe {
  authenticated: boolean;
  email: string | null;
}

export interface AuthRequestLinkInput {
  email: string;
}

export interface AuthRequestLinkResult {
  status: 'sent';
  expires_in: number;          // seconds, e.g. 900
  resend_after: number;        // seconds, e.g. 30
  dev_link: string | null;     // non-null ONLY in DEBUG + console delivery
}

export interface AuthConsumeInput {
  token: string;
}

export interface AuthConsumeResult {
  authenticated: true;
  email: string;
  session_expires_at: string;  // ISO 8601
}

// ===== username/password auth =====================================

export interface AuthRegisterInput {
  username: string;
  password: string;
  /** Optional and unverified; not used for sign-in yet. */
  email?: string;
}

export interface AuthPasswordLoginInput {
  username: string;
  password: string;
}

export interface AuthPasswordResult {
  authenticated: true;
  username: string;
  email: string | null;
  session_expires_at: string;  // ISO 8601
}

/** Every non-2xx body in this API. `detail` is always present. */
export interface ApiErrorBody {
  code: ApiErrorCode;
  detail: string;
  retry_after?: number;
}

export type ApiErrorCode =
  | 'invalid_email'
  | 'invalid_token'
  | 'expired_token'
  | 'invalid_username'
  | 'weak_password'
  | 'username_taken'
  | 'invalid_credentials'
  | 'validation_error'
  | 'not_authenticated'
  | 'csrf_failed'
  | 'not_found'
  | 'method_not_allowed'
  | 'rate_limited';
