/** Mirrors the DRF serializers in project/app/serializers/ (frozen contract). */

/**
 * The demo shape's columns. The server guarantees none of them — a user
 * declares what a lead is (`GET /api/shape/`) — so every field is optional.
 *
 * `hubspot_notes` is lead-controlled text. It is carried here for completeness
 * but must never be interpolated into a prompt on this side of the wire.
 */
export interface LeadData {
  agency_name?: string;
  contact_name?: string;
  contact_email?: string;
  contact_phone?: string;
  state?: string;
  stage?: string;
  num_producers?: number;
  years_in_business?: number;
  estimated_book_size_usd?: number;
  quotes_created?: number;
  quotes_submitted?: number;
  deals_closed?: number;
  /** DRF DateFields: "YYYY-MM-DD", or null when never recorded. */
  signed_up_date?: string | null;
  last_login_date?: string | null;
  last_contacted_date?: string | null;
  hubspot_notes?: string;
}

/**
 * Mirrors `LeadSerializer` (`fields = "__all__"`) — what `GET /api/leads/`
 * returns. Distinct from `Lead` above, which is the compact nested form.
 */
export interface LeadRecord {
  id: string;
  owner: number | null;
  data: LeadData;
}

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
  | 'validation_error'
  | 'not_authenticated'
  | 'csrf_failed'
  | 'not_found'
  | 'method_not_allowed'
  | 'rate_limited';
