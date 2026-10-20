import { useCallback, useEffect, useRef, useState } from "react";
import { ActionLink, Button, StatusMessage } from "../../components/ui";
import { errorText } from "../../lib/api";
import { withBase } from "../../lib/base";
import {
  diagramContentUrl,
  listRevisions,
  type RevisionInfo,
} from "../../lib/diagrams";
import { when } from "./problems";

const PAGE = 20;

/** Version list with restore. `refreshKey` changes when the list is stale. */
export function HistoryPanel({
  id,
  refreshKey,
  busy,
  onRestore,
}: {
  id: string;
  refreshKey: number;
  busy: boolean;
  onRestore: (revision: RevisionInfo) => void;
}) {
  const [revisions, setRevisions] = useState<RevisionInfo[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const request = useRef(0);

  const show = useCallback(
    async (before: number | null) => {
      const mine = ++request.current;
      setLoading(true);
      setError("");
      try {
        const page = await listRevisions(id, { before, limit: PAGE });
        if (mine !== request.current) return;
        setRevisions((old) =>
          before == null ? page.items : [...old, ...page.items],
        );
        setNext(page.next);
      } catch (failure) {
        if (mine === request.current) setError(errorText(failure));
      } finally {
        if (mine === request.current) setLoading(false);
      }
    },
    [id],
  );
  useEffect(() => {
    void show(null);
    return () => {
      // A counter, not a DOM node: invalidate any response still on its way.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      request.current++;
    };
  }, [show, refreshKey]);

  return (
    <aside
      id="diagramHistory"
      className="diagram-editor-history"
      aria-label="版の履歴"
    >
      <h2>版の履歴</h2>
      {error && <p role="alert">{error}</p>}
      <ol>
        {revisions.map((revision) => (
          <li key={revision.id}>
            <span>
              版 {revision.number}
              {revision.current && "（現在）"}
              {revision.restored_from && "（復元）"}
            </span>
            <small>{when(revision.created)}</small>
            <span className="diagram-editor-revision-actions">
              {!revision.current && (
                <Button
                  onClick={() => onRestore(revision)}
                  disabled={busy}
                  aria-label={`版 ${revision.number} に戻す`}
                >
                  この版に戻す
                </Button>
              )}
              <ActionLink
                href={withBase(
                  diagramContentUrl(id, {
                    revision: revision.id,
                    download: true,
                  }),
                )}
                aria-label={`版 ${revision.number} を保存`}
              >
                保存
              </ActionLink>
            </span>
          </li>
        ))}
      </ol>
      {loading && <StatusMessage>読み込み中…</StatusMessage>}
      {next != null && !loading && (
        <Button onClick={() => void show(next)}>さらに読み込む</Button>
      )}
    </aside>
  );
}
