/**
 * Keyboard events as DOOM reads them. `key` is the key the game binds,
 * chosen by physical position on a US layout, the way DOS read scancodes, so
 * the weapon row and movement keys sit in the same place on every layout.
 * `typed` is the character the key types, which is what cheats and savegame
 * names read. W/A/S/D and E move, strafe and use as in later shooters, and
 * still type their letters.
 */

/** doomgeneric's doomkeys.h */
export const KEY = {
  RIGHTARROW: 0xae,
  LEFTARROW: 0xac,
  UPARROW: 0xad,
  DOWNARROW: 0xaf,
  STRAFE_L: 0xa0,
  STRAFE_R: 0xa1,
  USE: 0xa2,
  FIRE: 0xa3,
  ESCAPE: 27,
  ENTER: 13,
  TAB: 9,
  BACKSPACE: 0x7f,
  PAUSE: 0xff,
  EQUALS: 0x3d,
  MINUS: 0x2d,
  RSHIFT: 0x80 + 0x36,
  RALT: 0x80 + 0x38,
  F1: 0x80 + 0x3b,
  F11: 0x80 + 0x57,
  F12: 0x80 + 0x58,
} as const;

const ascii = (char: string) => char.charCodeAt(0);

const BOUND: Record<string, number> = {
  ArrowUp: KEY.UPARROW,
  ArrowDown: KEY.DOWNARROW,
  ArrowLeft: KEY.LEFTARROW,
  ArrowRight: KEY.RIGHTARROW,
  KeyW: KEY.UPARROW,
  KeyS: KEY.DOWNARROW,
  KeyA: KEY.STRAFE_L,
  KeyD: KEY.STRAFE_R,
  KeyE: KEY.USE,
  Comma: KEY.STRAFE_L,
  Period: KEY.STRAFE_R,
  Space: KEY.USE,
  ControlLeft: KEY.FIRE,
  ControlRight: KEY.FIRE,
  ShiftLeft: KEY.RSHIFT,
  ShiftRight: KEY.RSHIFT,
  AltLeft: KEY.RALT,
  AltRight: KEY.RALT,
  Escape: KEY.ESCAPE,
  Enter: KEY.ENTER,
  NumpadEnter: KEY.ENTER,
  Tab: KEY.TAB,
  Backspace: KEY.BACKSPACE,
  Pause: KEY.PAUSE,
  Equal: KEY.EQUALS,
  NumpadAdd: KEY.EQUALS,
  Minus: KEY.MINUS,
  NumpadSubtract: KEY.MINUS,
  F11: KEY.F11,
  F12: KEY.F12,
  Slash: ascii('/'),
  Semicolon: ascii(';'),
  Quote: ascii("'"),
  BracketLeft: ascii('['),
  BracketRight: ascii(']'),
  Backslash: ascii('\\'),
  Backquote: ascii('`'),
};

/** What an unshifted key types on a US layout, where that differs from its binding. */
const PRINTS: Record<string, string> = {
  KeyW: 'w',
  KeyS: 's',
  KeyA: 'a',
  KeyD: 'd',
  KeyE: 'e',
  Comma: ',',
  Period: '.',
  Space: ' ',
  Equal: '=',
  NumpadAdd: '+',
  Minus: '-',
  NumpadSubtract: '-',
};

function bound(code: string): number | undefined {
  if (code in BOUND) return BOUND[code];
  const letter = /^Key([A-Z])$/.exec(code);
  if (letter) return ascii(letter[1].toLowerCase());
  const digit = /^Digit([0-9])$/.exec(code);
  if (digit) return ascii(digit[1]);
  const fn = /^F([1-9]|10)$/.exec(code);
  if (fn) return KEY.F1 + Number(fn[1]) - 1;
  return undefined;
}

/**
 * The DOOM key for a KeyboardEvent's `code` and `key`, or null for a key the
 * game doesn't know (left to the browser).
 */
export function doomKey(code: string, key: string): { key: number; typed: number } | null {
  const binding = bound(code);
  if (binding === undefined) return null;
  const printed = key.length === 1 && key >= ' ' && key <= '~' ? key : PRINTS[code];
  // A key that types nothing reports its binding, as doomgeneric's input did.
  return { key: binding, typed: printed !== undefined ? ascii(printed) : binding };
}
