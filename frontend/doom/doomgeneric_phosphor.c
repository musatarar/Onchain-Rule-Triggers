//
// Copyright(C) 1993-1996 Id Software, Inc.
// Copyright(C) 2005-2014 Simon Howard
//
// This program is free software; you can redistribute it and/or
// modify it under the terms of the GNU General Public License
// as published by the Free Software Foundation; either version 2
// of the License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// DESCRIPTION:
//     The doomgeneric platform for the Phosphor service terminal, built
//     for wasm32-wasi by build.sh. It stands in for doomgeneric's
//     i_input.c as well. The page on the other side is
//     frontend/src/console/terminal/doom/machine.ts: it implements the
//     "phosphor" imports below and calls the exports. Only numbers and
//     pointers into linear memory cross.
//

#include <stdint.h>

#include "d_event.h"
#include "deh_str.h"
#include "doomgeneric.h"
#include "doomtype.h"
#include "i_sound.h"
#include "i_video.h"
#include "m_misc.h"
#include "w_wad.h"
#include "z_zone.h"

#define IMPORT(name) __attribute__((import_module("phosphor"), import_name(name)))
#define EXPORT(name) __attribute__((export_name(name)))

// A finished frame: SCREENWIDTH x SCREENHEIGHT palette indices, and the
// 256 palette entries as (b, g, r, unused) bytes, flagged when they changed.
IMPORT("frame") void host_frame(const pixel_t *pixels, const struct color *palette, int palette_changed);

// Milliseconds on the page's game clock, which stops while the page is hidden.
IMPORT("ticks_ms") uint32_t host_ticks_ms(void);

// The next key event as (pressed << 16) | (typed character << 8) | doom key,
// or -1 when none is queued.
IMPORT("poll_key") int host_poll_key(void);

IMPORT("title") void host_title(const char *title);

// Sound effects are voices on the page: the lump is a DMX sample, vol is
// 0-127 and sep 0 (left) to 254 (right).
IMPORT("sfx_start") int host_sfx_start(int channel, int lump, const byte *data, int length, int vol, int sep);
IMPORT("sfx_update") void host_sfx_update(int channel, int vol, int sep);
IMPORT("sfx_stop") void host_sfx_stop(int channel);
IMPORT("sfx_playing") int host_sfx_playing(int channel);

EXPORT("doom_start") void doom_start(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
}

EXPORT("doom_tick") void doom_tick(void)
{
    doomgeneric_Tick();
}

// The buttons held and the motion since the last tic. The game keeps only
// the latest mouse event of a tic, so the page sums motion before posting.
EXPORT("doom_mouse") void doom_mouse(int buttons, int dx, int dy)
{
    event_t event;

    event.type = ev_mouse;
    event.data1 = buttons;
    event.data2 = dx;
    event.data3 = dy;
    event.data4 = 0;
    D_PostEvent(&event);
}

void DG_Init(void)
{
}

void DG_DrawFrame(void)
{
    host_frame(DG_ScreenBuffer, colors, palette_changed);
    palette_changed = false;
}

void DG_SleepMs(uint32_t ms)
{
    // Never block the page. It only ticks once a new tic is due, so the
    // engine's wait loops spin for less than a tic.
    (void) ms;
}

uint32_t DG_GetTicksMs(void)
{
    return host_ticks_ms();
}

void DG_SetWindowTitle(const char *title)
{
    host_title(title);
}

//
// Keyboard (in place of i_input.c)
//

// Off, so a savegame name takes the typed character (data2) rather than
// the key, which the page may have remapped (W is the up arrow).
int vanilla_keyboard_mapping = 0;

void I_InitInput(void)
{
}

// The page sends each key already translated: data1 is the key the game
// binds, data2 the character it types (shift and keyboard layout applied),
// so cheats and savegame names read what was typed.
void I_GetEvent(void)
{
    // A key released in the same tic it was pressed would never be seen
    // held, so its release waits for the next tic.
    static int deferred = -1;
    boolean pressed[256] = { false };
    int key;

    for (;;)
    {
        event_t event;

        if (deferred >= 0)
        {
            key = deferred;
            deferred = -1;
        }
        else if ((key = host_poll_key()) < 0)
        {
            break;
        }

        if (!(key >> 16) && pressed[key & 0xff])
        {
            deferred = key;
            break;
        }

        event.type = (key >> 16) ? ev_keydown : ev_keyup;
        event.data1 = key & 0xff;
        event.data2 = event.type == ev_keydown ? (key >> 8) & 0xff : 0;
        event.data3 = 0;
        event.data4 = 0;
        D_PostEvent(&event);

        if (event.type == ev_keydown)
        {
            pressed[key & 0xff] = true;
        }
    }
}

