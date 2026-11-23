// Use peaks already in this stereo block to shape the attack without delaying the next tap.
class PadcadeLimiter extends AudioWorkletProcessor {
  constructor() {
    super();
    this.gain = 1;
    this.hold = 0;
    this.holdFrames = Math.round(.015 * sampleRate);
    this.recovery = 1 / (.1 * sampleRate);
  }

  process([input], [output]) {
    const left = input[0];
    if (!left) {
      this.gain = 1;
      this.hold = 0;
      return true;
    }
    const right = input[1] || left;
    const start = this.gain;
    let ceiling = 1, slope = 0;
    for (let i = 0; i < left.length; i++) {
      const peak = Math.max(Math.abs(left[i]), Math.abs(right[i]));
      if (peak <= 1) continue;
      const safe = 1 / peak;
      ceiling = Math.min(ceiling, safe);
      slope = Math.min(slope, (safe - start) / (i + 1));
    }
    if (ceiling === 1 && start === 1) {
      output[0].set(left);
      output[1].set(right);
      return true;
    }
    const outLeft = output[0], outRight = output[1];
    let level = start, hold = this.hold;
    if (slope === 0 && hold >= left.length) {
      for (let i = 0; i < left.length; i++) {
        outLeft[i] = left[i] * level;
        outRight[i] = right[i] * level;
      }
      hold -= left.length;
    } else {
      for (let i = 0; i < left.length; i++) {
        if (slope < 0) level = Math.max(ceiling, start + (i + 1) * slope);
        else if (hold) hold--;
        else level = Math.min(ceiling, level + this.recovery);   // (a quieter note after a loud hit comes back up)
        outLeft[i] = left[i] * level;
        outRight[i] = right[i] * level;
      }
    }
    this.gain = level;
    this.hold = ceiling < 1 && level >= ceiling ? this.holdFrames : hold;
    return true;
  }
}
registerProcessor('padcade-limiter', PadcadeLimiter);
