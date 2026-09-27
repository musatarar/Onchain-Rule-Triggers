/**
 * DOOM's sound channels as Web Audio voices: each lump becomes an AudioBuffer
 * the first time it plays, and each channel a source through its own gain and
 * stereo pan, as the DMX library mixed them.
 */
import type { Sample, Speaker } from './machine.ts';

type Voice = { source: AudioBufferSourceNode; gain: GainNode; pan: StereoPannerNode; ends: number };

export class WebSpeaker implements Speaker {
  private readonly context = new AudioContext();
  private readonly master = this.context.createGain();
  private readonly buffers = new Map<number, AudioBuffer | null>();
  private readonly voices = new Map<number, Voice>();

  constructor() {
    this.master.connect(this.context.destination);
  }

  set muted(muted: boolean) {
    this.master.gain.value = muted ? 0 : 1;
  }

  /** Browsers hold audio until the page is used; call from an input handler. */
  resume(): void {
    if (this.context.state === 'suspended') void this.context.resume();
  }

  close(): void {
    for (const channel of [...this.voices.keys()]) this.stop(channel);
    void this.context.close();
  }

  start(channel: number, lump: number, sample: () => Sample | null, vol: number, sep: number): void {
    this.stop(channel);
    let buffer = this.buffers.get(lump);
    if (buffer === undefined) {
      buffer = this.decode(sample());
      this.buffers.set(lump, buffer);
    }
    if (!buffer) return;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    const voice: Voice = { source, gain: this.context.createGain(), pan: this.context.createStereoPanner(), ends: this.context.currentTime + buffer.duration };
    source.connect(voice.gain).connect(voice.pan).connect(this.master);
    this.set(voice, vol, sep);
    source.onended = () => {
      if (this.voices.get(channel) === voice) this.voices.delete(channel);
    };
    source.start();
    this.voices.set(channel, voice);
  }

  update(channel: number, vol: number, sep: number): void {
    const voice = this.voices.get(channel);
    if (voice) this.set(voice, vol, sep);
  }

  stop(channel: number): void {
    const voice = this.voices.get(channel);
    if (!voice) return;
    this.voices.delete(channel);
    voice.source.onended = null;
    voice.source.stop();
    voice.source.disconnect();
  }

  /** Measured on the audio clock, so a sound still counts as playing while audio is held. */
  playing(channel: number): boolean {
    const voice = this.voices.get(channel);
    return !!voice && this.context.currentTime < voice.ends;
  }

  private decode(sample: Sample | null): AudioBuffer | null {
    if (!sample) return null;
    const buffer = this.context.createBuffer(1, sample.data.length, sample.rate);
    buffer.copyToChannel(sample.data, 0);
    return buffer;
  }

  private set(voice: Voice, vol: number, sep: number): void {
    voice.gain.gain.value = vol / 127;
    voice.pan.pan.value = Math.max(-1, Math.min(1, (sep - 128) / 127));
  }
}
