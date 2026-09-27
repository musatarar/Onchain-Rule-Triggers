import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { doomKey } from './doom/keys.ts';
import { Doom, SCREEN_HEIGHT, SCREEN_WIDTH, type Speaker } from './doom/machine.ts';
import { paint, paletteWords, type Picture } from './doom/picture.ts';
import type { WebSpeaker } from './doom/speaker.ts';
import { Disk } from './doom/wasi.ts';

const DOOM_DIR = `${import.meta.env.BASE_URL}doom/`;
const WAD = 'doom1.wad';
const ARGS = ['doom', '-iwad', WAD, '-mb', '16'];
/** A gap between frames longer than this is a pause (a hidden tab, a debugger), not lag to catch up. */
const PAUSE_MS = 250;

type Assets = { module: WebAssembly.Module; wad: Uint8Array };

let assets: Promise<Assets> | null = null;
let loaded = 0;
/** The run waiting on the download now, which may not be the one that started it. */
let onLoaded: ((loaded: number) => void) | null = null;
/** One disk a page load, so savegames outlast quitting and running DOOM again. */
const disk = new Disk();

async function download(url: string, onBytes: (bytes: number) => void): Promise<Uint8Array<ArrayBuffer>> {
  const response = await fetch(url);
  if (!response.ok || !response.body) throw new Error(`${url.split('/').pop()} answered HTTP ${response.status}`);
  const chunks: Uint8Array[] = [];
  let size = 0;
  const reader = response.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.length;
    onBytes(value.length);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.length;
  }
  return bytes;
}

/** The engine and the WAD, fetched once a page load. Progress counts bytes of both. */
function loadAssets(onProgress: (loaded: number) => void): Promise<Assets> {
  onLoaded = onProgress;
  if (!assets) {
    loaded = 0;
    const counted = (bytes: number) => onLoaded?.((loaded += bytes));
    assets = Promise.all([download(`${DOOM_DIR}doom.wasm`, counted), download(`${DOOM_DIR}${WAD}`, counted)]).then(
      async ([engine, wad]) => ({ module: await WebAssembly.compile(engine), wad }),
    );
    assets.catch(() => {
      assets = null;
    });
  }
  onProgress(loaded);
  return assets;
}

type Props = {
  picture: Picture;
  speaker: WebSpeaker | null;
  /** DOOM's stdout and stderr, as it prints them. */
  onOutput: (text: string) => void;
  onProgress: (loaded: number) => void;
  onStarted: () => void;
  /** The game quit (status 0), failed, or couldn't load (`error`). */
  onExit: (status: number, error?: string) => void;
};

/**
 * One run of DOOM: mounting it loads and boots the engine, unmounting stops
 * it. The game draws into a 320×200 canvas and reads the keyboard while the
 * screen has focus, and the mouse (turn and fire) once it is captured.
 */
