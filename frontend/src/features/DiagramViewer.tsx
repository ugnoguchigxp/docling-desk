import { useEffect } from "react";
import type { ReactNode } from "react";
import {
  ActionLink,
  DocumentFrame,
  IconButton,
  Toolbar,
} from "../components/ui";
import { Icon } from "../components/Icons";
import { originalUrl } from "../lib/api";
import { withBase } from "../lib/base";
import type { Job } from "../lib/types";
import "./Viewer.css";

export function DiagramViewer({
  job,
  onBack,
  backLabel = "資料一覧へ戻る",
  message,
  navigation,
  initialUnit,
}: {
  job: Job;
  onBack: () => void;
  backLabel?: string;
  message: string;
  navigation?: ReactNode;
  initialUnit?: number | null;
}) {
  useEffect(() => {
    document.title = `${job.filename} · Docling Desk`;
  }, [job.filename]);
  const unit = Math.max(1, Math.min(initialUnit || 1, job.pages || 1));
  const preview = withBase(`/view/${job.id}/diagram?unit=${unit}`);
  return (
    <section id="detail" aria-label="図のビューアー">
      <Toolbar as="header" id="viewerToolbar" className="viewer-toolbar">
        {navigation}
        <IconButton
          id="backToFiles"
          icon="back"
          label={backLabel}
          onClick={onBack}
        />
        <span id="filename" title={job.filename}>
          {job.filename}
        </span>
        <span className="diagram-view-label">図のプレビュー</span>
        <div className="viewer-actions">
          <ActionLink
            href={preview}
            className="icon-button"
            target="_blank"
            rel="noopener"
            aria-label="プレビューを大きく開く"
            title="プレビューを大きく開く"
          >
            <Icon name="open" />
          </ActionLink>
          <ActionLink id="originalDownload" href={withBase(originalUrl(job))}>
            原本を保存
          </ActionLink>
        </div>
      </Toolbar>
      {message && (
        <p className="viewer-message" role="status">
          {message}
        </p>
      )}
      <div id="documentPanels">
        <div id="preview" className="diagram-preview-panel">
          <DocumentFrame
            id="original"
            title="図のプレビュー"
            sandbox="allow-scripts"
            src={preview}
          />
        </div>
      </div>
    </section>
  );
}
