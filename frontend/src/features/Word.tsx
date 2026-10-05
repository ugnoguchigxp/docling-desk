import { useCallback, useEffect, useState, type RefObject } from "react";
import { Button, IconButton, SelectField } from "../components/ui";
import { Icon } from "../components/Icons";
import { object } from "../lib/api";

export function useWordPreview(
  jobId: string,
  enabled: boolean,
  visible: boolean,
  frame: RefObject<HTMLIFrameElement | null>,
  label = "Word",
) {
  const [width, setWidth] = useState(0),
    [fit, setFit] = useState(true),
    [zoom, setZoom] = useState(1),
    [ratio, setRatio] = useState(1);
  const notify = useCallback(() => {
    if (enabled) {
      frame.current?.contentWindow?.postMessage(
        { type: "docling-document-request", jobId },
        "*",
      );
      frame.current?.contentWindow?.postMessage(
        { type: "docling-word-zoom", jobId, ratio },
        "*",
      );
    }
  }, [enabled, jobId, frame, ratio]);
  useEffect(() => {
    if (!enabled) return;
    const receive = (e: MessageEvent<unknown>) => {
      const d = e.data;
      if (
        e.source === frame.current?.contentWindow &&
        object(d) &&
        d.jobId === jobId &&
        d.type === "docling-document-size" &&
        typeof d.width === "number" &&
        Number.isFinite(d.width) &&
        d.width > 0
      )
        setWidth(d.width);
    };
    window.addEventListener("message", receive);
    notify();
    return () => window.removeEventListener("message", receive);
  }, [enabled, visible, frame, jobId, notify]);
  useEffect(() => {
    if (!enabled || !visible || !width || !frame.current) return;
    const resize = () => {
      const next = fit
        ? Math.max(0.1, Math.min(4, (frame.current!.clientWidth - 24) / width))
        : zoom;
      setRatio(next);
      frame.current?.contentWindow?.postMessage(
        { type: "docling-word-zoom", jobId, ratio: next },
        "*",
      );
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, [enabled, visible, width, fit, zoom, frame, jobId]);
  const manual = (value: number) => {
    setFit(false);
    setZoom(Math.max(0.1, Math.min(4, value)));
  };
  return {
    notify,
    controls: enabled && visible && (
      <div
        className="slide-controls word-controls"
        role="group"
        aria-label={`${label}の表示操作`}
      >
        <IconButton
          icon="minus"
          label={`${label}を縮小`}
          disabled={!width || ratio <= 0.1}
          onClick={() => manual(ratio / 1.25)}
        />
        <SelectField
          aria-label={`${label}のズーム倍率`}
          value="current"
          disabled={!width}
          onChange={(e) => {
            if (e.target.value !== "current") manual(Number(e.target.value));
          }}
        >
          <option value="current">{Math.round(ratio * 100)}%</option>
          {[0.25, 0.5, 0.75, 1, 1.5, 2, 4].map((value) => (
            <option key={value} value={value}>
              {value * 100}%
            </option>
          ))}
        </SelectField>
        <IconButton
          icon="plus"
          label={`${label}を拡大`}
          disabled={!width || ratio >= 4}
          onClick={() => manual(ratio * 1.25)}
        />
        <Button disabled={!width} onClick={() => manual(1)}>
          100%
        </Button>
        <Button
          className="word-fit"
          aria-label="幅に合わせる"
          title="幅に合わせる"
          disabled={!width}
          aria-pressed={fit}
          onClick={() => setFit(true)}
        >
          <Icon name="fit" />
          <span>Fit</span>
        </Button>
      </div>
    ),
  };
}
