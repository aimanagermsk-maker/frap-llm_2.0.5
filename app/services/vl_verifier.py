import base64
import json
from pathlib import Path
from typing import Any

import fitz
import requests

from app.config.app_config import VisionLanguageConfig

LABEL_DOCUMENT_TYPE = "LabelFoto"
VERDICT_MATCH = "MATCH"
VERDICT_MISMATCH = "MISMATCH"
VERDICT_UNKNOWN = "UNKNOWN"
VERDICT_ERROR = "ERROR"


class EmptyOllamaResponseError(RuntimeError):
    def __init__(self, ollama_response: dict[str, Any]) -> None:
        super().__init__("Ollama returned an empty response field")
        self.ollama_response = ollama_response


def _verdict_text(verdict: str) -> str:
    return {
        VERDICT_MATCH: "совпадает",
        VERDICT_MISMATCH: "не совпадает",
        VERDICT_UNKNOWN: "не удалось определить",
        VERDICT_ERROR: "ошибка проверки",
    }.get(verdict, "не удалось определить")


def _pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def _pdf_to_base64_pages(pdf_path: Path) -> list[str]:
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count

    return [_pdf_page_to_base64(pdf_path, page_num) for page_num in range(page_count)]


def _label_checks(extracted_values: dict[str, Any]) -> list[dict[str, Any]]:
    checks = extracted_values.get("checks", [])
    if not isinstance(checks, list):
        return []

    return [
        check
        for check in checks
        if isinstance(check, dict) and check.get("documentType") == LABEL_DOCUMENT_TYPE
    ]


def _build_prompt(extracted_values: dict[str, Any]) -> str:
    payload = {
        "ticket": {
            "id": extracted_values.get("id"),
            "type": extracted_values.get("type"),
            "date": extracted_values.get("date"),
            "uri": extracted_values.get("uri"),
            "productTypeGroup": extracted_values.get("productTypeGroup"),
        },
        "labelChecks": _label_checks(extracted_values),
    }

    return (
        "Ты проверяешь макеты этикеток алкогольной продукции. "
        "На изображениях перед тобой страницы PDF из XML-тегов LabelFoto. "
        "Сравни информацию на этикетке/этикетках с XML-значениями ниже. "
        "Проверяй только пункты labelChecks: их xmlTags, values и criteria. "
        "Верни строго JSON без markdown в формате: "
        '{"verdict":"MATCH|MISMATCH","summary":"краткий вывод",'
        '"mismatches":[{"field":"...","labelValue":"...","xmlValue":"...","reason":"..."}]}. '
        "Если все проверяемые сведения совпадают, verdict должен быть MATCH. "
        "Если есть хотя бы одно значимое расхождение, verdict должен быть MISMATCH.\n\n"
        f"XML_VALUES:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _send_to_ollama(
    images_b64: list[str],
    prompt: str,
    config: VisionLanguageConfig,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "prompt": prompt,
        "images": images_b64,
        "stream": False,
    }
    if config.json_mode:
        payload["format"] = "json"

    response = requests.post(
        config.url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    ollama_response = response.json()
    raw_response = ollama_response.get("response", "")
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise EmptyOllamaResponseError(ollama_response)
    return ollama_response


def _extract_verdict(raw_response: str) -> str:
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        upper_response = raw_response.upper()
        if VERDICT_MISMATCH in upper_response:
            return VERDICT_MISMATCH
        if VERDICT_MATCH in upper_response:
            return VERDICT_MATCH
        return VERDICT_UNKNOWN

    verdict = parsed.get("verdict")
    if isinstance(verdict, str) and verdict.upper() in {VERDICT_MATCH, VERDICT_MISMATCH}:
        return verdict.upper()
    return VERDICT_UNKNOWN


def verify_label_pdfs(
    label_pdf_paths: list[Path],
    extracted_values: dict[str, Any],
    ticket_dir: Path,
    config: VisionLanguageConfig,
) -> Path:
    verdict_path = ticket_dir / "llm_verdict.json"
    verdict_path.parent.mkdir(parents=True, exist_ok=True)

    if not config.enabled:
        verdict = {
            "ticketId": ticket_dir.name,
            "verdict": VERDICT_UNKNOWN,
            "verdictText": _verdict_text(VERDICT_UNKNOWN),
            "status": "SKIPPED",
            "reason": "VL verification is disabled",
            "labelPdfFiles": [str(path) for path in label_pdf_paths],
        }
    elif not label_pdf_paths:
        verdict = {
            "ticketId": ticket_dir.name,
            "verdict": VERDICT_UNKNOWN,
            "verdictText": _verdict_text(VERDICT_UNKNOWN),
            "status": "SKIPPED",
            "reason": "No LabelFoto PDF files found",
            "labelPdfFiles": [],
        }
    else:
        prompt = _build_prompt(extracted_values)
        prompt_path = ticket_dir / "vl_prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        try:
            images_b64: list[str] = []
            for pdf_path in label_pdf_paths:
                images_b64.extend(_pdf_to_base64_pages(pdf_path))

            ollama_response = _send_to_ollama(images_b64, prompt, config)
            raw_response = ollama_response["response"]
            parsed_verdict = _extract_verdict(raw_response)
            verdict = {
                "ticketId": ticket_dir.name,
                "verdict": parsed_verdict,
                "verdictText": _verdict_text(parsed_verdict),
                "status": "OK",
                "model": config.model,
                "url": config.url,
                "jsonMode": config.json_mode,
                "imageCount": len(images_b64),
                "promptFile": str(prompt_path),
                "labelPdfFiles": [str(path) for path in label_pdf_paths],
                "checkedFields": _label_checks(extracted_values),
                "rawResponse": raw_response,
                "ollamaResponse": ollama_response,
            }
        except EmptyOllamaResponseError as exc:
            verdict = {
                "ticketId": ticket_dir.name,
                "verdict": VERDICT_ERROR,
                "verdictText": _verdict_text(VERDICT_ERROR),
                "status": "ERROR",
                "errorType": "EMPTY_OLLAMA_RESPONSE",
                "error": str(exc),
                "model": config.model,
                "url": config.url,
                "jsonMode": config.json_mode,
                "promptFile": str(prompt_path),
                "labelPdfFiles": [str(path) for path in label_pdf_paths],
                "checkedFields": _label_checks(extracted_values),
                "ollamaResponse": exc.ollama_response,
            }
        except Exception as exc:
            verdict = {
                "ticketId": ticket_dir.name,
                "verdict": VERDICT_ERROR,
                "verdictText": _verdict_text(VERDICT_ERROR),
                "status": "ERROR",
                "errorType": type(exc).__name__,
                "error": str(exc),
                "model": config.model,
                "url": config.url,
                "jsonMode": config.json_mode,
                "promptFile": str(prompt_path),
                "labelPdfFiles": [str(path) for path in label_pdf_paths],
                "checkedFields": _label_checks(extracted_values),
            }

    verdict_path.write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return verdict_path
