import type { z } from "zod";
import { ApiError, MAX_JSON } from "../contracts";
import type { patchDraftSchema } from "./contract";
import { sha256 } from "./hash";
export function applyEdits(body: string, input: z.infer<typeof patchDraftSchema>): string {
  if (sha256(body) !== input.base_sha256) throw new ApiError(409, "body_changed");
  const bytes = Buffer.from(body);
  const edits = input.edits
    .map((edit) => {
      let start: number, end: number;
      if ("start" in edit) {
        start = edit.start;
        end = edit.end;
      } else {
        const needle = Buffer.from(edit.expected);
        start = bytes.indexOf(needle);
        if (start < 0) throw new ApiError(409, "edit_target_missing");
        if (bytes.indexOf(needle, start + 1) >= 0) throw new ApiError(409, "edit_target_ambiguous");
        end = start + needle.length;
      }
      if (
        start > end ||
        end > bytes.length ||
        (start < bytes.length && ((bytes[start] ?? 0) & 0xc0) === 0x80) ||
        (end < bytes.length && ((bytes[end] ?? 0) & 0xc0) === 0x80)
      )
        throw new ApiError(422, "invalid_edit_range");
      if (bytes.subarray(start, end).toString("utf8") !== edit.expected)
        throw new ApiError(409, "edit_target_mismatch");
      return { start, end, replacement: Buffer.from(edit.replacement) };
    })
    .sort((a, b) => a.start - b.start || a.end - b.end);
  for (let i = 1; i < edits.length; i++) {
    const current = edits[i],
      previous = edits[i - 1];
    if (!current || !previous || current.start < previous.end || current.start === previous.start)
      throw new ApiError(422, "overlapping_edits");
  }
  let result = bytes;
  for (const edit of edits.reverse())
    result = Buffer.concat([
      result.subarray(0, edit.start),
      edit.replacement,
      result.subarray(edit.end),
    ]);
  if (result.length > MAX_JSON) throw new ApiError(413, "body_too_large");
  return result.toString("utf8");
}
