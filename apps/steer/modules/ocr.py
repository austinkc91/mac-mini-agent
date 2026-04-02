"""OCR using Tesseract on Windows."""

from dataclasses import dataclass

from modules.errors import SteerError
from modules.tools import require


@dataclass
class OCRResult:
    text: str
    confidence: float
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


def _preprocess_for_ocr(img):
    """Preprocess image for better OCR: grayscale, contrast boost, upscale small text."""
    from PIL import Image, ImageEnhance, ImageFilter

    # Convert to grayscale — Tesseract works best on grayscale
    if img.mode != "L":
        img = img.convert("L")

    # Upscale small images so Tesseract can read fine text (target ~2400px wide)
    _ocr_target_width = 2400
    if img.width < _ocr_target_width:
        scale = _ocr_target_width / img.width
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), resample=Image.LANCZOS)
    elif img.width > 3200:
        # Only downscale very large images
        scale = 3200 / img.width
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), resample=Image.LANCZOS)

    # Sharpen to bring out text edges
    img = img.filter(ImageFilter.SHARPEN)

    # Boost contrast — makes text stand out from background
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.8)

    return img


def recognize(image_path: str, minimum_confidence: float = 0.5) -> list[OCRResult]:
    """Run OCR on an image file using Tesseract.

    Returns list of recognized text regions with bounding boxes.
    """
    require("tesseract")
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise SteerError("pytesseract or Pillow not installed")

    original = Image.open(image_path)
    img = _preprocess_for_ocr(original)

    # Scale factor to map OCR coordinates back to original image coordinates
    scale = original.width / img.width

    # Use better Tesseract config for screen text
    custom_config = r"--oem 3 --psm 6"
    data = pytesseract.image_to_data(
        img, output_type=pytesseract.Output.DICT, config=custom_config
    )

    results = []
    n_boxes = len(data["text"])

    current_text = []
    current_box = None
    current_conf = []

    for i in range(n_boxes):
        text = data["text"][i].strip()
        conf = float(data["conf"][i]) / 100.0 if data["conf"][i] != "-1" else 0.0

        if not text:
            if current_text and current_box:
                avg_conf = sum(current_conf) / len(current_conf)
                if avg_conf >= minimum_confidence:
                    results.append(OCRResult(
                        text=" ".join(current_text),
                        confidence=round(avg_conf, 2),
                        x=current_box[0],
                        y=current_box[1],
                        width=current_box[2],
                        height=current_box[3],
                    ))
            current_text = []
            current_box = None
            current_conf = []
            continue

        x = int(data["left"][i] / scale) if scale != 1.0 else data["left"][i]
        y = int(data["top"][i] / scale) if scale != 1.0 else data["top"][i]
        w = int(data["width"][i] / scale) if scale != 1.0 else data["width"][i]
        h = int(data["height"][i] / scale) if scale != 1.0 else data["height"][i]

        if current_box is None:
            current_box = [x, y, w, h]
            current_text = [text]
            current_conf = [conf]
        else:
            if abs(y - current_box[1]) < current_box[3] * 0.5:
                new_right = max(current_box[0] + current_box[2], x + w)
                current_box[2] = new_right - current_box[0]
                current_box[3] = max(current_box[3], h)
                current_text.append(text)
                current_conf.append(conf)
            else:
                avg_conf = sum(current_conf) / len(current_conf)
                if avg_conf >= minimum_confidence:
                    results.append(OCRResult(
                        text=" ".join(current_text),
                        confidence=round(avg_conf, 2),
                        x=current_box[0],
                        y=current_box[1],
                        width=current_box[2],
                        height=current_box[3],
                    ))
                current_box = [x, y, w, h]
                current_text = [text]
                current_conf = [conf]

    if current_text and current_box:
        avg_conf = sum(current_conf) / len(current_conf)
        if avg_conf >= minimum_confidence:
            results.append(OCRResult(
                text=" ".join(current_text),
                confidence=round(avg_conf, 2),
                x=current_box[0],
                y=current_box[1],
                width=current_box[2],
                height=current_box[3],
            ))

    return results


def to_elements(results: list[OCRResult]) -> list[dict]:
    """Convert OCR results to UI element dicts compatible with ElementStore."""
    elements = []
    for i, r in enumerate(results):
        elements.append({
            "id": f"O{i + 1}",
            "role": "ocrtext",
            "label": r.text,
            "value": None,
            "x": r.x,
            "y": r.y,
            "width": r.width,
            "height": r.height,
            "isEnabled": True,
            "depth": 0,
        })
    return elements
