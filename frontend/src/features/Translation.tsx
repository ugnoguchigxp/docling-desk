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
} from "../components/ui";
import { errorText, post, request } from "../lib/api";
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
  const lightweight = !!job.preview && isLightweightPreview(job);
  const overview = useQuery({
    queryKey: ["translations", job.id],
    queryFn: ({ signal }) =>
      request<TranslationOverview>(
        `/api/jobs/${job.id}/translations`,
        { signal },
        contracts.translations,
      ),
    enabled: isDone(job) && !lightweight,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (q) =>
      !q.state.error &&
      q.state.data?.units.some((u) =>
        Object.values(u.languages).some((r) => active.includes(r.state)),
      )
        ? 1500
        : false,
  });
  const data = lightweight ? undefined : overview.data,
    unit = data?.units.find((u) => u.number === current) || data?.units[0],
    record = language === "original" ? null : unit?.languages[language],
    revision = record?.result_created_at || "";
  const panel = useQuery({
    queryKey: ["translation-result", job.id, unit?.id, language, revision],
    queryFn: ({ signal }) =>
      request<{ available: boolean; texts: string[] }>(
        `/api/jobs/${job.id}/translations/${language}/${unit!.id}`,
        { signal },
        contracts.translationPanel,
      ),
    enabled: !lightweight && !!record?.available && unit?.mode === "panel",
  });
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
    try {
      const result = await overview.refetch();
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
      await cache.invalidateQueries({ queryKey: ["translations", job.id] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  const warnings: string[] = [];
  if (unit?.preview_unavailable_reason)
    warnings.push(unit.preview_unavailable_reason);
  if (unit?.unavailable_reason) warnings.push(unit.unavailable_reason);
  if (data) {
    const count = data.units.filter((u) =>
      Object.values(u.languages).some((r) => active.includes(r.state)),
    ).length;
    if (count) warnings.push(`翻訳処理中 · 残り ${count}単位`);
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
      warnings.push(
        `${waiting.wait_reason === "rate_limit" ? "レート制限のため" : waiting.wait_reason === "retry" ? "再試行まで" : "次の翻訳まで"}約${seconds}秒待機します。`,
      );
    }
    if (record && unit) {
      if (record.stale)
        warnings.push("原文が更新されています。再翻訳してください。");
      else if (!record.available)
        warnings.push(
          `${unitLabel(unit.kind, unit.number)} は${states[record.state] || record.state}です。原文を表示しています。`,
        );
      if (record.error) warnings.push(record.error);
      if (unit.kind === "slide") warnings.push("サムネイルは原文です。");
      if (unit.excluded_count)
        warnings.push(
          `対応を確定できない文字セル ${unit.excluded_count}件は原文のままです。`,
        );
      if (record.available && !unit.segments_count)
        warnings.push("翻訳対象の文字がありません。");
    }
    if (data.unlocated_count)
      warnings.push(
        `ページ位置のない要素 ${data.unlocated_count}件は翻訳対象外です。`,
      );
  }
  if (!lightweight && overview.error) warnings.push(overview.error.message);
  if (!lightweight && panel.error) warnings.push(panel.error.message);
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
      <div id="translationStatus" role="status" hidden={!warnings.length}>
        {warnings.join(" ")}
      </div>
      <SidePanel
        id="translationPanel"
        aria-label="このページの訳文"
        hidden={
          !record?.available || unit?.mode !== "panel" || !panel.data?.available
        }
        heading="translationPanelTitle"
        title={
          unit
            ? `${unitLabel(unit.kind, unit.number)} · ${language === "en" ? "英訳" : "日本語訳"}`
            : ""
        }
        headingClass="translation-panel-heading"
        closeId="translationPanelClose"
        onClose={() => onLanguage("original")}
      >
        <div id="translationPanelText">
          {panel.data?.texts?.map((text, i) => (
            <p key={i}>{text}</p>
          ))}
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
          文字列だけを翻訳し、文字枠・図形・文字サイズを保ちます。長い訳文ははみ出す場合があります。画像内の文字はそのままです。
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
