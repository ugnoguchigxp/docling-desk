import { useViewerSource } from "../viewer/data-source";
import * as contracts from "../lib/contracts";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  ContentCard,
  MetadataDisclosure,
  SelectField,
  StatusMessage,
} from "../components/ui";
import { fileUrl, request } from "../lib/api";
import { isDone, type Element, type Job, type RagPolicy } from "../lib/types";
export function Structure({ job, visible }: { job: Job; visible: boolean }) {
  const source = useViewerSource();
  const q = useQuery({
    queryKey: ["elements", job.id],
    queryFn: ({ signal }) =>
      request<Element[]>(
        source.url(fileUrl(job.id, "elements.json")),
        { signal },
        contracts.elements,
      ),
    enabled: visible && isDone(job),
  });
  return (
    <div
      id="structure"
      role="tabpanel"
      aria-labelledby="tab-structure"
      hidden={!visible}
    >
      {q.error?.message ||
        (q.isPending
          ? "構造・参照を読み込んでいます…"
          : q.data?.map((e) => (
              <ContentCard key={e.ref} title={`${e.label} · ${e.ref}`}>
                <p>{e.text || "画像・図要素（本文はJSONで参照）"}</p>
                <pre>{`ページ: ${e.pages.join(", ") || "位置情報なし"}\n親: ${e.parent || "なし"}\nキャプション参照: ${e.captions.join(", ") || "なし"}\n${JSON.stringify(e.provenance, null, 2)}`}</pre>
              </ContentCard>
            )))}
    </div>
  );
}
const files = {
  context: "rag.jsonl",
  search: "rag-index.jsonl",
  docling: "rag-docling.jsonl",
};
export function Rag({ job, visible }: { job: Job; visible: boolean }) {
  const [mode, setMode] = useState<keyof typeof files>("context");
  const source = useViewerSource();
  const q = useQuery({
    queryKey: ["rag", job.id, mode],
    queryFn: async ({ signal }) => {
      const [policy, response] = await Promise.all([
        request<RagPolicy>(
          source.url(fileUrl(job.id, "rag-policy.json")),
          { signal },
          contracts.policy,
        ),
        fetch(source.url(fileUrl(job.id, files[mode])), { signal }),
      ]);
      if (!response.ok) throw new Error("RAG出力を読み込めませんでした。");
      const chunks = (await response.text())
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((line) => contracts.chunk(JSON.parse(line)));
      return { policy, chunks };
    },
    enabled: visible && isDone(job),
  });
  const p = q.data?.policy,
    t = p?.tolerance_chars || 0,
    threshold = p?.split_threshold_chars ?? (p?.target_chars || 0) + t;
  const notice =
    mode === "context"
      ? "本文・表を対象とし、画像・図の情報は含めません。表の管理用タグや元行番号は本文から除いています。PPTは1スライド、XLSXは1シートをまとめています。長いシートは文脈の保存用です。検索用の小分けから親IDをたどって参照します。PDFは従来の見出し分割を保持しています。"
      : mode === "search"
        ? `画像・図の情報は含めません。表は行の途中で切らず、列見出しを繰り返します。表IDや元行番号は本文に含めず、出典情報に保持します。目安は${p?.target_chars}文字です。${t ? `猶予は${t}文字で、${threshold}文字までは分割しません。小さな末尾は${threshold}文字以内なら前の部分に統合します。シート名・列見出しも文字数に含めます。` : ""}埋め込みモデルのトークン上限は未検証です。`
        : "比較用にDoclingの分割を表示しています。画像・図の情報を除外した出力です。標準の文脈単位より細かい分割です。";
  const imageNotice =
    p?.image_content === "ocr_text"
      ? "画像から読み取った文字を含みます。画像の意味説明は生成していません。"
      : "画像・図の情報は含めません。";
  const [parent, setParent] = useState<string | null>(null);
  useEffect(() => {
    if (mode === "context" && parent && q.data) {
      document
        .querySelector<HTMLElement>(`[data-chunk-id="${CSS.escape(parent)}"]`)
        ?.scrollIntoView({ block: "start" });
      setParent(null);
    }
  }, [mode, parent, q.data]);
  return (
    <div id="rag" role="tabpanel" aria-labelledby="tab-rag" hidden={!visible}>
      <div className="rag-toolbar">
        <label>
          チャンクの表示
          <SelectField
            id="ragMode"
            aria-label="チャンクの表示"
            value={mode}
            onChange={(e) => setMode(e.target.value as keyof typeof files)}
          >
            <option value="context">文脈単位（標準）</option>
            <option value="search">検索用の小分け（親文脈付き）</option>
            <option value="docling">従来Docling分割（比較）</option>
          </SelectField>
        </label>
      </div>
      <StatusMessage id="ragStatus">
        {q.error?.message ||
          (p
            ? `${{ context: "文脈単位", search: "検索用の小分け", docling: "従来のDocling分割" }[mode]} ${q.data?.chunks.length} 件 · 親文脈 ${p.context_chunks} 件 · 検索用 ${p.search_chunks} 件 · 従来 ${p.docling_chunks} 件`
            : "RAGの文脈データを読み込んでいます…")}
      </StatusMessage>
      <p id="ragNotice">
        {p &&
          notice
            .replaceAll("画像・図の情報は含めません。", imageNotice)
            .replaceAll("画像・図の情報を除外した出力です。", imageNotice)}
      </p>
      <div id="ragCards">
        {q.data?.chunks.map((c, i) => (
          <ContentCard
            key={c.id}
            data-chunk-id={c.id}
            data-kind={c.kind || "docling"}
            data-parent-id={c.parent_id || ""}
            title={`${c.unit || c.headings.join(" › ") || "チャンク " + (i + 1)} · ${c.text.length.toLocaleString("ja")}文字${c.row_range?.length ? " · 元行 " + c.row_range.join("–") : ""}`}
          >
            <p>{c.text}</p>
            {c.oversize && (
              <p className="rag-size-note">
                {mode === "search"
                  ? "行・段落などのまとまりが分割基準を超えています。内容を切り捨てず保持しています。"
                  : "文字数の目安を超える親文脈です。検索用の小分けを別に用意しています。"}
              </p>
            )}
            {c.parent_id && (
              <Button
                onClick={() => {
                  setParent(c.parent_id!);
                  setMode("context");
                }}
              >
                親文脈を見る
              </Button>
            )}
            <MetadataDisclosure title="出典・要素参照">
              <pre>{`ID: ${c.id}\n親ID: ${c.parent_id || "なし"}\nページ: ${c.pages.join(", ") || "位置未取得"}\n参照: ${c.refs.join(", ")}\n補助文脈の参照: ${(c.context_refs || []).join(", ")}\n表とキャプション: ${JSON.stringify(c.relations || [])}\n出典: ${c.source}\nSHA256: ${c.source_sha256}`}</pre>
            </MetadataDisclosure>
          </ContentCard>
        ))}
      </div>
    </div>
  );
}