export function DoomScreen({ picture, speaker, onOutput, onProgress, onStarted, onExit }: Props) {
  const screenRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const doomRef = useRef<Doom | null>(null);
  const [running, setRunning] = useState(false);
  const [focused, setFocused] = useState(false);
  const held = useRef(new Map<string, number>());
  const events = useRef({ onOutput, onProgress, onStarted, onExit });
  events.current = { onOutput, onProgress, onStarted, onExit };
  // The page's audio can arrive after the run starts; the game plays through whichever is current.
  const speakerRef = useRef(speaker);
  speakerRef.current = speaker;

  // The last frame and palette, so a picture change repaints without a tic.
  const view = useRef<{ image: ImageData; out: Uint32Array; pixels: Uint8Array; palette: Uint8Array | null; words: Uint32Array | null } | null>(null);
  const pictureRef = useRef(picture);

  const repaint = () => {
    const canvas = canvasRef.current;
    const frame = view.current;
    if (!canvas || !frame?.palette) return;
    frame.words ??= paletteWords(frame.palette, pictureRef.current);
    paint(frame.pixels, frame.words, frame.out);
    canvas.getContext('2d')?.putImageData(frame.image, 0, 0);
  };

  useEffect(() => {
    pictureRef.current = picture;
    if (view.current) view.current.words = null;
    repaint();
  }, [picture]);

  useEffect(() => {
    let cancelled = false;
    let raf = 0;
    let skipped = 0;
    const now = () => performance.now() - skipped;
    const image = new ImageData(SCREEN_WIDTH, SCREEN_HEIGHT);
    view.current = { image, out: new Uint32Array(image.data.buffer), pixels: new Uint8Array(SCREEN_WIDTH * SCREEN_HEIGHT), palette: null, words: null };

    const finish = (status: number, error?: string) => {
      if (cancelled) return;
      cancelled = true;
      if (document.pointerLockElement) document.exitPointerLock();
      events.current.onExit(status, error);
    };

    const start = async () => {
      const { module, wad } = await loadAssets((bytes) => !cancelled && events.current.onProgress(bytes));
      if (cancelled) return;
      if (!disk.files.has(WAD)) disk.write(WAD, wad);
      const doom = await Doom.boot(
        module,
        disk,
        {
          now,
          frame(pixels, palette) {
            const frame = view.current!;
            frame.pixels.set(pixels);
            if (palette) {
              frame.palette = palette.slice();
              frame.words = null;
            }
            repaint();
          },
          stdout: (text) => events.current.onOutput(text),
          stderr: (text) => events.current.onOutput(text),
          speaker: {
            start: (...args: Parameters<Speaker['start']>) => speakerRef.current?.start(...args),
            update: (channel, vol, sep) => speakerRef.current?.update(channel, vol, sep),
            stop: (channel) => speakerRef.current?.stop(channel),
            playing: (channel) => speakerRef.current?.playing(channel) ?? false,
          },
        },
        ARGS,
      );
      if (cancelled) return;
      if (doom.status !== null) return finish(doom.status);
      doomRef.current = doom;
      setRunning(true);
      events.current.onStarted();
      let last = performance.now();
      const loop = (time: number) => {
        if (cancelled) return;
        if (time - last > PAUSE_MS) skipped += time - last;
        last = time;
        try {
          if (doom.due) doom.tick();
        } catch (error) {
          return finish(-1, `DOOM crashed: ${error instanceof Error ? error.message : String(error)}`);
        }
        if (doom.status !== null) return finish(doom.status);
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    };
    start().catch((error: unknown) => finish(-1, error instanceof Error ? error.message : String(error)));

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      doomRef.current = null;
      if (document.pointerLockElement) document.exitPointerLock();
    };
    // One run a mount: the parent remounts this with a new key to run again.
  }, []);

  useEffect(() => {
    if (!running) return;
    screenRef.current?.focus();
    // Ctrl is fire and W walks, and no page can stop Ctrl+W closing its tab,
    // so leaving mid-game asks first.
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    const onMove = (event: MouseEvent) => {
      if (document.pointerLockElement === screenRef.current) doomRef.current?.mouse(event.buttons, event.movementX);
    };
    const onButton = (event: MouseEvent) => {
      if (document.pointerLockElement === screenRef.current) doomRef.current?.mouse(event.buttons, 0);
    };
    window.addEventListener('beforeunload', guard);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mousedown', onButton);
    document.addEventListener('mouseup', onButton);
    return () => {
      window.removeEventListener('beforeunload', guard);
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mousedown', onButton);
      document.removeEventListener('mouseup', onButton);
    };
  }, [running]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const doom = doomRef.current;
    const mapped = doomKey(event.code, event.key);
    if (!doom || !mapped) return;
    event.preventDefault();
    event.stopPropagation();
    speaker?.resume();
    held.current.set(event.code, mapped.key);
    doom.key(true, mapped.key, mapped.typed);
  };

  const release = (code: string) => {
    const key = held.current.get(code);
    if (key === undefined) return false;
    held.current.delete(code);
    // W and the up arrow are one key to the game: it stays down while either is.
    if (![...held.current.values()].includes(key)) doomRef.current?.key(false, key, 0);
    return true;
  };

  const onKeyUp = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!release(event.code)) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const onBlur = () => {
    setFocused(false);
    for (const code of [...held.current.keys()]) release(code);
    doomRef.current?.mouse(0, 0);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    speaker?.resume();
    const screen = event.currentTarget;
    screen.focus();
    if (running && document.pointerLockElement !== screen && event.pointerType === 'mouse') {
      // Newer browsers return a promise that rejects when capture is refused.
      Promise.resolve(screen.requestPointerLock()).catch(() => {});
    }
  };

  return (
    <div
      ref={screenRef}
      className="doom"
      tabIndex={0}
      role="application"
      aria-label="DOOM. Arrow keys or W A S D move, Ctrl fires, Space or E uses, Escape opens the menu."
      onKeyDown={onKeyDown}
      onKeyUp={onKeyUp}
      onFocus={() => setFocused(true)}
      onBlur={onBlur}
      onPointerDown={onPointerDown}
      onContextMenu={(event) => event.preventDefault()}
      hidden={!running}
    >
      <canvas ref={canvasRef} width={SCREEN_WIDTH} height={SCREEN_HEIGHT} />
      {running && !focused && (
        <span className="doom-hint" aria-hidden="true">
          CLICK TO PLAY
        </span>
      )}
    </div>
  );
}

