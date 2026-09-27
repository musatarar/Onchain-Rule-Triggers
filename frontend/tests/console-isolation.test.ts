/**
 * Demo data stays behind the API seam. UI code imports only console/api's index or
 * types, and nothing outside console/api/demo/ reaches a fixture or the demo engine;
 * index.ts's one import of DemoConsoleApi is the switch itself.
 */
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../src');
const API = join(SRC, 'console/api');
const DEMO = join(API, 'demo');

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

function imports(file: string): string[] {
  const text = readFileSync(file, 'utf8');
  const found = [...text.matchAll(/(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|import\s+['"]([^'"]+)['"]/g)];
  return found
    .map((m) => m[1] ?? m[2] ?? m[3])
    .filter((spec) => spec.startsWith('.'))
    .map((spec) => resolve(dirname(file), spec));
}

const inside = (path: string, dir: string) => !relative(dir, path).startsWith('..');

test('nothing outside console/api/demo/ imports a fixture or the demo engine', () => {
  const offenders: string[] = [];
  for (const file of sources(SRC)) {
    if (inside(file, DEMO)) continue;
    for (const target of imports(file)) {
      if (!inside(target, DEMO)) continue;
      const sanctioned = file === join(API, 'index.ts') && target === join(DEMO, 'DemoConsoleApi.ts');
      if (!sanctioned) offenders.push(`${relative(SRC, file)} → ${relative(SRC, target)}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test('UI modules import console/api only through its index or types', () => {
  const offenders: string[] = [];
  const allowed = new Set([join(API, 'index.ts'), join(API, 'types.ts')]);
  for (const file of sources(join(SRC, 'console'))) {
    if (inside(file, API)) continue;
    for (const target of imports(file)) {
      if (inside(target, API) && !allowed.has(target)) offenders.push(`${relative(SRC, file)} → ${relative(SRC, target)}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test('the check sees the imports it is meant to police', () => {
  const index = imports(join(API, 'index.ts'));
  assert.ok(index.includes(join(DEMO, 'DemoConsoleApi.ts')));
  const ui = sources(join(SRC, 'console')).filter((file) => !inside(file, API));
  assert.ok(ui.length > 5);
  assert.ok(ui.some((file) => imports(file).includes(join(API, 'index.ts'))));
});
