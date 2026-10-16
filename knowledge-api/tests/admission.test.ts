import { expect, spyOn, test } from "bun:test";
import * as fs from "node:fs/promises";
import { UploadAdmission } from "../src/admission";

test("storage reservations include other in-flight writes and recover after refusals", async () => {
  const disk = spyOn(fs, "statfs").mockResolvedValue({
    bavail: 2 * 1024 ** 3 + 80,
    bsize: 1,
  } as Awaited<ReturnType<typeof fs.statfs>>);
  try {
    const admission = new UploadAdmission("unused synthetic root");
    const release = await admission.reserve(10);
    await expect(admission.reserve(11)).rejects.toMatchObject({ status: 507 });
    release();
    const whole = await admission.reserve(20);
    whole();
    disk.mockRejectedValueOnce(new Error("synthetic storage failure"));
    await expect(admission.reserve(20)).rejects.toThrow("synthetic storage failure");
    (await admission.reserve(20))();
  } finally {
    disk.mockRestore();
  }
});
