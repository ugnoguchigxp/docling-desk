import * as contracts from "../lib/contracts";
import { withBase } from "../lib/base";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ActionLink,
  Button,
  MetadataDisclosure,
  SidePanel,
  StatusMessage,
  mostUrgent,
  type StatusKind,
} from "../components/ui";
import { useAsyncScope } from "../lib/async-scope";
import { errorText, post, request } from "../lib/api";
import { WATCH_MS, pollInterval } from "../lib/polling";
import { queryKeys } from "../lib/query-keys";
import {
  isDone,
  unitLabel,
  type ExplanationOverview,
  type ExplanationResult,
  type ExplanationUnit,
  type ExplanationValue,
  type Job,
} from "../lib/types";
const active = ["queued", "running"];
const stageKind = (state?: string): StatusKind =>
  state === "queued"
    ? "pending"
    : state && active.includes(state)
      ? "running"
      : state === "failed"
        ? "error"
        : "partial";
const webNames: Record<string, string> = {
  success: "本文を確認済み",
  partial: "一部を確認済み",
  failed: "調査に失敗",
  no_results: "該当情報なし",
  no_usable_evidence: "本文を確認できませんでした",
  skipped_sensitive: "検索語を送信せず省略",
  disabled_by_policy: "設定により無効",
  not_needed: "追加調査なし",
  send_not_confirmed: "送信の確認がないため検索しませんでした",
  held_numeric_query: "数字を含む検索語は保留しました",
};
/** The web line of an explanation: its status, then why a query was held back. */
export function webSummary(web: {
  status: string;
  observations?: { error_code?: string }[];
}) {
  const held = [
    ...new Set(
      (web.observations || [])
        .map((o) => o.error_code || "")
        .filter((code) => code === "send_not_confirmed" || code === "held_numeric_query"),
    ),
  ]
    .filter((code) => webNames[code] !== webNames[web.status])
    .map((code) => webNames[code]);
  return [webNames[web.status] || web.status, ...held].join(" / ");
}
function Refs({
  evidenceIds,
  value,
}: {
  evidenceIds: string[];
  value: ExplanationValue;
}) {
  return (
    <div className="explanation-refs">
      {[...new Set(evidenceIds)].map((id) => {
        const e = value.web_search.evidence.find(
          (e) => e.id === id && e.kind === "web",
        );
        if (!e) return null;
        try {
          const url = new URL(e.url || "");
          if (!["http:", "https:"].includes(url.protocol)) return null;
          return (
            <ActionLink
              key={id}
              href={url.href}
              target="_blank"
              rel="noopener noreferrer"
            >
              {e.title || "補足を詳しく読む"}
            </ActionLink>
          );
        } catch {
          return null;
        }
      })}
    </div>
  );
}
function ExplanationBody({
  value,
  canJump,
}: {
  value: ExplanationValue;
  canJump: boolean;
}) {
  return (
    <>
      <p>
        {`作成: ${new Date(value.created_at).toLocaleString("ja-JP")} · Web: ${webSummary(value.web_search)}`}
      </p>
      {value.explanation.sections.map((s, i) => (
        <div key={i}>
          <h3>{s.title || "詳しい解説"}</h3>
          <p>{s.text}</p>
        </div>
      ))}
      {value.explanation.glossary.length > 0 && (
        <>
          <h3>用語の説明</h3>
          {value.explanation.glossary.map((t, i) => (
            <div key={i}>
              <h4>{t.term}</h4>
              <p>{t.definition}</p>
            </div>
          ))}
        </>
      )}
      {value.explanation.supplements.length > 0 && (
        <>
          <h3>Webからの補足</h3>
          {value.explanation.supplements.map((s, i) => (
            <div key={i}>
              <h4>{s.title}</h4>
              <p>{s.text}</p>
              <Refs evidenceIds={s.evidence_ids} value={value} />
            </div>
          ))}
        </>
      )}
      {value.source.tables.map((t) => (
        <MetadataDisclosure key={t.ref} title={`${t.label} · 元の表を確認`}>
          <div className="explanation-table-scroll">
            <table aria-label={t.label}>
              <tbody>
                {t.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) =>
                      r < t.header_rows ? (
                        <th key={c}>{cell}</th>
                      ) : (
                        <td key={c}>{cell}</td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.merged && <p>結合セルの範囲は原本と保存JSONで確認できます。</p>}
        </MetadataDisclosure>
      ))}
      {!canJump && (
        <MetadataDisclosure
          id="explanationSavedSource"
          title="保存時の原文を確認"
        >
          {value.source.blocks.map((b) => (
            <p key={b.id}>{b.text}</p>
          ))}
        </MetadataDisclosure>
      )}
      <h3>対象と制約</h3>
      {value.explanation.limitations.map((s, i) => (
        <p key={i}>{s}</p>
      ))}
      <p>
        {`図 ${value.source.excluded_pictures}件は対象外です。読む位置を特定できなかった本文・表: ${value.unlocated_count}件。${value.extraction_state === "partial" ? "原文の抽出は部分成功です。" : ""}`}
      </p>
    </>
  );
}
export function useExplanation(job: Job, current: number | null) {
  const cache = useQueryClient(),
    [opened, setOpened] = useState(false),
    [busy, setBusy] = useState(false),
    [lastError, setLastError] = useState(""),
    lock = useRef(false),
    openLock = useRef(false),
    position = useRef(current);
  position.current = current;
  // Opening is tied to the unit shown: it expires when the unit changes, the
  // panel closes, the screen goes away or opening starts again.
  const scope = useAsyncScope(current);
  const overview = useQuery({
    queryKey: queryKeys.explanations(job.id),
    queryFn: ({ signal }) =>
      request<ExplanationOverview>(
        `/api/jobs/${job.id}/explanations`,
        { signal },
        contracts.explanations,
      ),
    enabled: isDone(job),
    // 1.5 s while generating. An open panel with nothing running only watches
    // for changes made elsewhere; a failed read backs off instead of retrying
    // every 1.5 s.
    refetchInterval: (q) =>
      pollInterval(q.state, {
        busy: 1500,
        isBusy: (data) =>
          data.units.some((u) => active.includes(u.state.state)),
        idle: opened ? WATCH_MS : false,
      }),
  });
  const data = overview.data,
    entry = data?.units.find((u) => u.number === current);
  const result = useQuery({
    queryKey: queryKeys.explanationResult(
      job.id,
      entry?.id,
      entry?.state.latest_version_id,
    ),
    queryFn: ({ signal }) =>
      request<ExplanationResult>(
        `/api/jobs/${job.id}/explanations/${entry!.id}`,
        { signal },
        contracts.explanationResult,
      ),
    enabled: opened && !!entry?.available,
  });
  const { invalidate } = scope;
  const close = useCallback(() => {
    invalidate();
    openLock.current = false;
    setOpened(false);
    document.getElementById("explainOpen")?.focus();
  }, [invalidate]);
  const { refetch } = overview;
  useEffect(() => {
    const online = () => {
      void refetch();
    };
    window.addEventListener("online", online);
    return () => window.removeEventListener("online", online);
  }, [refetch]);
  useEffect(() => {
    setLastError("");
    openLock.current = false;
  }, [current]);
  async function generate(unit: ExplanationUnit, force = false) {
    if (
      lock.current ||
      !data?.enabled ||
      data.source_error ||
      data.configuration_error ||
      unit.storage_error
    )
      return;
    lock.current = true;
    setBusy(true);
    setLastError("");
    try {
      await post(`/api/jobs/${job.id}/explanations`, {
        unit_id: unit.id,
        force,
      });
      await cache.invalidateQueries({
        queryKey: queryKeys.explanations(job.id),
      });
    } catch (e) {
      if (position.current === unit.number) setLastError(errorText(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function open() {
    if (openLock.current) return;
    openLock.current = true;
    const isCurrent = scope.start(),
      requested = position.current;
    setOpened(true);
    requestAnimationFrame(() => {
      if (isCurrent()) document.getElementById("explanationPanel")?.focus();
    });
    try {
      const result = await overview.refetch();
      if (!isCurrent() || result.error) return;
      const unit = result.data?.units.find((u) => u.number === requested);
      if (
        unit &&
        !unit.available &&
        unit.state.state === "uncreated" &&
        result.data?.enabled &&
        !result.data.source_error &&
        !result.data.configuration_error &&
        !unit.storage_error
      ) {
        if (lock.current) return;
        lock.current = true;
        setBusy(true);
        try {
          await post(`/api/jobs/${job.id}/explanations`, {
            unit_id: unit.id,
            force: false,
          });
          await cache.invalidateQueries({
            queryKey: queryKeys.explanations(job.id),
          });
        } catch (e) {
          if (position.current === requested) setLastError(errorText(e));
        } finally {
          lock.current = false;
          setBusy(false);
        }
      }
    } finally {
      if (isCurrent()) openLock.current = false;
    }
  }
  const retryVisible =
    !!entry &&
    !entry.available &&
    !active.includes(entry.state.state) &&
    (entry.state.state !== "not_applicable" || entry.stale) &&
    data?.enabled &&
    !entry.storage_error;
  const regenerateVisible =
    entry?.available &&
    !active.includes(entry.state.state) &&
    data?.enabled &&
    !entry.storage_error;
  // One paragraph; the most urgent kind sets how it is announced.
  const notices: [StatusKind, string | undefined][] = [
    [stageKind(entry?.state.state), entry?.state.stage],
    ["error", entry?.state.error],
    [
      "error",
      entry?.state.state === "failed"
        ? entry.state.review_issues?.slice(0, 5).join("\n")
        : "",
    ],
    ["partial", entry?.warning],
    ["error", lastError],
    [
      "error",
      overview.error
        ? `状態の確認を再試行しています。${overview.error.message}`
        : "",
    ],
    ["error", result.error?.message],
    [
      "stale",
      entry?.stale
        ? entry.available
          ? "原文が更新されています。保存時の解説を表示します。"
          : "原文が更新されています。再試行できます。"
        : "",
    ],
    ["error", data?.source_error ?? undefined],
    ["setup", data?.configuration_error ?? undefined],
  ];
  const shown = notices.filter(([, text]) => text);
  const status = shown.map(([, text]) => text).join("\n");
  const controls = (
    <Button
      id="explainOpen"
      aria-controls="explanationPanel"
      aria-expanded={opened}
      disabled={!entry || busy || (!data?.enabled && !entry.available)}
      title={
        entry
          ? `${entry.kind === "document" ? "" : "現在の"}${unitLabel(entry.kind, entry.number)}の解説`
          : "現在のページ・スライド・シートを確認しています。"
      }
      onClick={() => void open()}
    >
      わかりやすく解説
    </Button>
  );
  const panel = (
    <SidePanel
      id="explanationPanel"
      aria-label="詳しい解説"
      hidden={!opened}
      tabIndex={-1}
      heading="explanationTitle"
      title={
        entry
          ? `${unitLabel(entry.kind, entry.number)}${entry.name ? ` · ${entry.name}` : ""} の解説`
          : "現在の範囲を確認しています"
      }
      headingClass="explanation-heading"
      closeId="explainClose"
      onClose={close}
      onKeyDown={(e) => {
        if (e.key === "Escape") close();
      }}
    >
      <StatusMessage
        id="explanationStatus"
        kind={mostUrgent(shown.map(([kind]) => kind))}
      >
        {status}
      </StatusMessage>
      <div id="explanationActions">
        <Button
          id="explainRetry"
          hidden={!retryVisible}
          disabled={busy || !!data?.source_error || !!data?.configuration_error}
          onClick={() => entry && void generate(entry)}
        >
          解説を再試行
        </Button>
        <Button
          id="explainRegenerate"
          hidden={!regenerateVisible}
          disabled={busy || !!data?.source_error || !!data?.configuration_error}
          onClick={() => entry && void generate(entry, true)}
        >
          解説を作り直す
        </Button>
      </div>
      <div id="explanationText">
        {entry?.available && result.data?.result && (
          <ExplanationBody
            value={result.data.result}
            canJump={result.data.source_match === true}
          />
        )}
      </div>
    </SidePanel>
  );
  const provider = data?.profile.web_provider || "未設定";
  const disclosure = `未作成の解説は本文・表をOpenAIへ送信します。Web補足: ${provider === "disabled" ? "無効" : provider + "で一般的な用語を検索"}。保存版は再生成せず開きます。`;
  const downloads = entry?.available
    ? (["markdown", "json"] as const).map((format) => (
        <ActionLink
          key={format}
          className="explanation-download"
          href={withBase(`/api/jobs/${job.id}/explanations/${entry.id}?download=${format}`)}
        >
          {format === "markdown"
            ? "解説を書き出す（Markdown）"
            : "解説と根拠を書き出す（JSON）"}
        </ActionLink>
      ))
    : null;
  return {
    opened,
    controls,
    panel,
    downloads,
    disclosure: data ? disclosure : "",
  };
}
