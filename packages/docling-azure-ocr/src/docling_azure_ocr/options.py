from typing import Any, ClassVar

from docling.datamodel.pipeline_options import OcrOptions
from pydantic import Field


class AzureReadOcrOptions(OcrOptions):
    kind: ClassVar[str] = "azure_read"
    lang: list[str] = Field(default_factory=list)
    runtime: Any = Field(exclude=True, repr=False)
