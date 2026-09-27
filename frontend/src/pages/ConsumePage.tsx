import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { consumeLoginToken } from '../api/endpoints';
import { messageOf } from '../auth/copy.ts';
import { TuneAlert, TunePanel, TuningShell } from '../auth/TuningShell.tsx';
import { LockedPanel, SignInPage, useSignalLock } from './SignInPage';

/** The two codes the backend uses to describe a dead link. */
const TOKEN_CODES = new Set(['expired_token', 'invalid_token']);

type Phase = 'working' | 'failed';

export function ConsumePage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const [phase, setPhase] = useState<Phase>('working');
  const [code, setCode] = useState('');
  const [detail, setDetail] = useState('');
  const [operator, setOperator] = useState('');
  const { signal, setSignal, lock } = useSignalLock();

  // A login token is single-use, and StrictMode double-invokes effects in
  // dev; the ref survives that simulated remount, so it guards the consume.
  const attempted = useRef(false);

  useEffect(() => {
    if (attempted.current) return;
    attempted.current = true;
    if (!token) {
      setCode('invalid_token');
      setPhase('failed');
      return;
    }
    setSignal('tuning');
    consumeLoginToken({ token })
      .then((me) => {
        // Safe across the login boundary: client.ts reads the csrftoken
        // cookie per request, so it picks up the rotated one.
        setOperator(me.email);
        lock();
      })
      .catch((error: unknown) => {
        setCode(error instanceof ApiError ? error.code : '');
        setDetail(messageOf(error));
        setSignal('none');
        setPhase('failed');
      });
  }, [token, lock, setSignal]);

  if (phase === 'failed' && TOKEN_CODES.has(code)) {
    // A state of /signin rather than a page of its own.
    return <SignInPage initialState="expired" expiredCode={code} />;
  }

  if (phase === 'failed') {
    // Rate limiting or the network: the server's own sentence, not "your link expired".
    return (
      <TuningShell title="Could not sign you in" tab={null} signal={signal} status="LINK NOT CHECKED">
        <TunePanel heading="NO SIGNAL">
          <TuneAlert heading="COULD NOT SIGN YOU IN">{detail}</TuneAlert>
          <div className="acts">
            <Link to="/signin" replace className="btn primary">
              BACK TO SIGN IN
            </Link>
          </div>
        </TunePanel>
      </TuningShell>
    );
  }

  if (signal === 'locking' || signal === 'locked') {
    return (
      <TuningShell title="Signing you in" tab={null} signal={signal} status={`OPERATOR ${operator.toUpperCase()}`}>
        <LockedPanel operator={operator} />
      </TuningShell>
    );
  }

  return (
    <TuningShell title="Signing you in" tab={null} signal="tuning" status="CHECKING THE LINK">
      <TunePanel heading="TUNING…">
        <p className="tp-lede" role="status">
          Checking your sign-in link. This tab moves on by itself.
        </p>
      </TunePanel>
    </TuningShell>
  );
}
