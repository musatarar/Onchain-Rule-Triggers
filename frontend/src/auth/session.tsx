import { createContext, useContext } from 'react';

/**
 * Who is signed in, as `/api/auth/me/` names them: the email for a link
 * account, the username for a password account. Provided by RequireAuth.
 */
export const SessionContext = createContext<{ operator: string | null }>({ operator: null });

export function useSession() {
  return useContext(SessionContext);
}
