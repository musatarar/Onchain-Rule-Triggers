import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { COMMAND_PROMPT, runCommand, type CommandResult } from './command.ts';

type Props = {
  open: boolean;
  journalSearch: string;
  onOpen: () => void;
  onClose: () => void;
  onGo: (result: Extract<CommandResult, { kind: 'go' }>) => void;
};

/**
 * The block cursor at the end of the status line, and the command line it
 * turns into. Closed, it is a button (or `:`); open, the hint gives way to a
 * `C:\>` prompt whose caret is the same block. Enter runs the line, Esc or
 * leaving it empty puts the cursor back.
 */
export function CommandLine({ open, journalSearch, onOpen, onClose, onGo }: Props) {
  const [line, setLine] = useState('');
  const [reply, setReply] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const cursorRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(open);
  // A run command moves on to its sheet; only Esc or an empty line gives focus back to the cursor.
  const returnFocus = useRef(true);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    } else {
      setLine('');
      setReply('');
      // Only hand focus back if it was in the command line when it closed.
      const lost = !document.activeElement || document.activeElement === document.body;
      if (wasOpen.current && returnFocus.current && lost) cursorRef.current?.focus();
      returnFocus.current = true;
    }
    wasOpen.current = open;
  }, [open]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const result = runCommand(line, journalSearch);
    setLine('');
    if (result.kind === 'close') return onClose();
    if (result.kind === 'say') return setReply(result.text);
    returnFocus.current = false;
    onGo(result);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  };

  if (!open) {
    return (
      <button
        ref={cursorRef}
        type="button"
        className="blink cl-cursor"
        onClick={onOpen}
        aria-label="Command line"
        aria-keyshortcuts=":"
        title="Command line ( : )"
      >
        █
      </button>
    );
  }

  return (
    <form className="cl" onSubmit={submit} aria-label="Command line">
      <label htmlFor="cl-input" className="cl-prompt">
        {COMMAND_PROMPT}
      </label>
      <input
        id="cl-input"
        ref={inputRef}
        value={line}
        onChange={(event) => {
          setLine(event.target.value);
          if (reply) setReply('');
        }}
        onKeyDown={onKeyDown}
        onBlur={() => {
          if (!line.trim()) onClose();
        }}
        size={Math.max(line.length + 1, 4)}
        maxLength={24}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        aria-describedby="cl-reply"
      />
      <span id="cl-reply" className="cl-reply" role="status">
        {reply}
      </span>
    </form>
  );
}