//
// Sound effects
//

// Bound by I_BindSoundVariables; there is no resampler here to use them.
int use_libsamplerate = 0;
float libsamplerate_scale = 0.65f;

static boolean use_sfx_prefix;

static snddevice_t sound_devices[] =
{
    SNDDEVICE_SB,
    SNDDEVICE_PAS,
    SNDDEVICE_GUS,
    SNDDEVICE_WAVEBLASTER,
    SNDDEVICE_SOUNDCANVAS,
    SNDDEVICE_AWE32,
};

static boolean Phosphor_InitSound(boolean _use_sfx_prefix)
{
    use_sfx_prefix = _use_sfx_prefix;
    return true;
}

static void Phosphor_ShutdownSound(void)
{
}

static int Phosphor_GetSfxLumpNum(sfxinfo_t *sfx)
{
    char namebuf[9];

    // Linked sounds play the lump of the sound they link to.
    if (sfx->link != NULL)
    {
        sfx = sfx->link;
    }

    if (use_sfx_prefix)
    {
        M_snprintf(namebuf, sizeof(namebuf), "ds%s", DEH_String(sfx->name));
    }
    else
    {
        M_StringCopy(namebuf, DEH_String(sfx->name), sizeof(namebuf));
    }

    return W_GetNumForName(namebuf);
}

static void Phosphor_UpdateSound(void)
{
}

static void Phosphor_UpdateSoundParams(int channel, int vol, int sep)
{
    host_sfx_update(channel, vol, sep);
}

static int Phosphor_StartSound(sfxinfo_t *sfxinfo, int channel, int vol, int sep)
{
    int lump = sfxinfo->lumpnum;
    int result;

    // The page decodes a lump the first time it plays and keeps it.
    result = host_sfx_start(channel, lump, W_CacheLumpNum(lump, PU_STATIC),
                            W_LumpLength(lump), vol, sep);
    W_ReleaseLumpNum(lump);

    return result;
}

static void Phosphor_StopSound(int channel)
{
    host_sfx_stop(channel);
}

static boolean Phosphor_SoundIsPlaying(int channel)
{
    return host_sfx_playing(channel) != 0;
}

static void Phosphor_CacheSounds(sfxinfo_t *sounds, int num_sounds)
{
    (void) sounds;
    (void) num_sounds;
}

sound_module_t DG_sound_module =
{
    sound_devices,
    arrlen(sound_devices),
    Phosphor_InitSound,
    Phosphor_ShutdownSound,
    Phosphor_GetSfxLumpNum,
    Phosphor_UpdateSound,
    Phosphor_UpdateSoundParams,
    Phosphor_StartSound,
    Phosphor_StopSound,
    Phosphor_SoundIsPlaying,
    Phosphor_CacheSounds,
};

//
// Music: not played. The page has no MUS synthesiser, so every call is a
// no-op and the game runs silent between sound effects.
//

static boolean Phosphor_InitMusic(void)
{
    return false;
}

static void Phosphor_ShutdownMusic(void)
{
}

static void Phosphor_SetMusicVolume(int volume)
{
    (void) volume;
}

static void Phosphor_PauseMusic(void)
{
}

static void Phosphor_ResumeMusic(void)
{
}

static void *Phosphor_RegisterSong(void *data, int len)
{
    (void) data;
    (void) len;
    return NULL;
}

static void Phosphor_UnRegisterSong(void *handle)
{
    (void) handle;
}

static void Phosphor_PlaySong(void *handle, boolean looping)
{
    (void) handle;
    (void) looping;
}

static void Phosphor_StopSong(void)
{
}

static boolean Phosphor_MusicIsPlaying(void)
{
    return false;
}

music_module_t DG_music_module =
{
    NULL,
    0,
    Phosphor_InitMusic,
    Phosphor_ShutdownMusic,
    Phosphor_SetMusicVolume,
    Phosphor_PauseMusic,
    Phosphor_ResumeMusic,
    Phosphor_RegisterSong,
    Phosphor_UnRegisterSong,
    Phosphor_PlaySong,
    Phosphor_StopSong,
    Phosphor_MusicIsPlaying,
    NULL,
};
