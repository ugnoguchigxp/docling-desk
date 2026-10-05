# Third-party notices

Docling Desk's own code is distributed under the MIT license in LICENSE.
Dependencies, model artifacts, fonts, and third-party components retain their own licenses.

- Docling is a pinned upstream Git submodule. Its license remains in `docling/LICENSE`.
- The Azure OCR adapter is also available as a separate package under `packages/docling-azure-ocr`.
- Wiki import/translation code was adapted from a non-public implementation by the same author (see `integrations/wiki/reference-assets.json`); retained notices are in `src/docling_desk/wiki_batch/NOTICE.md` and `licenses/` beside it.
- PyMuPDF (`pymupdf`) is a direct dependency used for PDF text and preview processing. It is dual-licensed under AGPL-3.0 or the Artifex commercial license; this project uses it under the AGPL-3.0 terms. Anyone distributing or hosting a build that includes it (wheel, Docker image, or network service) must meet the AGPL-3.0 obligations, including offering the corresponding source.
- Bundled AG Grid and TabuLens notices and checksums are retained under `src/docling_desk/resources/static/vendor`.
- Built frontend third-party notices are generated as `static/frontend/licenses.txt` within the installed resources.
- Model revisions and download sources are in `src/docling_desk/resources/models/manifest.json`. The application license does not grant rights to model weights or user documents.

Runtime documents and bulk verification output are excluded from source control and release archives. Synthetic fixtures are provided for tests. Dependency/model clearance is recorded per item in `supply/clearance.json`; an absent record remains unreviewed.

## Pinned model declarations

The pinned [Heron card](https://huggingface.co/docling-project/docling-layout-heron/blob/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md) declares Apache-2.0. The pinned [Docling models card](https://huggingface.co/docling-project/docling-models/blob/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md) declares CDLA-Permissive-2.0. These are source declarations, not blanket distribution clearance. Weight downloads retain upstream notices where present.
