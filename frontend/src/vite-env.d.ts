/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Console sources on the real API, e.g. "rules=http,matches=http"; see frontend/.env. */
  readonly VITE_CONSOLE_SOURCES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
