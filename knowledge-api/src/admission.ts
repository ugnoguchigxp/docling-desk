import { statfs } from "node:fs/promises";
import { ApiError } from "./contracts";

/** Shared by source uploads, Markdown saves and attachments in one API owner. */
export class UploadAdmission {
  active = 0;
  private reserved = 0;
  constructor(readonly root: string) {}

  acquire() {
    if (this.active >= 3) throw new ApiError(429, "queue_full", "Upload concurrency limit", true);
    this.active++;
    return () => {
      this.active--;
    };
  }

  async reserve(bytes: number) {
    const required = bytes * 4;
    this.reserved += required;
    try {
      const disk = await statfs(this.root);
      if (disk.bavail * disk.bsize < 2 * 1024 ** 3 + this.reserved)
        throw new ApiError(507, "insufficient_storage");
    } catch (error) {
      this.reserved -= required;
      throw error;
    }
    return () => {
      this.reserved -= required;
    };
  }
}
