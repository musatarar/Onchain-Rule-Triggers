/**
 * The slice of WASI preview1 that DOOM's C library calls (the imports of
 * public/doom/doom.wasm), over an in-memory disk. One directory is preopened
 * as `/`, so every path the game opens (the WAD, default.cfg, savegames)
 * lands on the Disk; stdout and stderr go to callbacks as text.
 */

const ERRNO = { SUCCESS: 0, BADF: 8, EXIST: 20, INVAL: 28, ISDIR: 31, NOENT: 44, NOTDIR: 54, NOTEMPTY: 55 } as const;
const FILETYPE = { CHARACTER_DEVICE: 2, DIRECTORY: 3, REGULAR_FILE: 4 } as const;
const OFLAGS = { CREAT: 1, DIRECTORY: 2, EXCL: 4, TRUNC: 8 } as const;
const FDFLAGS_APPEND = 1;
const WHENCE = { SET: 0, CUR: 1, END: 2 } as const;
const RIGHTS_ALL = 0xffff_ffff_ffff_ffffn;
// A terminal can't seek or tell; libc reads that as a tty and line-buffers it.
const RIGHTS_TTY = RIGHTS_ALL & ~((1n << 2n) | (1n << 5n));
const ROOT_FD = 3;

/** Thrown by proc_exit to unwind out of the running export. */
export class WasiExit extends Error {
  readonly code: number;
  constructor(code: number) {
    super(`exited with status ${code}`);
    this.code = code;
  }
}

type File = { data: Uint8Array; size: number };

/** Files by path from the root (no leading slash); directories are paths too. */
export class Disk {
  readonly files = new Map<string, File>();
  readonly dirs = new Set<string>(['']);

  write(path: string, bytes: Uint8Array): void {
    this.files.set(path, { data: bytes, size: bytes.length });
  }

  read(path: string): Uint8Array | undefined {
    const file = this.files.get(path);
    return file && file.data.subarray(0, file.size);
  }
}

type Open =
  | { kind: 'tty'; write?: (bytes: Uint8Array) => void }
  | { kind: 'dir'; path: string }
  | { kind: 'file'; path: string; pos: number; append: boolean };

function parent(path: string): string {
  return path.slice(0, Math.max(0, path.lastIndexOf('/')));
}

function text(out: (text: string) => void): (bytes: Uint8Array) => void {
  const decoder = new TextDecoder();
  return (bytes) => out(decoder.decode(bytes, { stream: true }));
}

export class Wasi {
  memory: WebAssembly.Memory | undefined;
  private readonly disk: Disk;
  private readonly fds = new Map<number, Open>();
  private next = ROOT_FD + 1;

  constructor(options: { disk: Disk; stdout: (text: string) => void; stderr: (text: string) => void }) {
    this.disk = options.disk;
    this.fds.set(0, { kind: 'tty' });
    this.fds.set(1, { kind: 'tty', write: text(options.stdout) });
    this.fds.set(2, { kind: 'tty', write: text(options.stderr) });
    this.fds.set(ROOT_FD, { kind: 'dir', path: '' });
  }

  private view(): DataView {
    if (!this.memory) throw new Error('WASI used before its memory was bound');
    return new DataView(this.memory.buffer);
  }

  private bytes(ptr: number, len: number): Uint8Array {
    return new Uint8Array(this.view().buffer, ptr, len);
  }

  private string(ptr: number, len: number): string {
    return new TextDecoder().decode(this.bytes(ptr, len));
  }

  /** `path` from a directory descriptor, normalised; null if `fd` isn't a directory. */
  private resolve(fd: number, ptr: number, len: number): string | null {
    const dir = this.fds.get(fd);
    if (!dir || dir.kind !== 'dir') return null;
    const parts = dir.path ? dir.path.split('/') : [];
    for (const part of this.string(ptr, len).split('/')) {
      if (part === '' || part === '.') continue;
      if (part === '..') parts.pop();
      else parts.push(part);
    }
    return parts.join('/');
  }

  private iovecs(ptr: number, count: number): Uint8Array[] {
    const view = this.view();
    return Array.from({ length: count }, (_, i) => this.bytes(view.getUint32(ptr + i * 8, true), view.getUint32(ptr + i * 8 + 4, true)));
  }

