import "./app-tokens.css";
import "./ui.css";
import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icons";
export function Button({
  type = "button",
  ...props
}: ComponentProps<"button">) {
  return <button type={type} {...props} />;
}
export function IconButton({
  icon,
  label,
  className = "",
  ...props
}: ComponentProps<"button"> & { icon?: string; label: string }) {
  return (
    <Button
      className={`icon-button ${className}`}
      aria-label={label}
      title={label}
      {...props}
    >
      {icon ? <Icon name={icon} /> : props.children}
    </Button>
  );
}
export function ActionLink(props: ComponentProps<"a">) {
  return <a {...props} />;
}
export function TextField(props: ComponentProps<"input">) {
  return <input {...props} />;
}
export function SelectField(props: ComponentProps<"select">) {
  return <select {...props} />;
}
export function CheckboxField({
  indeterminate = false,
  ...props
}: ComponentProps<"input"> & { indeterminate?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return <input type="checkbox" {...props} ref={ref} />;
}
export function Toolbar({
  as = "div",
  ...props
}: ComponentProps<"div"> & { as?: "div" | "header" }) {
  return as === "header" ? <header {...props} /> : <div {...props} />;
}
/**
 * What a message means to the person reading it. Features map their own data
 * (server states, query errors) to a kind; this component knows no API.
 * Only `error` interrupts (role="alert"); every other kind is a polite status,
 * so a state that is merely in progress or incomplete is never announced as a
 * failure.
 */
export type StatusKind =
  | "loading" // fetching what is shown
  | "empty" // nothing to show yet
  | "setup" // a prerequisite is not configured
  | "pending" // accepted or queued, not started
  | "running" // in progress
  | "partial" // done, with parts missing or skipped
  | "error" // failed
  | "stale" // an older version is shown or saved
  | "unsaved"; // input exists only on this screen
const urgency: StatusKind[] = [
  "loading",
  "empty",
  "running",
  "pending",
  "partial",
  "unsaved",
  "setup",
  "stale",
  "error",
];
/** The kind that should speak for several messages shown as one. */
export const mostUrgent = (kinds: StatusKind[]): StatusKind | undefined =>
  kinds.reduce<StatusKind | undefined>(
    (top, kind) =>
      !top || urgency.indexOf(kind) > urgency.indexOf(top) ? kind : top,
    undefined,
  );
export interface Recovery {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}
/**
 * Without `kind` this is the plain status paragraph. `detail` is secondary
 * text read together with the message; `recovery` lists the actions that can
 * be taken now (they are not part of the live region).
 */
export function StatusMessage({
  kind,
  detail,
  recovery,
  children,
  ...props
}: ComponentProps<"p"> & {
  kind?: StatusKind;
  detail?: ReactNode;
  recovery?: Recovery[];
}) {
  const message = (
    <p role={kind === "error" ? "alert" : "status"} data-kind={kind} {...props}>
      {children}
      {detail ? <small className="status-detail"> {detail}</small> : null}
    </p>
  );
  if (!recovery?.length) return message;
  return (
    <>
      {message}
      <span className="status-recovery" hidden={props.hidden}>
        {recovery.map((action) => (
          <Button
            key={action.label}
            disabled={action.disabled}
            onClick={action.onClick}
          >
            {action.label}
          </Button>
        ))}
      </span>
    </>
  );
}
export function ContentCard({
  title,
  children,
  ...props
}: ComponentProps<"article"> & { title: ReactNode }) {
  return (
    <article className="card" {...props}>
      <strong>{title}</strong>
      {children}
    </article>
  );
}
export function MetadataDisclosure({
  title,
  children,
  ...props
}: ComponentProps<"details"> & { title: ReactNode }) {
  return (
    <details {...props}>
      <summary>{title}</summary>
      {children}
    </details>
  );
}
export function Dialog({
  open,
  onClose,
  busy = false,
  children,
  ...props
}: Omit<ComponentProps<"dialog">, "open" | "onClose"> & {
  open: boolean;
  onClose: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    callback = useRef(onClose);
  callback.current = onClose;
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    else if (!open && node.open) node.close();
  }, [open]);
  useEffect(
    () => () => {
      ref.current?.close();
    },
    [],
  );
  return (
    <dialog
      {...props}
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) callback.current();
      }}
      onClose={() => callback.current()}
    >
      {children}
    </dialog>
  );
}
export function DialogHeading({
  id,
  title,
  onClose,
  disabled = false,
}: {
  id: string;
  title: string;
  onClose: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="dialog-heading">
      <h2 id={id}>{title}</h2>
      <IconButton label="閉じる" onClick={onClose} disabled={disabled}>
        ×
      </IconButton>
    </div>
  );
}
export function DialogActions(props: ComponentProps<"div">) {
  return <div className="dialog-actions" {...props} />;
}
export function DisclosureMenu({
  label,
  icon,
  children,
  className = "toolbar-menu",
  ...props
}: ComponentProps<"details"> & { label: string; icon: string }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: Event) => {
      if (
        (e instanceof KeyboardEvent && e.key === "Escape") ||
        (e.type === "click" &&
          e.target instanceof Node &&
          !ref.current?.contains(e.target))
      ) {
        if (ref.current) ref.current.open = false;
      }
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", close);
    };
  }, []);
  return (
    <details
      {...props}
      ref={ref}
      className={className}
      onToggle={() => {
        if (ref.current?.open)
          document
            .querySelectorAll<HTMLDetailsElement>(
              "details.toolbar-menu,details.item-menu",
            )
            .forEach((n) => {
              if (n !== ref.current) n.open = false;
            });
      }}
      onClick={(e) => {
        e.stopPropagation();
        if (
          e.target instanceof Element &&
          e.target.closest("button,a") &&
          ref.current
        )
          ref.current.open = false;
      }}
    >
      <summary className="icon-button" aria-label={label} title={label}>
        <Icon name={icon} />
      </summary>
      <div
        className={className === "item-menu" ? "item-menu-panel" : "menu-panel"}
      >
        {children}
      </div>
    </details>
  );
}
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item, i) => (
        <Button
          key={item.id}
          id={`tab-${item.id}`}
          data-view={item.id}
          className={item.id === value ? "active" : ""}
          role="tab"
          aria-controls={item.id}
          aria-selected={item.id === value}
          tabIndex={item.id === value ? 0 : -1}
          onClick={() => onChange(item.id)}
          onKeyDown={(e) => {
            const next =
              e.key === "ArrowRight"
                ? (i + 1) % items.length
                : e.key === "ArrowLeft"
                  ? (i + items.length - 1) % items.length
                  : e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? items.length - 1
                      : -1;
            if (next >= 0) {
              e.preventDefault();
              onChange(items[next].id);
              document.getElementById(`tab-${items[next].id}`)?.focus();
            }
          }}
        >
          {item.label}
        </Button>
      ))}
    </div>
  );
}
export function Breadcrumbs({
  items,
  onSelect,
  dropProps,
  ...props
}: Omit<ComponentProps<"nav">, "onSelect"> & {
  items: { id: string | null; name: string }[];
  onSelect: (id: string | null) => void;
  dropProps?: (id: string | null) => ComponentProps<"button">;
}) {
  return (
    <nav className="breadcrumbs" {...props}>
      {items.map((item, i) => (
        <Fragment key={item.id || "root"}>
          {i > 0 && <span aria-hidden="true">›</span>}
          <Button
            title={item.name}
            aria-current={i === items.length - 1 ? "page" : undefined}
            onClick={() => onSelect(item.id)}
            {...dropProps?.(item.id)}
          >
            {item.name}
          </Button>
        </Fragment>
      ))}
    </nav>
  );
}
export function SidePanel({
  heading,
  title,
  closeId,
  onClose,
  headingClass,
  ...props
}: ComponentProps<"aside"> & {
  heading: string;
  title: string;
  closeId: string;
  onClose: () => void;
  headingClass: string;
}) {
  return (
    <aside {...props}>
      <div className={headingClass}>
        <strong id={heading}>{title}</strong>
        <Button
          id={closeId}
          aria-label={title.includes("訳") ? "訳文を閉じる" : "解説を閉じる"}
          onClick={onClose}
        >
          ×
        </Button>
      </div>
      {props.children}
    </aside>
  );
}
export function DocumentFrame(props: ComponentProps<"iframe">) {
  return <iframe {...props} />;
}
export interface PopupPosition {
  anchor: HTMLElement;
  x?: number;
  y?: number;
}
export function Popup({
  position,
  onClose,
  menu = false,
  children,
}: {
  position: PopupPosition | null;
  onClose: () => void;
  menu?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null),
    [xy, setXY] = useState({ x: 8, y: 8 });
  useEffect(() => {
    if (!position) return;
    const rect = position.anchor.getBoundingClientRect();
    setXY({
      x: Math.max(8, Math.min(position.x ?? rect.left, innerWidth - 292)),
      y: Math.max(
        8,
        Math.min(position.y ?? rect.bottom + 6, innerHeight - 180),
      ),
    });
  }, [position]);
  useEffect(() => {
    if (!position) return;
    const close = (e: Event) => {
      if (
        e.type === "resize" ||
        (e.type === "pointerdown" &&
          e.target instanceof Node &&
          !ref.current?.contains(e.target) &&
          !position.anchor.contains(e.target))
      )
        onClose();
    };
    document.addEventListener("pointerdown", close);
    window.addEventListener("resize", close);
    ref.current
      ?.querySelector<HTMLElement>("button,input,select,textarea")
      ?.focus();
    return () => {
      document.removeEventListener("pointerdown", close);
      window.removeEventListener("resize", close);
    };
  }, [position, onClose]);
  useEffect(() => {
    if (!position || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    if (r.bottom > innerHeight - 8)
      setXY((v) => ({ ...v, y: Math.max(8, innerHeight - r.height - 8) }));
  }, [position, children]);
  if (!position) return null;
  const target = position.anchor.closest("dialog[open]") || document.body;
  return createPortal(
    <div
      ref={ref}
      className="table-popup"
      role={menu ? "menu" : "dialog"}
      aria-label={menu ? "表の操作メニュー" : "表の設定"}
      style={{ left: xy.x, top: xy.y }}
      onKeyDown={(e) => {
        const items = Array.from(
            ref.current?.querySelectorAll<HTMLElement>(
              "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea",
            ) || [],
          ),
          i = items.indexOf(document.activeElement as HTMLElement);
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onClose();
          position.anchor.focus();
        } else if (
          menu &&
          ["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)
        ) {
          e.preventDefault();
          items[
            e.key === "Home"
              ? 0
              : e.key === "End"
                ? items.length - 1
                : (i + (e.key === "ArrowDown" ? 1 : -1) + items.length) %
                  items.length
          ]?.focus();
        } else if (
          e.key === "Tab" &&
          ((e.shiftKey && i === 0) || (!e.shiftKey && i === items.length - 1))
        ) {
          e.preventDefault();
          items[e.shiftKey ? items.length - 1 : 0]?.focus();
        }
      }}
    >
      {children}
    </div>,
    target,
  );
}
