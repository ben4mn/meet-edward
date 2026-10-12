// Which container MediaRecorder should produce. Chrome and Firefox do
// webm/opus; Safari (iOS included) does mp4/aac. The backend accepts both.

export interface RecorderFormat {
  mimeType: string;
  extension: string;
}

const CANDIDATES: RecorderFormat[] = [
  { mimeType: "audio/webm;codecs=opus", extension: "webm" },
  { mimeType: "audio/webm", extension: "webm" },
  { mimeType: "audio/mp4", extension: "m4a" },
  { mimeType: "audio/ogg;codecs=opus", extension: "ogg" },
];

export function pickRecorderMime(isSupported: (mime: string) => boolean): RecorderFormat {
  for (const c of CANDIDATES) {
    if (isSupported(c.mimeType)) return c;
  }
  return { mimeType: "", extension: "webm" };
}