  readonly imports = {
    proc_exit: (code: number): never => {
      throw new WasiExit(code);
    },

    fd_prestat_get: (fd: number, buf: number): number => {
      if (fd !== ROOT_FD) return ERRNO.BADF;
      const view = this.view();
      view.setUint8(buf, 0);
      view.setUint32(buf + 4, 1, true);
      return ERRNO.SUCCESS;
    },

    fd_prestat_dir_name: (fd: number, ptr: number, len: number): number => {
      if (fd !== ROOT_FD) return ERRNO.BADF;
      if (len < 1) return ERRNO.INVAL;
      this.bytes(ptr, 1)[0] = 0x2f; // '/'
      return ERRNO.SUCCESS;
    },

    fd_fdstat_get: (fd: number, buf: number): number => {
      const open = this.fds.get(fd);
      if (!open) return ERRNO.BADF;
      const view = this.view();
      const type = open.kind === 'tty' ? FILETYPE.CHARACTER_DEVICE : open.kind === 'dir' ? FILETYPE.DIRECTORY : FILETYPE.REGULAR_FILE;
      view.setUint8(buf, type);
      view.setUint16(buf + 2, open.kind === 'file' && open.append ? FDFLAGS_APPEND : 0, true);
      view.setBigUint64(buf + 8, open.kind === 'tty' ? RIGHTS_TTY : RIGHTS_ALL, true);
      view.setBigUint64(buf + 16, RIGHTS_ALL, true);
      return ERRNO.SUCCESS;
    },

    fd_fdstat_set_flags: (fd: number, flags: number): number => {
      const open = this.fds.get(fd);
      if (!open) return ERRNO.BADF;
      if (open.kind === 'file') open.append = (flags & FDFLAGS_APPEND) !== 0;
      return ERRNO.SUCCESS;
    },

    path_open: (
      dirfd: number,
      _dirflags: number,
      ptr: number,
      len: number,
      oflags: number,
      _rightsBase: bigint,
      _rightsInheriting: bigint,
      fdflags: number,
      out: number,
    ): number => {
      const path = this.resolve(dirfd, ptr, len);
      if (path === null) return ERRNO.NOTDIR;
      let open: Open;
      if (this.disk.dirs.has(path)) {
        if (oflags & (OFLAGS.CREAT | OFLAGS.TRUNC)) return ERRNO.ISDIR;
        open = { kind: 'dir', path };
      } else {
        if (oflags & OFLAGS.DIRECTORY) return this.disk.files.has(path) ? ERRNO.NOTDIR : ERRNO.NOENT;
        const file = this.disk.files.get(path);
        if (file && oflags & OFLAGS.EXCL) return ERRNO.EXIST;
        if (!file) {
          if (!(oflags & OFLAGS.CREAT)) return ERRNO.NOENT;
          if (!this.disk.dirs.has(parent(path))) return ERRNO.NOENT;
          this.disk.write(path, new Uint8Array(0));
        } else if (oflags & OFLAGS.TRUNC) {
          file.data = new Uint8Array(0);
          file.size = 0;
        }
        open = { kind: 'file', path, pos: 0, append: (fdflags & FDFLAGS_APPEND) !== 0 };
      }
      const fd = this.next++;
      this.fds.set(fd, open);
      this.view().setUint32(out, fd, true);
      return ERRNO.SUCCESS;
    },

    fd_close: (fd: number): number => {
      if (fd === ROOT_FD || !this.fds.delete(fd)) return ERRNO.BADF;
      return ERRNO.SUCCESS;
    },

    fd_read: (fd: number, iovs: number, count: number, out: number): number => {
      const open = this.fds.get(fd);
      if (!open) return ERRNO.BADF;
      let total = 0;
      if (open.kind === 'file') {
        const file = this.disk.files.get(open.path);
        if (!file) return ERRNO.BADF;
        for (const iov of this.iovecs(iovs, count)) {
          const chunk = file.data.subarray(open.pos, Math.min(file.size, open.pos + iov.length));
          iov.set(chunk);
          open.pos += chunk.length;
          total += chunk.length;
          if (chunk.length < iov.length) break;
        }
      } else if (open.kind === 'dir') {
        return ERRNO.ISDIR;
      }
      this.view().setUint32(out, total, true);
      return ERRNO.SUCCESS;
    },

    fd_write: (fd: number, iovs: number, count: number, out: number): number => {
      const open = this.fds.get(fd);
      if (!open) return ERRNO.BADF;
      if (open.kind === 'dir') return ERRNO.ISDIR;
      let total = 0;
      for (const iov of this.iovecs(iovs, count)) {
        if (open.kind === 'tty') {
          open.write?.(iov);
        } else {
          const file = this.disk.files.get(open.path);
          if (!file) return ERRNO.BADF;
          if (open.append) open.pos = file.size;
          const end = open.pos + iov.length;
          if (end > file.data.length) {
            const grown = new Uint8Array(Math.max(end, file.data.length * 2));
            grown.set(file.data.subarray(0, file.size));
            file.data = grown;
          }
          // A write past the end leaves a gap that reads as zeros.
          if (open.pos > file.size) file.data.fill(0, file.size, open.pos);
          file.data.set(iov, open.pos);
          file.size = Math.max(file.size, end);
          open.pos = end;
        }
        total += iov.length;
      }
      this.view().setUint32(out, total, true);
      return ERRNO.SUCCESS;
    },

    fd_seek: (fd: number, offset: bigint, whence: number, out: number): number => {
      const open = this.fds.get(fd);
      if (!open) return ERRNO.BADF;
      if (open.kind !== 'file') return ERRNO.INVAL;
      const size = this.disk.files.get(open.path)?.size ?? 0;
      const base = whence === WHENCE.SET ? 0 : whence === WHENCE.CUR ? open.pos : whence === WHENCE.END ? size : NaN;
      const pos = base + Number(offset);
      if (!(pos >= 0)) return ERRNO.INVAL;
      open.pos = pos;
      this.view().setBigUint64(out, BigInt(pos), true);
      return ERRNO.SUCCESS;
    },

    path_create_directory: (fd: number, ptr: number, len: number): number => {
      const path = this.resolve(fd, ptr, len);
      if (path === null) return ERRNO.NOTDIR;
      if (this.disk.dirs.has(path) || this.disk.files.has(path)) return ERRNO.EXIST;
      if (!this.disk.dirs.has(parent(path))) return ERRNO.NOENT;
      this.disk.dirs.add(path);
      return ERRNO.SUCCESS;
    },

    path_remove_directory: (fd: number, ptr: number, len: number): number => {
      const path = this.resolve(fd, ptr, len);
      if (path === null) return ERRNO.NOTDIR;
      if (!this.disk.dirs.has(path)) return this.disk.files.has(path) ? ERRNO.NOTDIR : ERRNO.NOENT;
      if (path === '') return ERRNO.INVAL;
      const inside = (p: string) => p.startsWith(`${path}/`);
      if ([...this.disk.files.keys()].some(inside) || [...this.disk.dirs].some(inside)) return ERRNO.NOTEMPTY;
      this.disk.dirs.delete(path);
      return ERRNO.SUCCESS;
    },

    path_unlink_file: (fd: number, ptr: number, len: number): number => {
      const path = this.resolve(fd, ptr, len);
      if (path === null) return ERRNO.NOTDIR;
      if (this.disk.dirs.has(path)) return ERRNO.ISDIR;
      return this.disk.files.delete(path) ? ERRNO.SUCCESS : ERRNO.NOENT;
    },

    path_rename: (fd: number, oldPtr: number, oldLen: number, newFd: number, newPtr: number, newLen: number): number => {
      const from = this.resolve(fd, oldPtr, oldLen);
      const to = this.resolve(newFd, newPtr, newLen);
      if (from === null || to === null) return ERRNO.NOTDIR;
      const file = this.disk.files.get(from);
      if (!file) return this.disk.dirs.has(from) ? ERRNO.ISDIR : ERRNO.NOENT;
      if (this.disk.dirs.has(to)) return ERRNO.ISDIR;
      if (!this.disk.dirs.has(parent(to))) return ERRNO.NOENT;
      this.disk.files.delete(from);
      this.disk.files.set(to, file);
      return ERRNO.SUCCESS;
    },
  };
}
