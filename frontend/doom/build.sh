#!/usr/bin/env bash
# Builds frontend/public/doom/doom.wasm, the DOOM engine behind the console's
# service terminal (frontend/src/console/terminal/).
#
# The engine is doomgeneric (GPL-2.0) at a pinned commit, patched by
# doomgeneric.patch (a screen wipe that doesn't block, I_Quit that exits,
# an exit hook WebAssembly can call, no zenity under WASI) and given
# doomgeneric_phosphor.c as its platform.
# It is compiled for wasm32-wasi with the system clang against the wasi-sdk
# sysroot. The output is committed next to the WAD, like the console's
# fonts, so neither CI nor `npm run build` needs this toolchain; run this
# after changing anything in frontend/doom/ and commit the new doom.wasm.
#
# Needs: git, curl, sha256sum, and clang + wasm-ld 17 or newer with the
# wasm32 target (Debian/Ubuntu: apt install clang lld). The committed
# doom.wasm came from Ubuntu clang 18.1.3; the same compiler rebuilds it byte
# for byte.
set -euo pipefail

DOOMGENERIC_REPO=https://github.com/ozkl/doomgeneric.git
DOOMGENERIC_COMMIT=dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284

WASI_SDK=https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-22
SYSROOT_TAR=wasi-sysroot-22.0.tar.gz
SYSROOT_SHA256=23881870d5a9c94df0529bc3e9b13682b7bbb07e5167555132fdc14e1faf1bb8
BUILTINS_TAR=libclang_rt.builtins-wasm32-wasi-22.0.tar.gz
BUILTINS_SHA256=9c2f54ff7e3597ed1014f9a8e818ce60ec5d2099f1f4bdbf22310d799b46fdf6

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../public/doom/doom.wasm"
WORK="${DOOM_BUILD_DIR:-${TMPDIR:-/tmp}/phosphor-doom}"
CC="${CC:-clang}"

mkdir -p "$WORK"
cd "$WORK"

fetch() { # url file sha256
  [ -f "$2" ] || curl -fsSL -o "$2" "$1"
  echo "$3  $2" | sha256sum -c --quiet -
}

fetch "$WASI_SDK/$SYSROOT_TAR" "$SYSROOT_TAR" "$SYSROOT_SHA256"
fetch "$WASI_SDK/$BUILTINS_TAR" "$BUILTINS_TAR" "$BUILTINS_SHA256"
rm -rf sysroot resource
mkdir -p sysroot resource/lib
tar xzf "$SYSROOT_TAR" -C sysroot --strip-components=1
tar xzf "$BUILTINS_TAR" -C resource
# clang looks for the wasm32 builtins in its resource dir; give it one that
# pairs the system compiler's headers with the wasi-sdk library.
ln -s "$("$CC" -print-resource-dir)/include" resource/include

rm -rf doomgeneric
git init -q doomgeneric
git -C doomgeneric fetch -q --depth 1 "$DOOMGENERIC_REPO" "$DOOMGENERIC_COMMIT"
git -C doomgeneric checkout -q FETCH_HEAD
SRC="$WORK/doomgeneric/doomgeneric"
patch -s -p1 -d "$SRC" < "$HERE/doomgeneric.patch"
cp "$HERE/doomgeneric_phosphor.c" "$SRC/"
# i_sound.c includes SDL_mixer.h whenever FEATURE_SOUND is on, but uses
# nothing from it.
mkdir -p include
: > include/SDL_mixer.h

# doomgeneric's Makefile list, with doomgeneric_phosphor.c as the platform
# and in place of i_input.c.
SOURCES="dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop
d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus
i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox
m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors
p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr
p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data
r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib
st_stuff s_sound tables v_video wi_stuff w_checksum w_file w_main w_wad
z_zone w_file_stdc i_video doomgeneric doomgeneric_phosphor"

# CMAP256 at 320x200 hands the page the game's own palette-indexed frame.
# The prefix map keeps this machine's paths out of __FILE__, so the output
# is the same wherever it is built.
CFLAGS="--target=wasm32-wasi --sysroot=$WORK/sysroot -resource-dir=$WORK/resource
-O2 -w -DCMAP256 -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200 -DFEATURE_SOUND
-I$WORK/include -ffile-prefix-map=$SRC/="

rm -rf obj
mkdir obj
for name in $SOURCES; do
  # shellcheck disable=SC2086
  "$CC" $CFLAGS -c "$SRC/$name.c" -o "obj/$name.o"
done

# A reactor: the page calls _initialize once, then doom_start and doom_tick.
# shellcheck disable=SC2086
"$CC" $CFLAGS -mexec-model=reactor \
  -Wl,-z,stack-size=1048576 -Wl,--gc-sections \
  -Wl,--export=malloc -Wl,--export=free \
  obj/*.o -o "$OUT"

echo "wrote $OUT ($(wc -c < "$OUT") bytes)"
