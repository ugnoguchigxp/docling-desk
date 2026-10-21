import type { MouseEvent } from "react";
import { Button, StatusMessage } from "../../components/ui";
import { hitKey, hitUrl, searchSnippet } from "./hit";
import type { Operation } from "./useKnowledgeRetrieval";
import type { Hit, Task } from "./types";

/**
 * What a retrieval returned: the waiting notice, the outdated notice, the
 * answer with its citations, and the hits the server selected. It shows what
 * the task carries and reaches the server only through `onCancel`; which hits
 * were chosen and how the answer reads are the server's decisions.
 */
export function SearchResults({
  current,
  waiting,
  operation,
  query,
  busy,
  openedKey,
  onCancel,
  onOpen,
}: {
  current: Task | null;
  waiting: boolean;
  operation: Operation;
  /** The text the person typed, only for placing the snippet window. */
  query: string;
  busy: boolean;
  /** The hit the person opened last (see `hitKey`); marked when they return. */
  openedKey?: string | null;
  onCancel: (id: string) => void;
  onOpen: (hit: Hit, key: string) => void;
}) {
  function openHit(e: MouseEvent<HTMLAnchorElement>, hit: Hit, key: string) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0)
      return;
    e.preventDefault();
    onOpen(hit, key);
  }
  return (
    <>
      {waiting && current && (
        <StatusMessage kind="running">
          {operation === "rag"
            ? "関連する本文を選び、回答を作成しています…"
            : "検索を処理中です。Azureの待ち時間も含みます。"}
          <Button disabled={busy} onClick={() => onCancel(current.id)}>
            {operation === "rag" ? "RAGを中止" : "検索を中止"}
          </Button>
        </StatusMessage>
      )}
      {current?.output.stale && (
        <StatusMessage kind="stale">
          出典が更新されました。検索をやり直してください。
        </StatusMessage>
      )}
      {current?.state === "completed" &&
        !current.output.results?.length &&
        !current.output.answer && <p>該当する本文はありません。</p>}
      {current?.output.answer && !current.output.stale && (
        <section className="knowledge-answer" aria-label="RAGの回答">
          <h3>回答</h3>
          <div className="knowledge-answer-text">
            {current.output.answer.split(/(\[S\d+\])/g).map((part, i) => {
              const citation = current.output.citations?.find(
                (c) => `[${c.label}]` === part,
              );
              return citation ? (
                <a
                  key={i}
                  href={hitUrl(citation)}
                  data-hit-key={hitKey(citation, citation.label)}
                  data-opened={
                    openedKey === hitKey(citation, citation.label) || undefined
                  }
                  onClick={(e) =>
                    openHit(e, citation, hitKey(citation, citation.label))
                  }
                  aria-label={`${part} ${citation.title}の本文を開く`}
                >
                  {part}
                </a>
              ) : (
                part
              );
            })}
          </div>
          {!!current.output.unknowns?.length && (
            <div className="knowledge-answer-unknowns">
              <h4>本文から確認できないこと</h4>
              <ul>
                {current.output.unknowns.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      {!!current?.output.results?.length && (
        <p className="knowledge-result-count">
          {operation === "rag" ? "採用した本文" : "検索結果"} ·{" "}
          {current.output.results.length}件
        </p>
      )}
      <ul className="knowledge-results">
        {current?.output.results?.map((hit) => (
          <li key={hit.chunk_id}>
            <div className="knowledge-result-source">
              <span>{hit.kind === "wiki" ? "Wiki" : "資料"}</span>
              {hit.locator.heading && <span> › {hit.locator.heading}</span>}
              {hit.kind === "document" && <span> · 表示位置 {hit.unit}</span>}
              {hit.partial && <span> · 一部抽出</span>}
            </div>
            <a
              className="knowledge-result-title"
              href={hitUrl(hit)}
              data-hit-key={hitKey(hit)}
              data-opened={openedKey === hitKey(hit) || undefined}
              aria-label={`${hit.kind === "wiki" ? "Wiki" : "資料"} · ${hit.title}`}
              onClick={(e) => openHit(e, hit, hitKey(hit))}
            >
              {hit.title}
            </a>
            <p className="knowledge-result-snippet">
              {searchSnippet(hit.text, query)}
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}
