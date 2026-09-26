import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { registerAccount } from '../api/endpoints';
import { AuthShell } from '../components/AuthShell';
import { Button, Input } from '../components/ui';
import { takeDestination } from '../hooks/authDestination';
import { useFieldAttributes } from './fieldAttributes';

type Field = 'username' | 'password' | 'email';

/** Which field a server error belongs to; anything else lands in the banner. */
const FIELD_FOR_CODE: Record<string, Field> = {
  invalid_username: 'username',
  username_taken: 'username',
  weak_password: 'password',
  invalid_email: 'email',
};

/**
 * Create a username/password account; success signs you straight in. Email is
 * optional and unverified — it is kept on the account for when email is
 * supported officially, and signs nobody in today.
 */
export function RegisterPage() {
  const navigate = useNavigate();
  const formRef = useRef<HTMLFormElement>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [formError, setFormError] = useState('');

  useFieldAttributes(formRef, {
    'register-username': { autocomplete: 'username', name: 'username', autocapitalize: 'none' },
    'register-password': { autocomplete: 'new-password', name: 'password' },
    'register-email': { autocomplete: 'email', name: 'email', inputmode: 'email' },
  });

  const clearError = (field: Field) => {
    if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined }));
  };

  const submit = async () => {
    if (pending) return;
    const missing: Partial<Record<Field, string>> = {};
    if (!username.trim()) missing.username = 'Choose a username.';
    if (!password) missing.password = 'Choose a password.';
    setErrors(missing);
    setFormError('');
    if (Object.keys(missing).length > 0) return;

    setPending(true);
    try {
      await registerAccount({
        username: username.trim(),
        password,
        ...(email.trim() ? { email: email.trim() } : {}),
      });
      navigate(takeDestination(), { replace: true });
    } catch (error) {
      // The server is the authority on username rules and password strength.
      const code = error instanceof ApiError ? error.code : '';
      const detail =
        error instanceof Error
          ? error.message
          : 'Something went wrong. Check your connection and try again.';
      const field = FIELD_FOR_CODE[code];
      if (field) {
        setErrors({ [field]: detail });
      } else {
        setFormError(detail);
      }
      setPending(false);
    }
  };

  return (
    <AuthShell title="Create an account">
      <h1 className="auth-title">Create an account</h1>
      <p className="auth-lede">
        Already have one? <Link to="/signin">Sign in</Link>.
      </p>

      <form
        className="auth-form"
        noValidate
        ref={formRef}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Input
          label="Username"
          id="register-username"
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
            clearError('username');
          }}
          placeholder="letters, numbers, . _ -"
          error={errors.username}
          autoFocus
        />
        <Input
          label="Password"
          id="register-password"
          type="password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
            clearError('password');
          }}
          error={errors.password}
        />
        <Input
          label="Email (optional)"
          id="register-email"
          type="email"
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            clearError('email');
          }}
          placeholder="you@agency.com"
          error={errors.email}
        />
        <p className="auth-note">
          Not verified yet and not used to sign in. Email support is coming.
        </p>

        {formError && (
          <p className="auth-alert" role="alert">
            {formError}
          </p>
        )}

        <div className="auth-actions">
          <Button variant="primary" type="submit" loading={pending}>
            {pending ? 'Creating…' : 'Create account'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
