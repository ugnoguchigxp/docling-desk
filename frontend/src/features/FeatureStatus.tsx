import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Dialog,
  DialogActions,
  DialogHeading,
  StatusMessage,
  mostUrgent,
} from "../components/ui";
import {
  featureKind,
  loadDiagnostics,
  needsRecovery,
  optionalGaps,
  type Feature,
} from "../lib/diagnostics";
import { queryKeys } from "../lib/query-keys";
import "./feature-status.css";

/**
 * What each feature needs and whether this machine has it (R18).
 *
 * The server diagnoses from local state, so asking costs nothing and reaches no
 * provider. It is asked when the dialog opens and when the person presses
 * 「もう一度確認」 after fixing something; there is no timer and no refetch on
 * focus. How a feature is shown follows its own status only: an optional
 * prerequisite that is missing is a note under a feature that works.
 */
export function FeatureStatus() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button id="featureStatusOpen" onClick={() => setOpen(true)}>
        機能の利用状態
      </Button>
      <Dialog
        id="featureStatus"
        className="library-dialog feature-status"
        aria-labelledby="featureStatusTitle"
        open={open}
        onClose={() => setOpen(false)}
      >
        {open && <FeatureStatusBody onClose={() => setOpen(false)} />}
      </Dialog>
    </>
  );
}

function FeatureStatusBody({ onClose }: { onClose: () => void }) {
  const diagnostics = useQuery({
    queryKey: queryKeys.featureDiagnostics,
    queryFn: ({ signal }) => loadDiagnostics(signal),
    // Asked on open (the dialog mounts this body) and on request; never on a
    // timer, on focus or on reconnect.
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const features = diagnostics.data?.features ?? [];
  const urgent = features.filter(needsRecovery);
  const guidance = features.filter(
    (f) => f.status !== "available" && !needsRecovery(f),
  );
  return (
    <>
      <DialogHeading
        id="featureStatusTitle"
        title="機能の利用状態"
        onClose={onClose}
      />
      {diagnostics.isPending ? (
        <StatusMessage kind="loading">確認しています…</StatusMessage>
      ) : diagnostics.error ? (
        <StatusMessage
          kind="error"
          recovery={[
            {
              label: "もう一度確認",
              onClick: () => void diagnostics.refetch(),
            },
          ]}
        >
          {diagnostics.error.message}
        </StatusMessage>
      ) : (
        <StatusMessage
          kind={mostUrgent(urgent.map((f) => featureKind(f)!)) ?? undefined}
          detail={
            guidance.length
              ? `任意の機能のうち${guidance.length}件は未設定です。使わなければ対応は不要です。`
              : undefined
          }
        >
          {urgent.length
            ? `対応が必要な機能が${urgent.length}件あります。`
            : "必要な機能はすべて利用できます。"}
        </StatusMessage>
      )}
      <ul className="feature-status-list">
        {features.map((feature) => (
          <FeatureRow key={feature.id} feature={feature} />
        ))}
      </ul>
      <DialogActions>
        <Button
          disabled={diagnostics.isFetching}
          onClick={() => void diagnostics.refetch()}
        >
          {diagnostics.isFetching && !diagnostics.isPending
            ? "確認しています…"
            : "もう一度確認"}
        </Button>
        <Button className="primary" onClick={onClose}>
          閉じる
        </Button>
      </DialogActions>
    </>
  );
}

function summary(feature: Feature) {
  if (feature.status === "available") return "利用できます";
  if (feature.status === "not_configured")
    return feature.optional
      ? "未設定です（任意）"
      : "必要な設定が不足しています";
  if (feature.status === "temporary_failure") return "一時的に利用できません";
  return "状態を確認できません";
}

function FeatureRow({ feature }: { feature: Feature }) {
  const gaps = optionalGaps(feature);
  return (
    <li className="feature-status-item" data-feature={feature.id}>
      <h3>
        {feature.label}
        {feature.optional && <small> 任意</small>}
      </h3>
      <StatusMessage
        kind={featureKind(feature)}
        data-need={needsRecovery(feature) ? "action" : "guidance"}
      >
        {summary(feature)}
      </StatusMessage>
      {feature.description && <p>{feature.description}</p>}
      {feature.status !== "available" && feature.recovery.length > 0 && (
        <ul className="feature-status-steps">
          {feature.recovery.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      )}
      {gaps.length > 0 && (
        <small className="feature-status-note">
          {gaps.map((p) => `${p.label}は未設定です（任意）`).join("。")}
          。この機能の利用には影響しません。
        </small>
      )}
    </li>
  );
}
