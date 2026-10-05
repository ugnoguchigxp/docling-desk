from .model import AzureReadOcrModel


def ocr_engines():
    return {"ocr_engines": [AzureReadOcrModel]}
