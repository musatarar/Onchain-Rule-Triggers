/**
 * The signed-out pages' pure helpers: which field a server error lands on,
 * the banner headings, and the username guidance that mirrors
 * services.accounts. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { bannerHeading, fieldForCode, looksLikeEmail, messageOf, minutesFrom, usernameProblem } from '../src/auth/copy.ts';

test('server error codes land on the field they describe', () => {
  assert.equal(fieldForCode('invalid_username'), 'username');
  assert.equal(fieldForCode('username_taken'), 'username');
  assert.equal(fieldForCode('weak_password'), 'password');
  assert.equal(fieldForCode('invalid_email'), 'email');
});

test('credential and throttle errors go to the banner, never a field', () => {
  assert.equal(fieldForCode('invalid_credentials'), null);
  assert.equal(fieldForCode('rate_limited'), null);
  assert.equal(bannerHeading('invalid_credentials'), 'LOGIN INCORRECT');
  assert.equal(bannerHeading('rate_limited'), 'TOO MANY ATTEMPTS');
  assert.equal(bannerHeading('something_new'), 'NO SIGNAL');
});

test('username guidance mirrors the server rules', () => {
  assert.equal(usernameProblem('musa'), null);
  assert.equal(usernameProblem('ops.desk_2-b'), null);
  assert.equal(usernameProblem('  '), 'Choose a username.');
  assert.match(usernameProblem('ab') ?? '', /3 to 150/);
  assert.match(usernameProblem('x'.repeat(151)) ?? '', /3 to 150/);
  assert.match(usernameProblem('musa@desk.com') ?? '', /can't contain @/);
  assert.match(usernameProblem('musa tarar') ?? '', /letters, numbers/);
});

test('email check only catches the obviously malformed', () => {
  assert.equal(looksLikeEmail('you@desk.com'), true);
  assert.equal(looksLikeEmail(' you@desk.com '), true);
  assert.equal(looksLikeEmail('you@desk'), false);
  assert.equal(looksLikeEmail(''), false);
});

test('minutes read naturally and never round to zero', () => {
  assert.equal(minutesFrom(900), '15 minutes');
  assert.equal(minutesFrom(60), '1 minute');
  assert.equal(minutesFrom(5), '1 minute');
});

test('a thrown value always yields a sentence', () => {
  assert.equal(messageOf(new Error('Incorrect username or password.')), 'Incorrect username or password.');
  assert.match(messageOf('boom'), /Check your connection/);
});
