import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { loginWithPassword, requestLoginLink } from '../api/endpoints';
import type { AuthRequestLinkResult } from '../api/types';
import { bannerHeading, LOCK_MS, looksLikeEmail, messageOf, minutesFrom, prefersReducedMotion } from '../auth/copy.ts';
import { Field, PasswordField } from '../auth/Field.tsx';
import type { Signal } from '../auth/NoSignal.tsx';
import { TuneAlert, TunePanel, TuningShell } from '../auth/TuningShell.tsx';
import { Unavailable } from '../auth/Unavailable.tsx';
import { takeDestination } from '../hooks/authDestination';

/**
 * `password` is the default way in. `enter` → `sent` is the email-link flow at
 * `?via=link`; its EMAIL LINK tab and "Email me a link" entry are switched off
 * (not available yet), but the URL still works. `expired` is entered only
 * from ConsumePage after the backend rejects a token.
 */
type State = 'password' | 'enter' | 'sent' | 'expired';

export interface SignInPageProps {
  initialState?: 'password' | 'expired';
  /** `expired_token` or `invalid_token`. */
  expiredCode?: string;
}

type Banner = { heading: string; detail: string } | null;

/** After success: roll the picture, then hand over to where the visitor was going. */
export function useSignalLock() {
  const navigate = useNavigate();
  const [signal, setSignal] = useState<Signal>('none');
  const lock = useCallback(() => {
    setSignal('locking');
    const wait = prefersReducedMotion() ? 0 : LOCK_MS;
    window.setTimeout(() => {
      setSignal('locked');
      navigate(takeDestination(), { replace: true });
    }, wait);
  }, [navigate]);
  const fault = useCallback(() => {
    setSignal('fault');
    window.setTimeout(() => setSignal((s) => (s === 'fault' ? 'none' : s)), 420);
  }, []);
  return { signal, setSignal, lock, fault };
}

/** Shown in place of the form while the signal locks. */
export function LockedPanel({ operator }: { operator: string }) {
  return (
    <TunePanel heading="SIGNAL LOCKED" labelledBy="lock-h">
      <div className="tp-lock" role="status">
        <p className="who">
          OPERATOR <b>{operator}</b>
        </p>
        <p className="tp-lede">Opening the console…</p>
      </div>
    </TunePanel>
  );
}

/** Username and password: the default way in. */
function PasswordForm({ onLocked, onFault, busy }: { onLocked: (name: string) => void; onFault: () => void; busy: boolean }) {
  const location = useLocation();
  const signedOut = (location.state as { signedOut?: boolean } | null)?.signedOut === true;
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [missing, setMissing] = useState<{ username?: string; password?: string }>({});
  const [banner, setBanner] = useState<Banner>(null);

  const submit = async () => {
    if (pending || busy) return;
    const empty = {
      ...(username.trim() ? {} : { username: 'Enter your username.' }),
      ...(password ? {} : { password: 'Enter your password.' }),
    };
    setMissing(empty);
    if (Object.keys(empty).length) return;
    setBanner(null);
    setPending(true);
    try {
      const me = await loginWithPassword({ username: username.trim(), password });
      onLocked(me.username);
    } catch (error) {
      // One answer for a wrong username and a wrong password alike: the server's.
      const code = error instanceof ApiError ? error.code : '';
      setBanner({ heading: bannerHeading(code), detail: messageOf(error) });
      setPassword('');
      setPending(false);
      onFault();
    }
  };

  return (
    <TunePanel heading="TUNE IN" lede="Sign in with your operator username and password.">
      {signedOut && !banner && (
        <p className="tp-note" role="status">
          <b>SIGNED OUT</b> Your session on this device has ended.
        </p>
      )}
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          id="signin-username"
          label="Username"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
            if (missing.username) setMissing((m) => ({ ...m, username: undefined }));
          }}
          error={missing.username}
          autoFocus
        />
        <PasswordField
          id="signin-password"
          label="Password"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
            if (missing.password) setMissing((m) => ({ ...m, password: undefined }));
          }}
          error={missing.password}
        />
        {banner && <TuneAlert heading={banner.heading}>{banner.detail}</TuneAlert>}
        <div className="acts">
          <button type="submit" className="btn primary" disabled={pending || busy} aria-busy={pending}>
            {pending ? 'TUNING…' : 'SIGN IN'}
          </button>
        </div>
      </form>
      <p className="alt">
        <Unavailable className="alt-off">Email me a link instead</Unavailable>
        <Link to="/register">Create an operator account</Link>
      </p>
    </TunePanel>
  );
}

