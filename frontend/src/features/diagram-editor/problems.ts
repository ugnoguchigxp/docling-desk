import { errorText } from "../../lib/api";
import { HttpError } from "../../lib/http";

export interface Problem {
  /** `conflict` means someone else changed the server state. */
  kind: "conflict" | "failure";
  message: string;
  retry: boolean;
  /** What the retry button repeats. */
  action?: "save" | "restore";
  /** The server state is ahead of the canvas: offer to load it. */
  reload?: boolean;
}

export const when = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString("ja-JP");
export const time = (date: Date) => date.toLocaleTimeString("ja-JP");

/** The server will never accept this content as is (too large / invalid). */
export const isPermanent = (error: unknown) =>
  error instanceof HttpError && (error.status === 413 || error.status === 422);

export function classify(
  error: unknown,
  action: "save" | "draft" | "restore",
): Problem {
  const again = action === "restore" ? "restore" : "save";
  if (!(error instanceof HttpError))
    return {
      kind: "failure",
      message: errorText(error),
      retry: true,
      action: again,
    };
  const kept = "編集中の内容はこの画面に残っています。";
  switch (error.status) {
    case 0:
      return {
        kind: "failure",
        message: `通信できませんでした。${kept}通信を確認して再試行してください。`,
        retry: true,
        action: again,
      };
    case 412:
      return {
        kind: "conflict",
        message: `この図表は他の場所で更新されたため${action === "restore" ? "復元" : "保存"}できません。${kept}`,
        retry: false,
      };
    case 404:
      return {
        kind: "conflict",
        message: `図表または下書きが見つかりません。${kept}`,
        retry: false,
      };
    case 409:
      if (action === "draft")
        return {
          kind: "conflict",
          message: `別の画面で下書きが作成されています。${kept}`,
          retry: false,
        };
      return {
        kind: "conflict",
        message:
          action === "restore"
            ? `復元の再送内容が前回と一致しません。${kept}もう一度操作してください。`
            : `保存の再送内容が前回と一致しません。${kept}もう一度保存してください。`,
        retry: true,
        action: again,
      };
    case 413:
    case 422:
      return {
        kind: "failure",
        message: `サーバーが図表の内容を受け付けませんでした（${error.status}）。${error.detail} ${kept}`,
        retry: false,
      };
    default:
      return {
        kind: "failure",
        message: `${error.detail} ${kept}`,
        retry: action !== "draft",
        action: again,
      };
  }
}
