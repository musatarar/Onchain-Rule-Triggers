/** The status line's command line: what each typed line does. */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { COMMAND_HELP, runCommand } from '../src/console/command.ts';

test('DOOM opens the service terminal with the degauss, in any case and with stray spaces', () => {
  assert.deepEqual(runCommand('doom'), { kind: 'go', to: '/terminal/', degauss: true });
  assert.deepEqual(runCommand('  DOOM '), { kind: 'go', to: '/terminal/', degauss: true });
  assert.deepEqual(runCommand('doom.exe'), { kind: 'go', to: '/terminal/', degauss: true });
});

test('the sheet commands go where the tabs go, and JOURNAL keeps the journal as it was left', () => {
  assert.deepEqual(runCommand('journal', '?q=usdt'), { kind: 'go', to: '/journal/?q=usdt' });
  assert.deepEqual(runCommand('circuits'), { kind: 'go', to: '/circuits/' });
  assert.deepEqual(runCommand('new'), { kind: 'go', to: '/circuits/new/' });
});

test('HELP lists the sheet commands but keeps DOOM to itself', () => {
  assert.deepEqual(runCommand('help'), { kind: 'say', text: COMMAND_HELP });
  assert.ok(!COMMAND_HELP.includes('DOOM'));
});

test('an empty line closes the prompt and anything else is a bad command', () => {
  assert.deepEqual(runCommand('   '), { kind: 'close' });
  assert.deepEqual(runCommand('rm -rf'), { kind: 'say', text: 'Bad command or file name' });
});
