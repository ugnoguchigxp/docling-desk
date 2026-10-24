import type { DocumentReference } from "../reference";

const originOf = (url: string) => {
  try {
    const { origin } = new URL(url);
    return origin === "null" ? "" : origin;
  } catch {
    return "";
  }
};

/**
 * Origin that may receive host messages, or null when it cannot be determined
 * (then nothing is sent). The viewer is embedded only by the Open WebUI tool
 * wrapper, which the gateway serves from this app's own origin
 * (`frame-ancestors 'self'`). The nearest parent is addressed by its origin.
 * A sandboxed wrapper has an opaque origin that only "*" can address; that is
 * accepted solely when the ancestor chain is verifiably this app's origin
 * (ancestorOrigins, or the referrer where a browser has no ancestorOrigins and
 * cannot tell an opaque parent from a same-origin one).
 */
export function hostOrigin(): string | null {
  if (window.parent === window) return null;
  const own = originOf(location.href);
  const ancestors = Array.from(location.ancestorOrigins ?? []);
  if (ancestors.length) {
    const nearest = ancestors[0];
    if (nearest !== "null") return originOf(nearest) || null;
    return own && ancestors[ancestors.length - 1] === own ? "*" : null;
  }
  const referrer = originOf(document.referrer);
  if (!referrer) return null;
  return referrer === own ? "*" : referrer;
}
function post(message: unknown) {
  const origin = hostOrigin();
  if (origin) window.parent.postMessage(message, origin);
}

/** Uses documented Open WebUI messages; never reaches into host DOM/storage. */
export function askHost(text: string, reference: DocumentReference) {
  const prompt = `この資料の箇所について説明してください。\n資料参照: ${JSON.stringify(reference)}${text ? `\n選択した本文:\n${text.slice(0, 10000)}` : ""}`;
  post({ type: "input:prompt", text: prompt });
  return prompt;
}
export function connectHost(code: string) {
  post({
    type: "input:prompt",
    text: `Docling Deskの閲覧画面に表示された接続コード ${code} を connect_document_view ツールで承認してください。`,
  });
}
export function reportHeight() {
  post({
    type: "iframe:height",
    height: Math.max(600, Math.min(1000, window.innerHeight || 760)),
  });
}
