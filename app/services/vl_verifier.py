import base64
import json
import traceback
from pathlib import Path
from typing import Any

import fitz
import requests

from app.config.app_config import VisionLanguageConfig

LABEL_DOCUMENT_TYPE = "LabelFoto"


class EmptyOllamaResponseError(RuntimeError):
    def __init__(self, ollama_response: dict[str, Any]) -> None:
        super().__init__("Ollama returned an empty response field")
        self.ollama_response = ollama_response


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


def _required_label_fields(extracted_values: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for check in _label_checks(extracted_values):
        for tag_name in check.get("xmlTags", []):
            if not isinstance(tag_name, str):
                continue
            fields.append(
                {
                    "field": tag_name,
                    "documentDescription": check.get("documentDescription"),
                    "criteria": check.get("criteria"),
                }
            )
    return fields


def _build_prompt(extracted_values: dict[str, Any]) -> str:
    payload = {
        "ticket": {
            "id": extracted_values.get("id"),
            "type": extracted_values.get("type"),
            "date": extracted_values.get("date"),
            "uri": extracted_values.get("uri"),
            "productTypeGroup": extracted_values.get("productTypeGroup"),
        },
        "requiredLabelFields": _required_label_fields(extracted_values),
    }

    return (
        "На изображениях перед тобой страницы PDF из XML-тегов LabelFoto. "
        "Твоя задача: только извлечь значения требуемых полей с этикетки или этикеток. "
        "Не сравнивай с XML, не делай финальный вердикт, не оценивай корректность. "
        "Если поле на этикетке не найдено, верни null для value и кратко укажи evidence. "
        "Верни строго JSON без markdown в формате: "
        '{"fields":[{"field":"...","value":"...","evidence":"...","confidence":0.0}]}. '
        "confidence должен быть числом от 0 до 1. "
        "Извлекай только поля из requiredLabelFields.\n\n"
        f"PAYLOAD:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
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


def _parse_label_values(raw_response: str) -> Any:
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        return None


def _ollama_response_without_context(ollama_response: dict[str, Any]) -> dict[str, Any]:
    cleaned_response = dict(ollama_response)
    cleaned_response.pop("context", None)
    return cleaned_response


def _save_raw_response_text(text: str, ticket_id: str, config: VisionLanguageConfig) -> Path:
    response_text_dir = Path(config.response_text_dir)
    response_text_dir.mkdir(parents=True, exist_ok=True)
    response_text_path = response_text_dir / f"{ticket_id}_llm_response.txt"
    response_text_path.write_text(text, encoding="utf-8")
    return response_text_path


def _error_response_text(exc: Exception) -> str:
    return "\n".join(
        [
            "LLM request failed.",
            f"errorType: {type(exc).__name__}",
            f"error: {exc}",
            "",
            "traceback:",
            traceback.format_exc(),
        ]
    )


def extract_label_values(
    label_pdf_paths: list[Path],
    extracted_values: dict[str, Any],
    header: dict[str, Any],
    ticket_dir: Path,
    config: VisionLanguageConfig,
) -> Path:
    output_path = ticket_dir / "llm_label_values.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response_text_path = _save_raw_response_text(
        "LLM response file created before request.\nstatus: NOT_STARTED\n",
        ticket_dir.name,
        config,
    )

    if not config.enabled:
        response_text_path.write_text(
            "LLM request skipped.\nreason: VL extraction is disabled\n",
            encoding="utf-8",
        )
        result = {
            "ticketId": ticket_dir.name,
            "header": header,
            "status": "SKIPPED",
            "reason": "VL extraction is disabled",
            "rawResponseTextFile": str(response_text_path),
            "labelPdfFiles": [str(path) for path in label_pdf_paths],
            "requiredFields": _required_label_fields(extracted_values),
        }
    elif not label_pdf_paths:
        response_text_path.write_text(
            "LLM request skipped.\nreason: No LabelFoto PDF files found\n",
            encoding="utf-8",
        )
        result = {
            "ticketId": ticket_dir.name,
            "header": header,
            "status": "SKIPPED",
            "reason": "No LabelFoto PDF files found",
            "rawResponseTextFile": str(response_text_path),
            "labelPdfFiles": [],
            "requiredFields": _required_label_fields(extracted_values),
        }
    else:
        prompt_path = ticket_dir / "vl_prompt.txt"
        try:
            prompt = _build_prompt(extracted_values)
            prompt_path.write_text(prompt, encoding="utf-8")
            images_b64: list[str] = []
            for pdf_path in label_pdf_paths:
                images_b64.extend(_pdf_to_base64_pages(pdf_path))

            ollama_response = _send_to_ollama(images_b64, prompt, config)
            raw_response = ollama_response["response"]
            response_text_path.write_text(raw_response, encoding="utf-8")
            result = {
                "ticketId": ticket_dir.name,
                "header": header,
                "status": "OK",
                "model": config.model,
                "url": config.url,
                "jsonMode": config.json_mode,
                "imageCount": len(images_b64),
                "promptFile": str(prompt_path),
                "rawResponseTextFile": str(response_text_path),
                "labelPdfFiles": [str(path) for path in label_pdf_paths],
                "requiredFields": _required_label_fields(extracted_values),
                "labelValues": _parse_label_values(raw_response),
                "rawResponse": raw_response,
                "ollamaResponse": _ollama_response_without_context(ollama_response),
            }
        except EmptyOllamaResponseError as exc:
            response_text_path.write_text(
                json.dumps(_ollama_response_without_context(exc.ollama_response), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result = {
                "ticketId": ticket_dir.name,
                "header": header,
                "status": "ERROR",
                "errorType": "EMPTY_OLLAMA_RESPONSE",
                "error": str(exc),
                "model": config.model,
                "url": config.url,
                "jsonMode": config.json_mode,
                "promptFile": str(prompt_path),
                "rawResponseTextFile": str(response_text_path),
                "labelPdfFiles": [str(path) for path in label_pdf_paths],
                "requiredFields": _required_label_fields(extracted_values),
                "ollamaResponse": _ollama_response_without_context(exc.ollama_response),
            }
        except Exception as exc:
            response_text_path.write_text(_error_response_text(exc), encoding="utf-8")
            result = {
                "ticketId": ticket_dir.name,
                "header": header,
                "status": "ERROR",
                "errorType": type(exc).__name__,
                "error": str(exc),
                "model": config.model,
                "url": config.url,
                "jsonMode": config.json_mode,
                "promptFile": str(prompt_path),
                "rawResponseTextFile": str(response_text_path),
                "labelPdfFiles": [str(path) for path in label_pdf_paths],
                "requiredFields": _required_label_fields(extracted_values),
            }

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path
