import { useEffect, useRef, useState, type FormEvent, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { groupDigits } from '../derive/format.ts';
import { DoomScreen } from './DoomScreen.tsx';
import type { Picture } from './doom/picture.ts';
import { WebSpeaker } from './doom/speaker.ts';

const PROMPT = 'C:\\>';
const HELP = `DOOM    Runs DOOM, shareware episode 1: Knee-Deep in the Dead.
CLS     Clears the screen.
EXIT    Leaves the terminal for the match journal.
`;

type Phase = 'loading' | 'running' | 'prompt';

// Toggles in the header keep the game's focus, so a click doesn't drop its keys.
const keepFocus = (event: MouseEvent) => event.preventDefault();

/**
 * The service terminal: the console's hidden sheet, opened from the cursor
 * at the end of the status line. It runs DOOM on the tube straight away,
 * shows the engine's own startup log while it loads, and drops to a prompt
 * when the game quits.
 */
export function TerminalSheet() {
  const navigate = useNavigate();
  const [run, setRun] = useState(1);
  const [phase, setPhase] = useState<Phase>('loading');
  const [log, setLog] = useState(`PHOSPHOR SERVICE TERMINAL\n\n${PROMPT} DOOM\n`);
  const [loaded, setLoaded] = useState(0);
  const [picture, setPicture] = useState<Picture>('phosphor');
  const [muted, setMuted] = useState(false);
  const [command, setCommand] = useState('');
  const [speaker, setSpeaker] = useState<WebSpeaker | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (typeof AudioContext === 'undefined') return;
    const created = new WebSpeaker();
    setSpeaker(created);
    return () => created.close();
  }, []);

  useEffect(() => {
    if (speaker) speaker.muted = muted;
  }, [speaker, muted]);

  useEffect(() => {
    const scroll = scrollRef.current;
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
  }, [log, phase]);

  useEffect(() => {
    if (phase === 'prompt') inputRef.current?.focus();
  }, [phase]);

  const print = (text: string) => setLog((current) => current + text);

  const runDoom = () => {
    setLoaded(0);
    setRun((current) => current + 1);
    setPhase('loading');
  };

  const onExit = (status: number, error?: string) => {
    print(`${error ? `${error}\n` : ''}${status === 0 ? '' : `DOOM stopped with status ${status}.\n`}\nType DOOM to play again, or HELP.\n`);
    setPhase('prompt');
  };

  const stop = () => {
    print('^C\n\nType DOOM to play again, or HELP.\n');
    setPhase('prompt');
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const typed = command.trim();
    setCommand('');
    const echo = `${PROMPT} ${typed}\n`;
    switch (typed.toUpperCase()) {
      case '':
        return print(`${PROMPT}\n`);
      case 'DOOM':
        print(echo);
        return runDoom();
      case 'HELP':
        return print(echo + HELP);
      case 'CLS':
        return setLog('');
      case 'EXIT':
        return navigate('/journal/');
      default:
        return print(`${echo}Bad command or file name\n`);
    }
  };

  return (
    <section className="pane sheet term" aria-label="Service terminal">
      <div className="pane-h">
        <h1>Service terminal</h1>
        <div className="acts-r">
          <div className="term-seg" role="group" aria-label="Picture">
            <button type="button" className="chip" aria-pressed={picture === 'phosphor'} onMouseDown={keepFocus} onClick={() => setPicture('phosphor')}>
              Phosphor
            </button>
            <button type="button" className="chip" aria-pressed={picture === 'colour'} onMouseDown={keepFocus} onClick={() => setPicture('colour')}>
              Colour
            </button>
          </div>
          <button type="button" className="chip" aria-pressed={!muted} onMouseDown={keepFocus} onClick={() => setMuted((current) => !current)}>
            Sound
          </button>
          <button type="button" className="btn sm" onClick={stop} disabled={phase === 'prompt'}>
            Stop
          </button>
        </div>
      </div>
      <div className="term-body">
        {phase !== 'running' && (
          <div className="scroll term-scroll" ref={scrollRef}>
            <pre className="term-log" role="log">
              {log}
            </pre>
            {phase === 'loading' && (
              <p className="term-load" role="status">
                Loading DOOM · {groupDigits(String(loaded))} bytes
              </p>
            )}
            {phase === 'prompt' && (
              <form className="cmd term-cmd" onSubmit={submit}>
                <label htmlFor="term-cmd" className="prompt">
                  {PROMPT}
                </label>
                <input
                  id="term-cmd"
                  ref={inputRef}
                  autoComplete="off"
                  spellCheck={false}
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  aria-label="Command. DOOM runs the game again, HELP lists the commands."
                />
              </form>
            )}
          </div>
        )}
        {phase !== 'prompt' && (
          <DoomScreen
            key={run}
            picture={picture}
            speaker={speaker}
            onOutput={print}
            onProgress={setLoaded}
            onStarted={() => setPhase('running')}
            onExit={onExit}
          />
        )}
      </div>
    </section>
  );
}
