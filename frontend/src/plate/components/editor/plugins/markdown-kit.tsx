import {
  BaseFootnoteDefinitionPlugin,
  BaseFootnoteReferencePlugin,
} from "@platejs/footnote";
import {
  MarkdownPlugin,
  defaultRules,
  propsToAttributes,
  parseAttributes,
  remarkMdx,
  remarkMention,
} from "@platejs/markdown";
import { KEYS } from "platejs";
import remarkEmoji from "remark-emoji";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

export const MarkdownKit = [
  BaseFootnoteReferencePlugin,
  BaseFootnoteDefinitionPlugin,
  MarkdownPlugin.configure({
    options: {
      rules: {
        excalidraw: {
          serialize: (node) => ({
            type: "mdxJsxFlowElement",
            name: "excalidraw",
            attributes: propsToAttributes({ data: node.data }),
            children: [],
          }),
          deserialize: (node) => ({
            type: "excalidraw",
            data: parseAttributes(node.attributes).data,
            children: [{ text: "" }],
          }),
        },
        img: {
          deserialize: (node, deco, options) => ({
            ...defaultRules.img!.deserialize!(node, deco, options),
            alt: node.alt ?? "",
            caption: [{ text: node.title ?? "" }],
          }),
          serialize: (node) => ({
            type: "image",
            url: node.url,
            alt: typeof node.alt === "string" ? node.alt : "",
            title: Array.isArray(node.caption)
              ? node.caption.map((c) => c.text).join("")
              : undefined,
          }),
        },
      },
      plainMarks: [KEYS.suggestion, KEYS.comment],
      remarkPlugins: [
        remarkMath,
        remarkGfm,
        remarkEmoji as any,
        remarkMdx,
        remarkMention,
      ],
    },
  }),
];
