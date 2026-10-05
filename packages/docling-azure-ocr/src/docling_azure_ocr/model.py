from docling.models.base_ocr_model import BaseOcrModel
from docling_core.types.doc import BoundingBox, CoordOrigin
from docling_core.types.doc.page import BoundingRectangle, TextCell

from .config import OcrError
from .options import AzureReadOcrOptions


class AzureReadOcrModel(BaseOcrModel):
    def __init__(self, *, options: AzureReadOcrOptions, **kwargs):
        super().__init__(options=options, **kwargs)
        self.runtime = options.runtime
        if self.runtime.profile.provider != "azure_read" or not self.runtime.profile.enabled:
            raise OcrError("ocr_disabled")
        if options.lang:
            raise ValueError("初期Azure Readプロファイルは言語自動判定のみ対応します。")

    @classmethod
    def get_options_type(cls):
        return AzureReadOcrOptions

    def __call__(self, conv_res, page_batch):
        for page in page_batch:
            if not self.enabled or not page._backend or not page._backend.is_valid():
                yield page
                continue
            rects = [r for r in self.get_ocr_rects(page) if r.area() > 0]
            if not rects:
                yield page
                continue
            image = page._backend.get_page_image(scale=self.options.scale)
            geometry = (
                page.parsed_page.dimension.model_dump(mode="json") if page.parsed_page else {}
            )
            value = self.runtime.read(
                image,
                f"pdf:{page.page_no}",
                {
                    "page_number": page.page_no,
                    "page_size": page.size.model_dump(),
                    "page_geometry": geometry,
                    "ocr_rects": [r.model_dump(mode="json") for r in rects],
                    "coordinate_frame": "docling_backend_page_top_left",
                    "render_scale": self.options.scale,
                },
            )
            width, height = value["image_size"]
            sx, sy = page.size.width / width, page.size.height / height
            # Backend rendering already applies CropBox and rotation. Do not rotate Azure words again.
            value["image_to_page"] = [sx, 0, 0, 0, sy, 0]
            cells = []
            for word in value["words"]:
                xs, ys = word["polygon"][0::2], word["polygon"][1::2]
                bbox = BoundingBox(
                    l=min(xs) * sx,
                    t=min(ys) * sy,
                    r=max(xs) * sx,
                    b=max(ys) * sy,
                    coord_origin=CoordOrigin.TOPLEFT,
                )
                if not any(bbox.intersection_area_with(r) > 0 for r in rects):
                    continue
                cells.append(
                    TextCell(
                        index=len(cells),
                        text=word["text"],
                        orig=word["text"],
                        from_ocr=True,
                        confidence=word["confidence"] if word["confidence"] is not None else 0.0,
                        rect=BoundingRectangle.from_bounding_box(bbox),
                    )
                )
            self.post_process_cells(cells, page, conv_res)
            accepted = {c.text for c in page.parsed_page.textline_cells if c.from_ocr}
            value["accepted_words"] = [
                w
                for w in value["words"]
                if w["text"] in accepted
                and any(
                    BoundingBox(
                        l=min(w["polygon"][0::2]) * sx,
                        t=min(w["polygon"][1::2]) * sy,
                        r=max(w["polygon"][0::2]) * sx,
                        b=max(w["polygon"][1::2]) * sy,
                        coord_origin=CoordOrigin.TOPLEFT,
                    ).intersection_area_with(r)
                    > 0
                    for r in rects
                )
            ]
            yield page
