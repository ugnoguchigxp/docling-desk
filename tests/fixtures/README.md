# Test document provenance

These fixtures contain fictional test material, not uploaded business documents.

- `documents/page`, `documents/slide`, and `documents/sheet` contain the PDF, PowerPoint, and Excel examples produced by `docling_desk.development.samples`: categories A/B/C, counts 120/180/150, and total 450. Their extracted text and previews describe this fictional data.
- `documents/glyph` contains a synthetic SVG deck used for text selection. `qa/frontend-migration/synthetic_office.py` generates its downloadable Office counterpart and the synthetic workbook.
- `wiki_batch/legacy-snapshot.json` is a snapshot of fictional Wiki content.
- The Word sample is produced by `docling_desk.development.word`.
- Saved explanations for the CI browser server are tracked under `qa/frontend-migration/fixtures/explanations/`. They refer only to the fictional PDF, slide and sheet fixtures; they are not runtime generation output from user documents.
- The Word print preview under `qa/print-preview/fixtures/word/` contains the same fictional A/B/C counts and chart. Its Quick Look HTML, PNG and metadata are tracked so print E2E tests run on a fresh checkout.
- With `UI_SYNTHETIC=1`, the browser server uses the tracked slide chart image for thumbnail responses. This UI-only lane checks image loading, caching and request concurrency without Office/WebKit renderers; native rendering has separate renderer tests. Saved-document browser runs retain the real renderer.

Quick Look previews include common viewer controls, styles and scripts. These are distinct from document content. Ordinary tests copy fixtures into temporary directories; they do not need the user's `data/`, local database, or cloud credentials.

Keep original business documents, extracted business text, screenshots of those documents, and runtime databases out of this directory. The application wheel and sdist omit these document fixtures and sample binaries.
