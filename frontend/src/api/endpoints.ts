import { getJson, postJson } from './client';
import type {
  AuthConsumeInput,
  AuthConsumeResult,
  AuthMe,
  AuthPasswordLoginInput,
  AuthPasswordResult,
  AuthRegisterInput,
  AuthRequestLinkInput,
  AuthRequestLinkResult,
} from './types';

// The console's calls live in console/api/http.ts, one method per contract endpoint.

// ===== magic-link auth =============================================

export const fetchAuthMe = () => getJson<AuthMe>('/api/auth/me/');

export const requestLoginLink = (body: AuthRequestLinkInput) =>
  postJson<AuthRequestLinkResult>('/api/auth/request-link/', body);

export const consumeLoginToken = (body: AuthConsumeInput) =>
  postJson<AuthConsumeResult>('/api/auth/consume/', body);

export const logout = () => postJson<void>('/api/auth/logout/', {});

// ===== username/password auth ======================================

export const registerAccount = (body: AuthRegisterInput) =>
  postJson<AuthPasswordResult>('/api/auth/register/', body);

export const loginWithPassword = (body: AuthPasswordLoginInput) =>
  postJson<AuthPasswordResult>('/api/auth/login/', body);
