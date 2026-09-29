/**
 * The service terminal's DOOM: the committed engine (public/doom/doom.wasm)
 * boots over the WASI shim with the shareware WAD, draws a frame a tic, takes
 * keys the way the page sends them, saves to the in-memory disk and quits with
 * status 0. The pieces the page adds (DMX sound decoding, the key map, the
 * phosphor picture) do what the engine relies on.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { doomKey, KEY } from '../src/console/terminal/doom/keys.ts';
import { Doom, readDmx, SCREEN_HEIGHT, SCREEN_WIDTH, TICRATE } from '../src/console/terminal/doom/machine.ts';
import { paletteWords } from '../src/console/terminal/doom/picture.ts';
import { Disk } from '../src/console/terminal/doom/wasi.ts';

const PUBLIC = new URL('../public/doom/', import.meta.url);
const engine = WebAssembly.compile(readFileSync(new URL('doom.wasm', PUBLIC)));
const wad = new Uint8Array(readFileSync(new URL('doom1.wad', PUBLIC)));

async function boot() {
  const disk = new Disk();
  disk.write('doom1.wad', wad);
  let clock = 1;
  let output = '';
  const screen = { frames: 0, pixels: new Uint8Array(0), palette: null as Uint8Array | null };
  const sounds: number[] = [];
  const doom = await Doom.boot(
    await engine,
    disk,
    {
      // The engine waits for a tic by reading the clock in a loop, so each read moves it on a little.
      now: () => (clock += 0.05),
      frame(pixels, palette) {
        screen.frames++;
        screen.pixels = pixels.slice();
        if (palette) screen.palette = palette.slice();
      },
      stdout: (text) => (output += text),
      stderr: (text) => (output += text),
      speaker: {
        start: (_channel, lump, sample) => void (sample() && sounds.push(lump)),
        update() {},
        stop() {},
        playing: () => false,
      },
    },
    ['doom', '-iwad', 'doom1.wad'],
  );
  const run = (seconds: number) => {
    for (let tic = 0; tic < seconds * TICRATE; tic++) {
      clock += 1000 / TICRATE;
      if (doom.due) doom.tick();
    }
  };
  const press = (code: string, key = code) => {
    const mapped = doomKey(code, key);
    assert.ok(mapped, code);
    doom.key(true, mapped.key, mapped.typed);
    run(0.1);
    doom.key(false, mapped.key, mapped.typed);
    run(0.25);
  };
  const newGame = () => {
    run(1);
    press('Escape');
    press('Enter'); // New Game
    press('Enter'); // Knee-Deep in the Dead
    press('Enter'); // Hurt me plenty
    run(1);
  };
  return { doom, disk, screen, sounds, output: () => output, run, press, newGame };
}

test('DOOM boots from the committed engine and WAD and draws its title a frame a tic', async () => {
  const { doom, screen, output, run } = await boot();
  assert.equal(doom.status, null);
  assert.match(output(), /DOOM Shareware/);
  run(2);
  assert.ok(Math.abs(screen.frames - 2 * TICRATE) <= 2, `${screen.frames} frames in 2 seconds`);
  assert.equal(screen.pixels.length, SCREEN_WIDTH * SCREEN_HEIGHT);
  assert.equal(screen.palette?.length, 1024);
  assert.ok(new Set(screen.pixels).size > 50, 'the title picture, not a blank screen');
});

test('a game started from the menu saves to the disk under the name typed, with keys that also move', async () => {
  const { disk, sounds, press, newGame, run } = await boot();
  newGame();
  assert.ok(sounds.length > 0, 'the menu clicks as it opens and selects');
  press('F2');
  press('Enter');
  press('KeyA', 'a');
  press('KeyB', 'b');
  press('Enter');
  run(0.5);
  const save = disk.read('.savegame/doomsav0.dsg');
  assert.ok(save && save.length > 1000, 'savegame written');
  assert.equal(new TextDecoder().decode(save.subarray(0, 2)), 'AB');
});

test("quitting from DOOM's menu exits with status 0", async () => {
  const { doom, press, newGame } = await boot();
  newGame();
  press('Escape');
  press('ArrowUp');
  press('Enter');
  press('KeyY', 'y');
  assert.equal(doom.status, 0);
  assert.equal(doom.due, false, 'a stopped machine is never due');
});

test('a DMX lump decodes to its samples, without the 16 padding bytes at each end', () => {
  // DMX stays silent for 48 bytes or fewer, padding included.
  const samples = [0, 128, 255, ...Array<number>(17).fill(128)];
  const lump = new Uint8Array(8 + 16 + samples.length + 16);
  const header = new DataView(lump.buffer);
  header.setUint16(0, 3, true);
  header.setUint16(2, 11025, true);
  header.setUint32(4, 16 + samples.length + 16, true);
  lump.set(samples, 8 + 16);
  const sample = readDmx(lump);
  assert.equal(sample?.rate, 11025);
  assert.equal(sample?.data.length, samples.length);
  assert.deepEqual([...sample!.data.subarray(0, 3)], [-1, 0, 127 / 128]);
  assert.equal(readDmx(lump.subarray(0, 20)), null, 'a length past the lump is silent');
  assert.equal(readDmx(new Uint8Array([1, 0, 0, 0, 0, 0, 0, 0])), null, 'not format 3');
});

test('keys bind by position and type what the layout types', () => {
  assert.deepEqual(doomKey('KeyW', 'w'), { key: KEY.UPARROW, typed: 'w'.charCodeAt(0) });
  assert.deepEqual(doomKey('KeyD', 'D'), { key: KEY.STRAFE_R, typed: 'D'.charCodeAt(0) });
  // AZERTY: the key where QWERTY has A reads Q; it strafes, and types q.
  assert.deepEqual(doomKey('KeyA', 'q'), { key: KEY.STRAFE_L, typed: 'q'.charCodeAt(0) });
  // AZERTY's digit row types & unshifted; it still picks weapon 1.
  assert.deepEqual(doomKey('Digit1', '&'), { key: '1'.charCodeAt(0), typed: '&'.charCodeAt(0) });
  assert.deepEqual(doomKey('ControlLeft', 'Control'), { key: KEY.FIRE, typed: KEY.FIRE });
  assert.deepEqual(doomKey('Space', ' '), { key: KEY.USE, typed: ' '.charCodeAt(0) });
  assert.deepEqual(doomKey('KeyE', 'é'), { key: KEY.USE, typed: 'e'.charCodeAt(0) });
  assert.equal(doomKey('MetaLeft', 'Meta'), null, 'left to the browser');
});

test('the phosphor picture puts every colour on the ramp; colour keeps the palette', () => {
  const palette = new Uint8Array(1024);
  palette.set([0, 0, 0, 0, 255, 255, 255, 0, 0x33, 0x66, 0x99, 0]); // black, white, and (r 0x99, g 0x66, b 0x33)
  const rgba = (word: number) => [word & 0xff, (word >> 8) & 0xff, (word >> 16) & 0xff, word >>> 24];
  const phosphor = paletteWords(palette, 'phosphor');
  assert.deepEqual(rgba(phosphor[0]), [0x03, 0x12, 0x0a, 0xff], 'black is the tube');
  assert.deepEqual(rgba(phosphor[1]), [0x9d, 0xff, 0xbd, 0xff], 'white is hot phosphor');
  const [r, g, b] = rgba(phosphor[2]);
  assert.ok(g > r && g > b, 'a colour lands on the green ramp');
  assert.deepEqual(rgba(paletteWords(palette, 'colour')[2]), [0x99, 0x66, 0x33, 0xff]);
});
