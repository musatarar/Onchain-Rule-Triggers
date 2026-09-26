import { useState } from 'react';
import type { InputHTMLAttributes, KeyboardEvent, ReactNode } from 'react';

interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  id: string;
  label: string;
  /** Guidance under the field; replaced by the error when there is one. */
  hint?: ReactNode;
  error?: string;
  /** Rendered inside the field's row, after the input (the show/hide key). */
  trailing?: ReactNode;
  /** Shown under the field regardless of error, e.g. the caps-lock warning. */
  notice?: string;
}

/**
 * A terminal field: label, a `>` prompt and the input on one ruled line, then
 * the hint or the error. The error is announced and wired to the input.
 */
export function Field({ id, label, hint, error, trailing, notice, ...input }: FieldProps) {
  const described = [error ? `${id}-err` : hint ? `${id}-hint` : '', notice ? `${id}-note` : ''].filter(Boolean).join(' ') || undefined;
  return (
    <div className={error ? 'tf bad' : 'tf'}>
      <label className="tf-l" htmlFor={id}>
        {label}
      </label>
      <div className="tf-row">
        <span className="tf-p" aria-hidden="true">
          &gt;
        </span>
        <input id={id} aria-invalid={error ? true : undefined} aria-describedby={described} spellCheck={false} {...input} />
        {trailing}
      </div>
      {error ? (
        <p className="tf-err" id={`${id}-err`} role="alert">
          {error}
        </p>
      ) : (
        hint && (
          <p className="tf-hint" id={`${id}-hint`}>
            {hint}
          </p>
        )
      )}
      {notice && (
        <p className="tf-note" id={`${id}-note`} role="status">
          {notice}
        </p>
      )}
    </div>
  );
}

/** A password field with a SHOW/HIDE key and a caps-lock warning. */
export function PasswordField(props: Omit<FieldProps, 'type' | 'trailing' | 'notice'>) {
  const [shown, setShown] = useState(false);
  const [caps, setCaps] = useState(false);
  const readCaps = (event: KeyboardEvent<HTMLInputElement>) => setCaps(event.getModifierState?.('CapsLock') ?? false);
  return (
    <Field
      {...props}
      type={shown ? 'text' : 'password'}
      onKeyDown={readCaps}
      onKeyUp={readCaps}
      onBlur={(event) => {
        setCaps(false);
        props.onBlur?.(event);
      }}
      notice={caps ? 'CAPS LOCK IS ON' : undefined}
      trailing={
        <button type="button" className="tf-key" onClick={() => setShown((s) => !s)} aria-pressed={shown} aria-controls={props.id}>
          {shown ? 'HIDE' : 'SHOW'}
        </button>
      }
    />
  );
}