/** Email link, step one: one field, one button. */
function LinkForm({
  email,
  onEmailChange,
  onSubmit,
  pending,
  fieldError,
  banner,
}: {
  email: string;
  onEmailChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  fieldError: string;
  banner: Banner;
}) {
  return (
    <TunePanel heading="EMAIL LINK" lede="We'll email you a sign-in link. It works once, and there's no password to type.">
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <Field
          id="signin-email"
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          placeholder="you@desk.com"
          value={email}
          onChange={(event) => onEmailChange(event.target.value)}
          error={fieldError || undefined}
          autoFocus
        />
        {banner && <TuneAlert heading={banner.heading}>{banner.detail}</TuneAlert>}
        <div className="acts">
          <button type="submit" className="btn primary" disabled={pending} aria-busy={pending}>
            {pending ? 'SENDING…' : 'EMAIL ME A LINK'}
          </button>
        </div>
      </form>
      <p className="alt">
        <Link to="/signin">Use a username and password</Link>
      </p>
    </TunePanel>
  );
}

/** Email link, step two: where it went, how long it lasts, and how to try again. */
function SentPanel({
  email,
  result,
  cooldown,
  resending,
  banner,
  onResend,
  onUseAnother,
}: {
  email: string;
  result: AuthRequestLinkResult;
  cooldown: number;
  resending: boolean;
  banner: Banner;
  onResend: () => void;
  onUseAnother: () => void;
}) {
  return (
    <TunePanel
      heading="LINK SENT"
      lede={
        <>
          A sign-in link is on its way to <b>{email}</b>. It expires in {minutesFrom(result.expires_in)} and works once. Open it
          on this device.
        </>
      }
    >
      {/* Rendered only when the API returns one; never constructed here. */}
      {result.dev_link && (
        <div className="devlink">
          <span className="tag abn">DEV MODE</span>
          <a href={result.dev_link}>{result.dev_link}</a>
          <p>Shown because the server runs with DEBUG and console delivery. It is never returned in production.</p>
        </div>
      )}
      {banner && <TuneAlert heading={banner.heading}>{banner.detail}</TuneAlert>}
      <div className="acts">
        <button type="button" className="btn" onClick={onResend} disabled={cooldown > 0 || resending} aria-busy={resending}>
          {cooldown > 0 ? `SEND ANOTHER IN ${cooldown}s` : resending ? 'SENDING…' : 'SEND ANOTHER LINK'}
        </button>
      </div>
      <p className="alt">
        <button type="button" onClick={onUseAnother}>
          Use a different address
        </button>
        <Link to="/signin">Sign in with a password</Link>
      </p>
    </TunePanel>
  );
}

/** A dead link: why, then back to step one. */
function ExpiredPanel({ code, onRestart }: { code: string; onRestart: () => void }) {
  const expired = code === 'expired_token';
  return (
    <TunePanel
      heading={expired ? 'LINK EXPIRED' : 'LINK NOT VALID'}
      lede={
        expired
          ? 'Sign-in links last a short while, and this one is past that.'
          : 'Sign-in links work once. This one has already been used, or it was never valid.'
      }
    >
      <div className="acts">
        <button type="button" className="btn primary" onClick={onRestart}>
          REQUEST A NEW LINK
        </button>
      </div>
      <p className="alt">
        <Link to="/signin">Sign in with a password</Link>
      </p>
    </TunePanel>
  );
}

