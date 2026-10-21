import { Button, StatusMessage } from "../../components/ui";
import type { IndexStatus, Task } from "./types";

/** Semantic-search index progress, with the actions the server offers. */
export function IndexPanel({
  status,
  indexJob,
  busy,
  onIndex,
  onCancel,
}: {
  status: IndexStatus | undefined;
  indexJob: Task | undefined;
  busy: boolean;
  onIndex: () => void;
  onCancel: (id: string) => void;
}) {
  return (
    <div className="knowledge-index">
      <span>
        意味検索の準備：{status?.embedded || 0} / {status?.chunks || 0}断片
      </span>
      {status?.azure_configured ? (
        <>
          <Button disabled={busy || !!indexJob} onClick={onIndex}>
            意味検索の索引を作成・更新
          </Button>
          <small>Azureへの通信後は15秒待ちます。</small>
        </>
      ) : (
        <small>
          {status?.configuration_error ||
            "Azure未設定。全文検索を利用できます。"}
        </small>
      )}
      {indexJob && (
        <>
          <span>
            索引作成中 {indexJob.progress} / {indexJob.total}
          </span>
          <Button disabled={busy} onClick={() => onCancel(indexJob.id)}>
            索引作成を中止
          </Button>
        </>
      )}
      {!indexJob && status?.jobs[0]?.state === "failed" && (
        <StatusMessage kind="error">{status.jobs[0].error}</StatusMessage>
      )}
    </div>
  );
}
