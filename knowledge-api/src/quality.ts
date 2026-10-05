/** Crash boundaries for tests. Normal runs leave DOCLING_QUALITY_FAULT unset. */
export class QualityFault extends Error {
  constructor(readonly checkpoint: string) {
    super(checkpoint);
    this.name = "QualityFault";
  }
}

export function checkpoint(name: string) {
  const raw = process.env.DOCLING_FAULT ?? "";
  const separator = raw.indexOf(":");
  if (separator > 0 && raw.slice(0, separator) === "exit" && raw.slice(separator + 1) === name)
    process.kill(process.pid, "SIGKILL");
  const raised =
    process.env.DOCLING_QUALITY_FAULT === name ||
    (separator > 0 && raw.slice(0, separator) === "raise" && raw.slice(separator + 1) === name);
  if (raised) throw new QualityFault(name);
}
