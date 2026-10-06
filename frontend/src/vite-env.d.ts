/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** propose and backtest on the real API, e.g. "propose=http"; see frontend/.env. */
  readonly VITE_CONSOLE_SOURCES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
