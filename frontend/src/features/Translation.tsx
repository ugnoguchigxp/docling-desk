import * as contracts from "../lib/contracts";
import { withBase } from "../lib/base";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ActionLink,
  Button,
  CheckboxField,
  Dialog,
  DialogHeading,
  SelectField,
  SidePanel,
  StatusMessage,
  TextField,
  mostUrgent,
  type Recovery,
  type StatusKind,
} from "../components/ui";
import { useAsyncScope } from "../lib/async-scope";
import { errorText, post, request } from "../lib/api";
import { pollInterval } from "../lib/polling";
import { queryKeys } from "../lib/query-keys";
import {
  isDone,
  isLightweightPreview,
  unitLabel,
  unitNames,
  type Job,
  type Language,
  type TranslationOverview,
} from "../lib/types";
const active = ["queued", "waiting", "running"];
type PanelResult = ReturnType<typeof contracts.translationPanel>;
interface Notice {
  kind: StatusKind;
  text: string;
}
/** How a stored unit that has no usable translation is shown. */
const recordKind: Record<string, StatusKind> = {
  queued: "pending",
  waiting: "pending",
  running: "running",
  failed: "error",
  interrupted: "partial",
  untranslated: "empty",
};
const states: Record<string, string> = {
  queued: "順番待ち",
  waiting: "間隔を空けて待機中",
  running: "翻訳中",
  completed: "翻訳済み",
  failed: "失敗",
  interrupted: "中断",
  untranslated: "未翻訳",
};
export function useTranslation(
  job: Job,
  current: number,
  language: Language,
  onLanguage: (lang: Language) => void,
) {
  const cache = useQueryClient();
  // The dialog is for the unit shown when it was asked for.
  const opening = useAsyncScope(`${job.id}:${current}`);
  const lightweight = !!job.preview && isLightweightPreview(job);
  const overview = useQuery({
    queryKey: queryKeys.translations(job.id),
    queryFn: ({ signal }) =>
      request<TranslationOverview>(
        `/api/jobs/${job.id}/translations`,
        { signal },
        contracts.translations,
      ),
    enabled: isDone(job) && !lightweight,
    retry: false,
    refetchOnWindowFocus: false,
    // Only while the server translates; a failed read stretches the interval
    // instead of stopping it, so progress is not lost to one dropped request.
    refetchInterval: (q) =>
      pollInterval(q.state, {
        busy: 1500,
        isBusy: (data) =>
          data.units.some((u) =>
            Object.values(u.languages).some((r) => active.includes(r.state)),
          ),
        idle: false,
      }),
  });
  const data = lightweight ? undefined : overview.data,
    unit = data?.units.find((u) => u.number === current) || data?.units[0],
    record = language === "original" ? null : unit?.languages[language],
    revision = record?.result_created_at || "";
  const panel = useQuery({
    queryKey: queryKeys.translationResult(job.id, unit?.id, language, revision),
    queryFn: ({ signal }) =>
      request<PanelResult>(
        `/api/jobs/${job.id}/translations/${language}/${unit!.id}`,
        { signal },
        contracts.translationPanel,
      ),
    // A translation saved against an older page layout cannot be placed on the
    // page; the server lists it beside its original text instead.
    enabled:
      !lightweight &&
      ((!!record?.available && unit?.mode === "panel") || !!record?.stale),
  });
  const legacy =
    !record?.available && !!panel.data?.legacy && !!panel.data.pairs?.length;
  const showPanel =
    legacy ||
    (!!record?.available && unit?.mode === "panel" && !!panel.data?.available);
  const [opened, setOpened] = useState(false),
    [target, setTarget] = useState<"en" | "ja">("en"),
    [scope, setScope] = useState("current"),
    [interval, setInterval] = useState(60),
    [force, setForce] = useState(false),
    [selected, setSelected] = useState<string[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const lock = useRef(false),
    dialogLock = useRef(false);
  async function open() {
    if (lightweight || dialogLock.current) return;
    dialogLock.current = true;
    const isCurrent = opening.start();
    try {
      const result = await overview.refetch();
      if (!isCurrent()) return;
      if (result.error || !result.data) {
        setError(errorText(result.error));
        return;
      }
      setOpened(true);
      setError("");
      setTarget(language === "ja" ? "ja" : "en");
      setForce(false);
      const selectedUnit =
        result.data.units.find((u) => u.number === current) ||
        result.data.units[0];
      setSelected(selectedUnit ? [selectedUnit.id] : []);
      setInterval(result.data.scheduling?.interval_seconds ?? 60);
    } finally {
      dialogLock.current = false;
    }
  }
  async function submit() {
    if (lightweight || !data || !unit || lock.current) return;
    const ids =
      scope === "all" ? null : scope === "current" ? [unit.id] : selected;
    if (ids && !ids.length) {
      setError("翻訳する範囲を選択してください。");
      return;
    }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await post(`/api/jobs/${job.id}/translations`, {
        target_language: target,
        unit_ids: ids,
        force,
        interval_seconds: interval,
      });
      setOpened(false);
      onLanguage(target);
      await cache.invalidateQueries({
        queryKey: queryKeys.translations(job.id),
      });
    } catch (e) {
      setError(errorText(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  const notices: Notice[] = [];
  const note = (kind: StatusKind, text: string) => notices.push({ kind, text });
  if (unit?.preview_unavailable_reason)
    note("partial", unit.preview_unavailable_reason);
  if (unit?.unavailable_reason) note("partial", unit.unavailable_reason);
  if (data) {
    const count = data.units.filter((u) =>
      Object.values(u.languages).some((r) => active.includes(r.state)),
    ).length;
    if (count) note("running", `翻訳処理中 · 残り ${count}単位`);
    const waiting = data.units
      .flatMap((u) => Object.values(u.languages))
      .find((r) => r.state === "waiting");
    if (waiting) {
      const seconds = Math.max(
        0,
        Math.ceil(
          (Date.parse(waiting.next_attempt_at || "") - Date.now()) / 1000,
        ),
      );
      note(
        "pending",
        `${waiting.wait_reason === "rate_limit" ? "レート制限のため" : waiting.wait_reason === "retry" ? "再試行まで" : "次の翻訳まで"}約${seconds}秒待機します。`,
      );
    }
    if (record && unit) {
      if (record.stale)
        note("stale", "原文が更新されています。再翻訳してください。");
      else if (!record.available)
        note(
          recordKind[record.state] ?? "partial",
          `${unitLabel(unit.kind, unit.number)} は${states[record.state] || record.state}です。原文を表示しています。`,
        );
      if (legacy)
        note(
          "partial",
          "以前の表示方式で保存した訳文を、原文と並べて一覧で表示しています。翻訳をやり直すと原本上の位置に表示します。",
        );
      if (record.error) note("error", record.error);
      if (unit.kind === "slide") note("partial", "サムネイルは原文です。");
      if (unit.excluded_count)
        note(
          "partial",
          `対応を確定できない文字セル ${unit.excluded_count}件は原文のままです。`,
        );
      if (record.available && !unit.segments_count)
        note("empty", "翻訳対象の文字がありません。");
    }
    if (data.unlocated_count)
      note(
        "partial",
        `ページ位置のない要素 ${data.unlocated_count}件は翻訳対象外です。`,
      );
  }
  if (!lightweight && overview.error) note("error", overview.error.message);
  if (!lightweight && panel.error) note("error", panel.error.message);
  // Waiting and running are the server's normal progress; only a stored
  // failure, an interruption or an outdated source can be answered by
  // translating again, and only a failed read by reading again.
  const recovery: Recovery[] = [];
  if (
    record &&
    (record.stale ||
      record.error ||
      ["failed", "interrupted"].includes(record.state))
  )
    recovery.push({ label: "翻訳をやり直す", onClick: () => void open() });
  if (!lightweight && (overview.error || panel.error))
    recovery.push({
      label: "状態を再取得",
      onClick: () => {
        void overview.refetch();
        if (panel.error) void panel.refetch();
      },
    });
  const downloads = data?.units.flatMap((u) =>
    (["en", "ja"] as const)
      .filter((lang) => u.languages[lang].available)
      .map((lang) => (
        <ActionLink
          key={`${u.id}:${lang}`}
          className="translation-download"
          href={withBase(
            `/api/jobs/${job.id}/translations/${lang}/${u.id}?download=true`,
          )}
        >
          {lang === "en" ? "英訳" : "日本語訳"} {unitLabel(u.kind, u.number)}{" "}
          JSON
        </ActionLink>
      )),
  );
  const controls = (
    <>
      <Button
        id="translateOpen"
        title={
          lightweight
            ? "この軽量プレビューでは翻訳を利用できません。"
            : "この資料を翻訳"
        }
        disabled={!isDone(job) || lightweight}
        onClick={() => void open()}
      >
        翻訳
      </Button>
      <SelectField
        id="translationLanguage"
        aria-label="文書の言語"
        disabled={lightweight}
        value={lightweight ? "original" : language}
        onChange={(e) => onLanguage(e.target.value as Language)}
      >
        <option value="original">原文</option>
        <option value="en">英訳</option>
        <option value="ja">日本語訳</option>
      </SelectField>
    </>
  );
  const content = (
    <>
      <StatusMessage
        id="translationStatus"
        kind={mostUrgent(notices.map((n) => n.kind))}
        hidden={!notices.length}
        recovery={recovery}
      >
        {notices.map((n) => n.text).join(" ")}
      </StatusMessage>
      <SidePanel
        id="translationPanel"
        aria-label="このページの訳文"
        hidden={!showPanel}
        heading="translationPanelTitle"
        title={
          unit
            ? `${unitLabel(unit.kind, unit.number)} · ${language === "en" ? "英訳" : "日本語訳"}${legacy ? "（原文と対訳）" : ""}`
            : ""
        }
        headingClass="translation-panel-heading"
        closeId="translationPanelClose"
        onClose={() => onLanguage("original")}
      >
        <div id="translationPanelText">
          {panel.data?.pairs?.length
            ? panel.data.pairs.map((pair, i) => (
                <div key={i} className="translation-pair">
                  <p className="translation-source">{pair.source}</p>
                  <p>{pair.translation}</p>
                </div>
              ))
            : panel.data?.texts?.map((text, i) => <p key={i}>{text}</p>)}
        </div>
      </SidePanel>
    </>
  );
  const name = unit ? unitNames[unit.kind] : "ページ";
  const dialog = (
    <Dialog
      id="translationDialog"
      aria-labelledby="translationTitle"
      open={opened && !lightweight}
      onClose={() => {
        if (!busy) setOpened(false);
      }}
      busy={busy}
    >
      <form
        id="translationForm"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <DialogHeading
          id="translationTitle"
          title="資料を翻訳"
          onClose={() => setOpened(false)}
          disabled={busy}
        />
        <label>
          翻訳先
          <SelectField
            id="translationTarget"
            value={target}
            onChange={(e) => setTarget(e.target.value as "en" | "ja")}
          >
            <option value="en">英語</option>
            <option value="ja">日本語</option>
          </SelectField>
        </label>
        <label>
          翻訳する範囲
          <SelectField
            id="translationScope"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="current">
              {unit?.kind === "document"
                ? "文書全体"
                : `現在の${name} ${current}`}
            </option>
            <option value="selected">選択した範囲</option>
            <option value="all">資料全体</option>
          </SelectField>
        </label>
        <fieldset id="translationUnits" hidden={scope !== "selected"}>
          <legend>翻訳する{name}</legend>
          <div id="translationUnitList">
            {data?.units.map((u) => (
              <label key={u.id}>
                <CheckboxField
                  value={u.id}
                  checked={selected.includes(u.id)}
                  onChange={(e) => {
                    const checked = e.currentTarget.checked;
                    setSelected((v) =>
                      checked ? [...v, u.id] : v.filter((id) => id !== u.id),
                    );
                  }}
                />
                {unitLabel(u.kind, u.number)}
              </label>
            ))}
          </div>
        </fieldset>
        <label>
          次の翻訳までの間隔（秒）
          <TextField
            id="translationInterval"
            type="number"
            min={0}
            max={3600}
            step={1}
            required
            value={interval}
            onChange={(e) => setInterval(Number(e.target.value))}
          />
        </label>
        <p id="translationQueueNote">
          {unit?.kind === "document"
            ? "文書全体を翻訳します。長い文書を分割する場合は、この間隔を空けます。"
            : `1${name}ずつ順番に翻訳します。長い${name}を分割する場合も、この間隔を空けます。`}
        </p>
        <label className="translation-check">
          <CheckboxField
            id="translationForce"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
          />
          保存済みの単位も再翻訳する
        </label>
        <p id="translationDisclosure">
          対象の本文・表を
          {data?.profile.provider === "azure_openai"
            ? "Azure OpenAI"
            : "OpenAI"}
          へ送信します。
        </p>
        <p>
          文字列だけを翻訳し、図形や画像はそのまま、原文の文字の位置に訳文を重ねて表示します。長い訳文は文字を小さくして収めますが、はみ出す場合があります。画像内の文字はそのままです。
        </p>
        <StatusMessage
          id="translationDialogMessage"
          hidden={!error && !data?.configuration_error}
        >
          {error || data?.configuration_error}
        </StatusMessage>
        <Button
          id="translationSubmit"
          type="submit"
          className="primary"
          disabled={busy || !!data?.configuration_error}
        >
          翻訳を開始
        </Button>
      </form>
    </Dialog>
  );
  return { controls, content, dialog, downloads, revision };
}
