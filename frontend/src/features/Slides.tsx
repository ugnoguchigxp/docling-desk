import { useViewerSource } from "../viewer/data-source";
import * as contracts from "../lib/contracts";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  DocumentFrame,
  IconButton,
  SelectField,
  StatusMessage,
} from "../components/ui";
import { Icon } from "../components/Icons";
import { fileUrl, object, request } from "../lib/api";
import {
  isLightweightPreview,
  type Job,
  type Language,
  type Slides as SlideData,
} from "../lib/types";
export function useSlides(
  job: Job,
  visible: boolean,
  number: number,
  onNumber: (n: number) => void,
  language: Language,
  revision: string,
) {
  const source = useViewerSource();
  const lightweight = isLightweightPreview(job);
  const enabled = job.slide_layout || lightweight;
  const data = useQuery({
    queryKey: ["slides", job.id, job.preview],
    queryFn: ({ signal }) =>
      request<SlideData>(
        source.url(fileUrl(job.id, "slides.json")),
        { signal },
        contracts.slides,
      ),
    enabled,
  });
  const [fit, setFit] = useState(true),
    [zoom, setZoom] = useState(1),
    [ratio, setRatio] = useState(1),
    [viewport, setViewport] = useState({ width: 0, height: 0 }),
    [selectionRevision, setSelectionRevision] = useState(0),
    [thumbnails, setThumbnails] = useState(true);
  const stage = useRef<HTMLDivElement>(null),
    rail = useRef<HTMLElement>(null),
    frame = useRef<HTMLIFrameElement>(null);
  const page = data.data?.slides.find((s) => s.number === number),
    active = !!page?.preview && enabled,
    hasMetadata = !!data.data,
    index = data.data?.slides.findIndex((s) => s.number === number) ?? 0;
  const scale = useCallback(() => {
    if (!page || !visible || !stage.current) return;
    const s = stage.current;
    if (s.clientWidth <= 0 || s.clientHeight <= 0) return;
    const width = s.clientWidth,
      height = s.clientHeight;
    setViewport((previous) =>
      previous.width === width && previous.height === height
        ? previous
        : { width, height },
    );
    const next = fit
      ? Math.min(s.clientWidth / page.width, s.clientHeight / page.height)
      : zoom;
    setRatio(next);
    if (fit) {
      s.scrollLeft = 0;
      s.scrollTop = 0;
    }
  }, [page, visible, fit, zoom]);
  useLayoutEffect(() => {
    scale();
    const observer = new ResizeObserver(scale);
    if (stage.current) observer.observe(stage.current);
    window.addEventListener("resize", scale);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", scale);
    };
  }, [scale, thumbnails]);
  useLayoutEffect(() => {
    if (stage.current) {
      stage.current.scrollLeft = 0;
      stage.current.scrollTop = 0;
    }
  }, [number, visible, thumbnails]);
  useLayoutEffect(() => {
    if (visible && thumbnails)
      rail.current
        ?.querySelector<HTMLElement>(`[data-page="${number}"]`)
        ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [number, visible, thumbnails, hasMetadata]);
  useEffect(() => {
    if (!data.data || !rail.current) return;
    const root = rail.current;
    let loading = 0,
      dead = false;
    const inFlight = new Set<HTMLImageElement>();
    const queue: HTMLImageElement[] = [],
      queued = new Set<HTMLImageElement>();
    const life = new AbortController();
    const load = () => {
      while (
        !dead &&
        root.clientWidth &&
        root.clientHeight &&
        loading < 2 &&
        queue.length
      ) {
        const img = queue.shift()!;
        queued.delete(img);
        if (!img.dataset.url || !img.isConnected) continue;
        loading++;
        inFlight.add(img);
        const complete = (success: boolean) => {
          if (dead) return;
          img.parentElement?.classList.remove("loading");
          if (!success) img.parentElement?.classList.add("failed");
          loading--;
          inFlight.delete(img);
          load();
        };
        img.addEventListener("load", () => complete(true), {
          once: true,
          signal: life.signal,
        });
        img.addEventListener("error", () => complete(false), {
          once: true,
          signal: life.signal,
        });
        img.src = img.dataset.url;
        delete img.dataset.url;
        observer.unobserve(img);
      }
    };
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!(e.target instanceof HTMLImageElement)) continue;
          if (!e.isIntersecting) {
            queued.delete(e.target);
            const index = queue.indexOf(e.target);
            if (index >= 0) queue.splice(index, 1);
          } else if (e.target.dataset.url && !queued.has(e.target)) {
            queued.add(e.target);
            queue.push(e.target);
          }
        }
        load();
      },
      { root: rail.current, rootMargin: "120px 0px" },
    );
    rail.current
      .querySelectorAll("img[data-url]")
      .forEach((img) => observer.observe(img));
    return () => {
      dead = true;
      life.abort();
      observer.disconnect();
      for (const img of inFlight) {
        // React StrictMode can stop and restart this effect before a thumbnail
        // finishes. Keep the URL available for the replacement observer,
        // preserving a new URL that React committed during a mode change.
        img.dataset.url ||= img.getAttribute("src") || "";
        img.removeAttribute("src");
      }
    };
  }, [data.data, lightweight]);
  const url = page?.preview
    ? source.url(
        lightweight
          ? fileUrl(job.id, page.preview) + "?inline_fonts=true"
          : `/view/${job.id}/slides/${number}?language=${language}`,
      )
    : "";
  const lastDelivery = useRef({ url: "", revision: "" });
  useEffect(() => {
    const previous = lastDelivery.current;
    lastDelivery.current = { url, revision };
    // React already navigates on a changed src. Only refresh in place when a
    // saved translation changes while the same slide/language remains open.
    if (
      previous.url !== url ||
      previous.revision === revision ||
      !frame.current ||
      language === "original" ||
      lightweight
    )
      return;
    const s = stage.current,
      top = s?.scrollTop || 0,
      left = s?.scrollLeft || 0;
    frame.current.src = url;
    const f = frame.current,
      restore = () => {
        if (s) {
          s.scrollTop = top;
          s.scrollLeft = left;
        }
      };
    f.addEventListener("load", restore, { once: true });
    return () => f.removeEventListener("load", restore);
  }, [revision, url, language, lightweight]);
  const setManual = (value: number) => {
    setZoom(Math.min(4, Math.max(0.1, value)));
    setFit(false);
  };
  // Recreate the source frame on an explicit slide selection, as the legacy
  // viewer does. Its quirks-mode fallback fonts depend on that frame lifecycle.
  const select = (next: number) => {
    setSelectionRevision((value) => value + 1);
    onNumber(next);
    if (stage.current) {
      stage.current.scrollLeft = 0;
      stage.current.scrollTop = 0;
    }
  };
  const move = (step: number) => {
    const next = data.data?.slides[index + step];
    if (next) select(next.number);
  };
  useEffect(() => {
    const receive = (e: MessageEvent<unknown>) => {
      const d = e.data;
      if (
        !visible ||
        !fit ||
        e.source !== frame.current?.contentWindow ||
        !object(d) ||
        d.jobId !== job.id ||
        d.type !== "docling-slide-key"
      )
        return;
      const step = d.key === "ArrowLeft" ? -1 : d.key === "ArrowRight" ? 1 : 0;
      const next = step ? data.data?.slides[index + step] : undefined;
      if (next) {
        setSelectionRevision((v) => v + 1);
        onNumber(next.number);
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [visible, fit, job.id, data.data, index, onNumber]);
  const controls = (
    <div
      id="slideControls"
      className="slide-controls"
      hidden={
        !visible || (!active && !(lightweight && data.data?.slides.length))
      }
    >
      <IconButton
        id="slideThumbnailsToggle"
        icon="thumbnails"
        label="サムネイルを表示・非表示"
        aria-pressed={thumbnails}
        onClick={() => setThumbnails((v) => !v)}
      />
      <IconButton
        id="slidePrevious"
        icon="back"
        label="前のスライド"
        disabled={index <= 0}
        onClick={() => move(-1)}
      />
      <SelectField
        id="slidePicker"
        aria-label="スライド"
        value={number}
        onChange={(e) => select(Number(e.target.value))}
      >
        {data.data?.slides.map((s) => (
          <option
            key={s.number}
            value={s.number}
            disabled={lightweight && !s.preview}
          >
            {s.number} / {data.data.slides.length}
          </option>
        ))}
      </SelectField>
      <IconButton
        id="slideNext"
        icon="next"
        label="次のスライド"
        disabled={index === (data.data?.slides.length || 0) - 1}
        onClick={() => move(1)}
      />
      <span className="toolbar-divider" />
      <IconButton
        id="slideZoomOut"
        icon="minus"
        label="縮小"
        disabled={ratio <= 0.1}
        onClick={() => setManual(ratio / 1.25)}
      />
      <SelectField
        id="slideZoomPreset"
        aria-label="ズーム倍率"
        value="current"
        onChange={(e) => {
          if (e.target.value !== "current") setManual(Number(e.target.value));
        }}
      >
        <option value="current">{Math.round(ratio * 100)}%</option>
        {[0.25, 0.5, 0.75, 1, 1.5, 2, 4].map((v) => (
          <option key={v} value={v}>
            {v * 100}%
          </option>
        ))}
      </SelectField>
      <IconButton
        id="slideZoomIn"
        icon="plus"
        label="拡大"
        disabled={ratio >= 4}
        onClick={() => setManual(ratio * 1.25)}
      />
      <Button
        id="slideFit"
        aria-label="画面に合わせる"
        title="画面に合わせる"
        aria-pressed={fit}
        onClick={() => setFit(true)}
      >
        <Icon name="fit" />
        <span>Fit</span>
      </Button>
    </div>
  );
  const content = (
    <>
      <StatusMessage
        id="previewStatus"
        hidden={
          !enabled ||
          (!data.isPending && !data.error && !(!active && !!data.data))
        }
      >
        {data.error?.message ||
          (data.isPending
            ? "スライドを読み込んでいます…"
            : lightweight
              ? "このページのプレビューはまだありません。作成済みのページを選んでください。"
              : "スライド別表示を取得できないため、文書全体のプレビューを表示します。")}
      </StatusMessage>
      <div id="slides" hidden={!active}>
        <nav
          id="slideThumbnails"
          ref={rail}
          aria-label="スライドのサムネイル"
          hidden={!thumbnails}
        >
          {data.data?.slides.map((s, i) => (
            <Button
              key={s.number}
              className="slide-thumbnail"
              data-page={s.number}
              aria-label={`スライド ${s.number}`}
              aria-current={s.number === number}
              tabIndex={s.number === number ? 0 : -1}
              onClick={() => select(s.number)}
              onKeyDown={(e) => {
                const next =
                  e.key === "ArrowDown"
                    ? Math.min(data.data.slides.length - 1, i + 1)
                    : e.key === "ArrowUp"
                      ? Math.max(0, i - 1)
                      : e.key === "Home"
                        ? 0
                        : e.key === "End"
                          ? data.data.slides.length - 1
                          : -1;
                if (next >= 0) {
                  e.preventDefault();
                  const n = data.data.slides[next].number;
                  select(n);
                  rail.current
                    ?.querySelector<HTMLElement>(`[data-page="${n}"]`)
                    ?.focus();
                }
              }}
            >
              <span
                className="thumbnail-image loading"
                style={{ aspectRatio: `${s.width}/${s.height}` }}
              >
                <img
                  alt={`スライド ${s.number}のプレビュー`}
                  decoding="async"
                  data-url={
                    lightweight
                      ? s.preview
                        ? source.url(
                            fileUrl(job.id, s.preview) + "?thumbnail=true",
                          )
                        : undefined
                      : source.url(
                          `/api/jobs/${job.id}/slides/${s.number}/thumbnail`,
                        )
                  }
                />
              </span>
              <span>{s.number}</span>
            </Button>
          ))}
        </nav>
        <div
          id="slideStage"
          ref={stage}
          tabIndex={0}
          role="region"
          aria-label="スライド表示領域"
          style={{ overflow: fit ? "hidden" : "auto" }}
          onKeyDown={(e) => {
            if (
              e.target === stage.current &&
              fit &&
              ["ArrowLeft", "ArrowRight"].includes(e.key)
            ) {
              e.preventDefault();
              move(e.key === "ArrowLeft" ? -1 : 1);
            }
          }}
        >
          <div
            id="slideSurface"
            style={{
              width: page ? page.width * ratio : undefined,
              height: page ? page.height * ratio : undefined,
            }}
          >
            <div
              id="slideCanvas"
              style={{
                width: page?.width,
                height: page?.height,
                transform: `scale(${ratio})`,
                left: Math.max(
                  0,
                  (viewport.width - (page?.width || 0) * ratio) / 2,
                ),
                top: Math.max(
                  0,
                  (viewport.height - (page?.height || 0) * ratio) / 2,
                ),
              }}
            >
              {active && (
                <DocumentFrame
                  key={`${number}:${selectionRevision}`}
                  ref={frame}
                  title={`原本プレビュー・スライド ${number}`}
                  sandbox={lightweight ? "" : "allow-scripts"}
                  src={url}
                  style={{ width: page.width, height: page.height }}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
  const fallback =
    job.slide_layout &&
    !data.isPending &&
    (!!data.error || (!!data.data && !active));
  return { controls, content, active, fallback, url, frame, selectionRevision };
}