export function SignInPage({ initialState = 'password', expiredCode = '' }: SignInPageProps) {
  const [params] = useSearchParams();
  const viaLink = params.get('via') === 'link';
  const [state, setState] = useState<State>(initialState === 'expired' ? 'expired' : viaLink ? 'enter' : 'password');
  const [email, setEmail] = useState('');
  const [result, setResult] = useState<AuthRequestLinkResult | null>(null);
  const [pending, setPending] = useState(false);
  const [fieldError, setFieldError] = useState('');
  const [banner, setBanner] = useState<Banner>(null);
  const [cooldown, setCooldown] = useState(0);
  const [operator, setOperator] = useState('');
  const { signal, setSignal, lock, fault } = useSignalLock();

  // The tab strip changes the query string; follow it (but never out of expired).
  useEffect(() => {
    setState((s) => (s === 'expired' ? s : viaLink ? (s === 'sent' ? s : 'enter') : 'password'));
  }, [viaLink]);

  // Cleared on unmount and restart, so a resend cannot leave two intervals racing.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((left) => (left <= 1 ? 0 : left - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown > 0]);

  const send = useCallback(
    async (address: string) => {
      if (!looksLikeEmail(address)) {
        setFieldError('Enter a valid email address.');
        setBanner(null);
        return;
      }
      setFieldError('');
      setBanner(null);
      setPending(true);
      setSignal('tuning');
      try {
        const response = await requestLoginLink({ email: address.trim() });
        setResult(response);
        setCooldown(response.resend_after);
        setState('sent');
        setSignal('none');
      } catch (error) {
        const code = error instanceof ApiError ? error.code : '';
        if (code === 'invalid_email') setFieldError(messageOf(error));
        else setBanner({ heading: bannerHeading(code), detail: messageOf(error) });
        fault();
      } finally {
        setPending(false);
      }
    },
    [fault, setSignal],
  );

  const restart = useCallback(() => {
    setState('enter');
    setResult(null);
    setCooldown(0);
    setFieldError('');
    setBanner(null);
  }, []);

  if (signal === 'locking' || signal === 'locked') {
    return (
      <TuningShell title="Sign in" tab="signin" signal={signal} status={`OPERATOR ${operator.toUpperCase()}`}>
        <LockedPanel operator={operator} />
      </TuningShell>
    );
  }

  if (state === 'expired') {
    return (
      <TuningShell title="Link expired" tab="link" signal={signal} status="LINK REJECTED">
        <ExpiredPanel code={expiredCode} onRestart={restart} />
      </TuningShell>
    );
  }

  if (state === 'sent' && result) {
    return (
      <TuningShell title="Check your email" tab="link" signal={signal} status="LINK SENT · WAITING FOR IT TO BE OPENED">
        <SentPanel
          email={email.trim()}
          result={result}
          cooldown={cooldown}
          resending={pending}
          banner={banner ?? (fieldError ? { heading: 'NO SIGNAL', detail: fieldError } : null)}
          onResend={() => {
            if (pending || cooldown > 0) return;
            void send(email);
          }}
          onUseAnother={restart}
        />
      </TuningShell>
    );
  }

  if (state === 'enter') {
    return (
      <TuningShell title="Sign in" tab="link" signal={signal} status="AWAITING OPERATOR">
        <LinkForm
          email={email}
          onEmailChange={(value) => {
            setEmail(value);
            if (fieldError) setFieldError('');
          }}
          onSubmit={() => {
            if (!pending) void send(email);
          }}
          pending={pending}
          fieldError={fieldError}
          banner={banner}
        />
      </TuningShell>
    );
  }

  return (
    <TuningShell title="Sign in" tab="signin" signal={signal} status="AWAITING OPERATOR">
      <PasswordForm
        busy={signal !== 'none' && signal !== 'fault'}
        onFault={fault}
        onLocked={(name) => {
          setOperator(name);
          lock();
        }}
      />
    </TuningShell>
  );
}
