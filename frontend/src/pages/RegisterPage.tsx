import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { registerAccount } from '../api/endpoints';
import type { AuthField } from '../auth/copy.ts';
import { bannerHeading, fieldForCode, looksLikeEmail, messageOf, usernameProblem, USERNAME_MAX, USERNAME_MIN } from '../auth/copy.ts';
import { Field, PasswordField } from '../auth/Field.tsx';
import { TuneAlert, TunePanel, TuningShell } from '../auth/TuningShell.tsx';
import { LockedPanel, useSignalLock } from './SignInPage';

type Errors = Partial<Record<AuthField, string>>;

/**
 * Create a username/password account; success signs you straight in. Email is
 * optional and unverified: it's kept on the account for when email is
 * supported officially, and signs nobody in today.
 */
export function RegisterPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [banner, setBanner] = useState<{ heading: string; detail: string } | null>(null);
  const [operator, setOperator] = useState('');
  const { signal, lock, fault } = useSignalLock();

  const clear = (field: AuthField) => {
    if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined }));
  };

  const submit = async () => {
    if (pending) return;
    const found: Errors = {};
    const nameProblem = usernameProblem(username);
    if (nameProblem) found.username = nameProblem;
    if (!password) found.password = 'Choose a password.';
    if (email.trim() && !looksLikeEmail(email)) found.email = 'Enter a valid email address, or leave it empty.';
    setErrors(found);
    setBanner(null);
    if (Object.keys(found).length) return;

    setPending(true);
    try {
      const me = await registerAccount({
        username: username.trim(),
        password,
        ...(email.trim() ? { email: email.trim() } : {}),
      });
      setOperator(me.username);
      lock();
    } catch (error) {
      // The server is the authority on username rules and password strength.
      const code = error instanceof ApiError ? error.code : '';
      const field = fieldForCode(code);
      if (field) setErrors({ [field]: messageOf(error) });
      else setBanner({ heading: bannerHeading(code), detail: messageOf(error) });
      setPending(false);
      fault();
    }
  };

  if (signal === 'locking' || signal === 'locked') {
    return (
      <TuningShell title="Create an account" tab="register" signal={signal} status={`OPERATOR ${operator.toUpperCase()}`}>
        <LockedPanel operator={operator} />
      </TuningShell>
    );
  }

  return (
    <TuningShell title="Create an account" tab="register" signal={signal} status="ENROLLING A NEW OPERATOR">
      <TunePanel heading="NEW OPERATOR" lede="Choose a username and password. You'll be signed in straight away.">
        <form
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Field
            id="register-username"
            label="Username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              clear('username');
            }}
            onBlur={() => {
              if (username.trim()) {
                const problem = usernameProblem(username);
                if (problem) setErrors((current) => ({ ...current, username: problem }));
              }
            }}
            hint={`${USERNAME_MIN}–${USERNAME_MAX} characters: letters, numbers, . _ -`}
            error={errors.username}
            autoFocus
          />
          <PasswordField
            id="register-password"
            label="Password"
            name="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              clear('password');
            }}
            hint="At least 8 characters. Not a common password, not only numbers, and not close to your username."
            error={errors.password}
          />
          <Field
            id="register-email"
            label="Email (optional)"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            placeholder="you@desk.com"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              clear('email');
            }}
            hint="Stored unverified. It isn't used to sign in yet."
            error={errors.email}
          />
          {banner && <TuneAlert heading={banner.heading}>{banner.detail}</TuneAlert>}
          <div className="acts">
            <button type="submit" className="btn primary" disabled={pending} aria-busy={pending}>
              {pending ? 'ENROLLING…' : 'CREATE OPERATOR'}
            </button>
          </div>
        </form>
        <p className="alt">
          Already have an account? <Link to="/signin">Sign in</Link>
        </p>
      </TunePanel>
    </TuningShell>
  );
}
