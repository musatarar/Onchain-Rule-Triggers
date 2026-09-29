/**
 * The status line's command line: what a typed line does.
 *
 * DOOM is deliberately left out of HELP. The prompt is the visible way in;
 * the game is still something you find.
 */
export const COMMAND_PROMPT = 'C:\\>';

export const COMMAND_HELP = 'JOURNAL · CIRCUITS · NEW · HELP · ESC CLOSES';

export type CommandResult =
  | { kind: 'close' }
  | { kind: 'go'; to: string; degauss?: boolean }
  | { kind: 'say'; text: string };

export function runCommand(line: string, journalSearch = ''): CommandResult {
  const typed = line.trim().toUpperCase();
  switch (typed) {
    case '':
      return { kind: 'close' };
    case 'DOOM':
    case 'DOOM.EXE':
      return { kind: 'go', to: '/terminal/', degauss: true };
    case 'JOURNAL':
      return { kind: 'go', to: `/journal/${journalSearch}` };
    case 'CIRCUITS':
      return { kind: 'go', to: '/circuits/' };
    case 'NEW':
      return { kind: 'go', to: '/circuits/new/' };
    case 'HELP':
    case '?':
      return { kind: 'say', text: COMMAND_HELP };
    default:
      return { kind: 'say', text: 'Bad command or file name' };
  }
}
