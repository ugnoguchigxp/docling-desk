import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Button } from "../components/ui";
import { IconDefinitions } from "../components/Icons";
import { request, errorText } from "../lib/api";
import type { View } from "../lib/types";
import { DocumentViewer } from "../viewer/DocumentViewer";
import { ViewerSource, sessionViewerSource } from "../viewer/data-source";
import type { DocumentReference, ViewerManifest } from "../viewer/reference";
import { askHost, connectHost, reportHeight } from "../viewer/hosts/open-webui";
import "./style.css";

const cache = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
function viewerError(error: unknown) {
  const code = errorText(error);
  return (
    (
      {
        viewer_session_expired:
          "閲覧の有効期限が切れました。資料へ再接続してください。",
        viewer_code_expired:
          "接続コードの有効期限が切れました。新しいコードで再接続してください。",
        viewer_session_revoked:
          "閲覧権限が変更されました。資料へ再接続してください。",
        viewer_user_forbidden: "この利用者には資料の閲覧権限がありません。",
        source_not_found: "資料が削除されたか、閲覧権限がありません。",
        source_changed:
          "資料が更新されました。チャットで検索し直し、新しい資料参照を開いてください。",
        evidence_changed:
          "資料の解析結果が更新されました。新しい検索結果から開いてください。",
        preview_unavailable: "この資料のプレビューはまだ利用できません。",
        invalid_location:
          "指定された出典位置を表示できません。資料参照を確認してください。",
        viewer_api_unavailable:
          "資料サービスに接続できません。しばらくしてから再接続してください。",
      } as Record<string, string>
    )[code] || code
  );
}
function Entry() {
  const [attempt, setAttempt] = useState(0);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [session, setSession] = useState("");
  const [manifest, setManifest] = useState<ViewerManifest | null>(null);
  const [view, setView] = useState<View>("preview");
  const [current, setCurrent] = useState(1);
  const [question, setQuestion] = useState("");
  useEffect(() => {
    let cancelled = false;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const start = async () => {
      try {
        const raw = new URL(location.href).searchParams.get("ref");
        if (!raw || raw.length > 2048)
          throw new Error("資料参照がありません。");
        const reference: DocumentReference = JSON.parse(raw);
        const challenge = await request<{ code: string; challenge: string }>(
          "/viewer/challenges",
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(reference),
            signal: abort.signal,
          },
        );
        if (cancelled) return;
        setCode(challenge.code);
        const poll = async () => {
          try {
            const result = await request<{ status: string; session?: string }>(
              `/viewer/challenges/${challenge.challenge}`,
              { signal: abort.signal },
            );
            if (cancelled) return;
            if (result.session) {
              setSession(result.session);
              setCode("");
            } else timer = setTimeout(() => void poll(), 2000);
          } catch (e) {
            if (!cancelled) {
              setCode("");
              setError(viewerError(e));
            }
          }
        };
        timer = setTimeout(() => void poll(), 1000);
      } catch (e) {
        if (!cancelled) setError(viewerError(e));
      }
    };
    void start();
    return () => {
      cancelled = true;
      abort.abort();
      clearTimeout(timer);
    };
  }, [attempt]);
  useEffect(() => {
    if (!session) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      try {
        const data = await request<ViewerManifest>(
          `/viewer/session/${session}/manifest`,
          { signal: abort.signal },
        );
        if (abort.signal.aborted) return;
        setManifest(data);
        timer = setTimeout(() => void check(), 15000);
      } catch (e) {
        if (abort.signal.aborted) return;
        setManifest(null);
        setSession("");
        cache.clear();
        setError(viewerError(e));
      }
    };
    void check();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [session]);
  useEffect(() => {
    reportHeight();
    window.addEventListener("resize", reportHeight);
    return () => window.removeEventListener("resize", reportHeight);
  }, []);
  const ask = useCallback(
    (text: string, unit: number) => {
      if (!manifest) return;
      setQuestion(
        askHost(text, {
          ...manifest.reference,
          location: { kind: manifest.kind, number: unit },
        }),
      );
    },
    [manifest],
  );
  const reconnect = () => {
    setManifest(null);
    setSession("");
    cache.clear();
    setError("");
    setQuestion("");
    setCode("");
    setAttempt((v) => v + 1);
  };
  if (!manifest || !session)
    return (
      <main className="viewer-connect">
        <h1>資料をチャットで閲覧</h1>
        <p>
          この画面の接続コードをチャットで送信してください。
          現在の利用者の閲覧権限を確認します。
        </p>
        {code && (
          <>
            <strong aria-label="接続コード">{code}</strong>
            <p>
              <Button onClick={() => connectHost(code)}>
                接続コードをチャット入力へ
              </Button>
            </p>
          </>
        )}
        <p role="status">
          {error ||
            (!code
              ? "閲覧画面へ接続しています…"
              : "接続コードは3分間有効です。チャットの再表示時も接続してください。")}
        </p>
        {error && <Button onClick={reconnect}>資料へ再接続</Button>}
      </main>
    );
  const base = new URL(`/viewer/session/${session}/`, location.href).href;
  return (
    <ViewerSource value={sessionViewerSource(base)}>
      <DocumentViewer
        key={session}
        job={manifest.job}
        view={view}
        onView={setView}
        onBack={() => {}}
        message={
          manifest.reference.location
            ? ""
            : "出典位置は未特定です。文書の先頭から表示しています。"
        }
        initialLanguage="original"
        onLanguage={() => {}}
        embedded
        initialUnit={manifest.reference.location?.number || 1}
        onUnitChange={(unit) => setCurrent(unit || 1)}
        onQuestion={ask}
      />
      <footer className="viewer-footer">
        <span>
          {manifest.kind === "document" ? "文書全体" : `位置 ${current}`}
        </span>
        <Button onClick={reconnect}>資料へ再接続</Button>
      </footer>
      {question && (
        <details className="viewer-question">
          <summary>
            チャットに渡す質問（他のホストではコピーしてください）
          </summary>
          <textarea aria-label="チャットに渡す質問" readOnly value={question} />
        </details>
      )}
    </ViewerSource>
  );
}
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={cache}>
    <IconDefinitions />
    <Entry />
  </QueryClientProvider>,
);
