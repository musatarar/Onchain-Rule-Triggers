import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { setUnauthorizedHandler } from '../api/client';
import { SessionContext } from '../auth/session.tsx';
import { TuningShell } from '../auth/TuningShell.tsx';
import { rememberDestination } from '../hooks/authDestination';
import { useAuth } from '../hooks/useAuth';

/**
 * Route guard for every authenticated surface. Redirects to /signin on the
 * mount-time session check and on any mid-session 401 (via the one handler
 * installed into client.ts); both remember the destination so signing in
 * resumes the journey. Provides who is signed in to the console.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, email } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const destination = `${location.pathname}${location.search}`;

  useEffect(() => {
    setUnauthorizedHandler(() => {
      // Read `window.location`, not the closed-over value: the 401 may fire
      // long after this effect ran, from a page navigated to since.
      rememberDestination(`${window.location.pathname}${window.location.search}`);
      navigate('/signin', { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate]);

  // Redirect from an effect, not <Navigate>, so the destination is remembered
  // strictly before the navigation.
  useEffect(() => {
    if (status !== 'anonymous') return;
    rememberDestination(destination);
    navigate('/signin', { replace: true, state: { from: destination } });
  }, [status, destination, navigate]);

  if (status === 'authenticated') {
    return <SessionContext.Provider value={{ operator: email }}>{children}</SessionContext.Provider>;
  }

  // Usually one frame: the monitor, off-air, while the session is checked.
  return (
    <TuningShell title="Checking your session" tab={null} signal="tuning" status="CHECKING SESSION">
      <p className="tune-status" role="status">
        {status === 'checking' ? 'CHECKING YOUR SESSION…' : 'TAKING YOU TO SIGN IN…'}
      </p>
    </TuningShell>
  );
}
