import { getJson, postJson } from './client';
import type {
  AuthConsumeInput,
  AuthConsumeResult,
  AuthMe,
  AuthRequestLinkInput,
  AuthRequestLinkResult,
  LeadRecord,
} from './types';

// ===== leads =======================================================

/** Every lead in the book, full records (`LeadSerializer`). */
export const fetchLeads = () => getJson<LeadRecord[]>('/api/leads/');

// ===== magic-link auth =============================================

export const fetchAuthMe = () => getJson<AuthMe>('/api/auth/me/');

export const requestLoginLink = (body: AuthRequestLinkInput) =>
  postJson<AuthRequestLinkResult>('/api/auth/request-link/', body);

export const consumeLoginToken = (body: AuthConsumeInput) =>
  postJson<AuthConsumeResult>('/api/auth/consume/', body);

export const logout = () => postJson<void>('/api/auth/logout/', {});
