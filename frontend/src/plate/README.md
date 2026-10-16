# Plate standard editor

Source: Plate's MIT-licensed registry at https://platejs.org/r/editor-kit.json and its recursive registry dependencies, retrieved 2026-10-16. The complete fixed/floating toolbar, menus, node components, editing plugins and export components are kept together here. Shadcn UI primitives are in `ui-primitives/`, under their own MIT notice. Package versions are pinned in `frontend/package.json` and `pnpm-lock.yaml`.

Integration changes:

- Aliases point to this directory; the existing application UI is separate.
- The full toolbar wraps to fit the writing pane; the floating toolbar cannot cover its controls and hides while focus is in a toolbar input. Accessible labels mirror standard tooltips.
- Image drops, pastes and file selections use the existing `/api/upload` with the root library `upload` folder (created on first use), then insert the returned `image.reference` at the standard Plate placeholder. Concurrent files share the folder lookup; another tab's folder-creation conflict is resolved by reading the saved folder. Wiki's article-import drop handler leaves editor drops to Plate's HTML5 backend. The library list is refreshed after success. Non-image attachments retain `/api/wiki/media`. There is no UploadThing service or mock upload fallback.
- AI uses `/api/wiki/editor/ai`, backed by the application's existing isolated Codex provider. Failed requests remain errors; generated demo text and fake streams have been removed.
- Comments start empty, use a local author, and are saved with the document. Demo users, avatars and the fictional mention directory have been removed. Mentions allow the locally entered name.
- Excalidraw is registered for the standard Insert menu and its fonts are shipped locally. Its public JSON serializer stores shapes, image files and restorable app state, excluding transient Maps; a Markdown/MDX rule retains the drawing when switching source mode. Browser shims support the standard Word exporter. HTML exports carry the locally built stylesheet.
- Image alt text is separate from Plate's caption, encoded as Markdown alt/title, and survives reload. The standard media editor includes an explicit Done action.
- The Image split-button menu adds a library picker backed by the existing `/api/v1/media` search, thumbnails and metadata. It pages across all folders, rechecks the chosen registration, and inserts its immutable `reference` and current `default_alt` with the standard image transform, without another upload. `ImageLibraryDialog` owns only selection UI; the toolbar keeps a Plate range ref. It uses Plate's DOM-to-Slate conversion for a recently moved caret because Slate's selectionchange is deferred. Cancel restores that range synchronously; delayed dialog autofocus cannot steal focus from a reopened menu. Closing aborts metadata checks and ignores late results. Computer uploads and URL insertion retain their standard behavior.

The registry's open plugin `any` types are retained in vendored components. TypeScript and functional tests check integration; application code and API models keep the repository's strict lint rules. The only lint override applies to explicit `any` in this directory.

`../editor/WikiPlateEditor.tsx` connects this standard kit to Wiki persistence. It stores the native document and discussions alongside Markdown so colors, tables, columns, suggestions and other rich features survive reopening. Unknown original Markdown blocks stay inert and editable through source mode. External publication is not implemented.
