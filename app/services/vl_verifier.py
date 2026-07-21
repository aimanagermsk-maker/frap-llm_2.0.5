import base64
import json
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.config.app_config import VisionLanguageConfig
from app.config.paths import PROJECT_ROOT

LLM_REQUEST_FILE_NAME = "llm_request.txt"
PROMPT_FILE = PROJECT_ROOT / "PROMPT.txt"
SECTION_SEPARATOR = "!" * 20
PDF_XML_TAGS = {"LabelFoto", "TDElectronicView"}


class EmptyOllamaResponseError(RuntimeError):
    def __init__(self, ollama_response: dict[str, Any]) -> None:
        super().__init__("Ollama returned an empty response field")
        self.ollama_response = ollama_response


def _pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    import fitz

    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def _pdf_to_base64_pages(pdf_path: Path) -> list[str]:
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count

    return [_pdf_page_to_base64(pdf_path, page_num) for page_num in range(page_count)]


def _pdf_payloads(label_pdf_paths: list[Path]) -> list[dict[str, Any]]:
    payloads = []
    for pdf_path in label_pdf_paths:
        payloads.append(
            {
                "file": str(pdf_path),
                "pages": [
                    {
                        "page": page_num + 1,
                        "imageBase64": image_b64,
                    }
                    for page_num, image_b64 in enumerate(_pdf_to_base64_pages(pdf_path))
                ],
            }
        )
    return payloads


def _whole_pdf_payloads(pdf_paths: list[Path]) -> list[dict[str, Any]]:
    payloads = []
    for pdf_path in pdf_paths:
        payloads.append(
            {
                "file": str(pdf_path),
                "contentType": "application/pdf",
                "pdfBase64": base64.b64encode(pdf_path.read_bytes()).decode("utf-8"),
            }
        )
    return payloads


def _decode_xml_content(xml_content: bytes) -> str:
    try:
        return xml_content.decode("utf-8")
    except UnicodeDecodeError:
        return xml_content.decode("utf-8", errors="replace")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_without_pdf_content(xml_content: bytes) -> str:
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return _decode_xml_content(xml_content)

    for element in root.iter():
        tag_name = _local_name(element.tag)
        text = (element.text or "").strip()
        if tag_name in PDF_XML_TAGS and text.startswith("JVBERi0"):
            element.text = "[PDF_CONTENT_REMOVED]"

    return ET.tostring(root, encoding="unicode")


def _document_checks(extracted_values: dict[str, Any]) -> list[dict[str, Any]]:
    checks = extracted_values.get("checks", [])
    if not isinstance(checks, list):
        return []

    return [
        check
        for check in checks
        if isinstance(check, dict)
    ]


def _required_label_fields(extracted_values: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for check in _document_checks(extracted_values):
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


def _iter_incoming_requests(incoming_json: dict[str, Any]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for section_name in ("capacityList", "technicalDocumentation", "technologicalInstruction"):
        section = incoming_json.get(section_name, [])
        if not isinstance(section, list):
            continue
        for document in section:
            if not isinstance(document, dict):
                continue
            for request in document.get("request", []):
                if isinstance(request, dict) and request.get("prompt"):
                    requests.append(
                        {
                            "section": section_name,
                            "document": {
                                key: value
                                for key, value in document.items()
                                if key != "request"
                            },
                            **request,
                        }
                    )
    return requests


def _tag_names(tag_expression: Any) -> list[str]:
    if not isinstance(tag_expression, str):
        return []

    tags = []
    for separator in ("/", ",", ";"):
        tag_expression = tag_expression.replace(separator, "|")
    for tag in tag_expression.split("|"):
        tag = tag.strip()
        if tag:
            tags.append(tag)
    return tags


def _matching_checks(extracted_values: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    request_tags = set(_tag_names(request.get("tag")))
    if not request_tags:
        return []

    matches = []
    for check in _document_checks(extracted_values):
        check_tags = set(check.get("xmlTags", []))
        if request_tags & check_tags:
            matches.append(check)
    return matches


def _xml_values_for_tags(matches: list[dict[str, Any]], tags: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for match in matches:
        match_values = match.get("values", {})
        if not isinstance(match_values, dict):
            continue
        for tag in tags:
            if tag in match_values:
                values[tag] = match_values[tag]
    return values


def _question_payloads(incoming_json: dict[str, Any], extracted_values: dict[str, Any]) -> list[dict[str, Any]]:
    questions = []
    for request in _iter_incoming_requests(incoming_json):
        tags = _tag_names(request.get("tag"))
        matches = _matching_checks(extracted_values, request)
        questions.append(
            {
                "requestId": request.get("requestId"),
                "section": request.get("section"),
                "document": request.get("document"),
                "description": request.get("description"),
                "tag": request.get("tag"),
                "prompt": request.get("prompt"),
                "referenceFromProductFields": [
                    {
                        "origin": match.get("origin"),
                        "documentType": match.get("documentType"),
                        "documentDescription": match.get("documentDescription"),
                        "xmlTags": match.get("xmlTags"),
                        "criteria": match.get("criteria"),
                    }
                    for match in matches
                ],
                "actualXmlValues": _xml_values_for_tags(matches, tags),
            }
        )
    return questions


def _build_llm_request_text(
    label_pdf_paths: list[Path],
    incoming_json: dict[str, Any],
    extracted_values: dict[str, Any],
    header: dict[str, Any],
    xml_content: bytes,
) -> str:
    prompt_text = PROMPT_FILE.read_text(encoding="utf-8")
    pdf_payload = json.dumps(
        {
            "ticketHeader": header,
            "incomingRequests": _iter_incoming_requests(incoming_json),
            "productFieldReferenceAndXmlValues": _question_payloads(incoming_json, extracted_values),
            "pdfFiles": _whole_pdf_payloads(label_pdf_paths),
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"{prompt_text}\n{SECTION_SEPARATOR}\n{_xml_without_pdf_content(xml_content)}\n{SECTION_SEPARATOR}\n{pdf_payload}"


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
    import requests

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


def _llm_request_path(config: VisionLanguageConfig) -> Path:
    response_text_dir = Path(config.response_text_dir)
    response_text_dir.mkdir(parents=True, exist_ok=True)
    return response_text_dir / LLM_REQUEST_FILE_NAME


def _save_raw_response_text(text: str, config: VisionLanguageConfig) -> Path:
    response_text_path = _llm_request_path(config)
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
    incoming_json: dict[str, Any],
    xml_content: bytes,
    header: dict[str, Any],
    ticket_dir: Path,
    config: VisionLanguageConfig,
) -> Path:
    output_path = ticket_dir / "llm_label_values.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response_text_path = _llm_request_path(config)

    try:
        request_text = _build_llm_request_text(
            label_pdf_paths=label_pdf_paths,
            incoming_json=incoming_json,
            extracted_values=extracted_values,
            header=header,
            xml_content=xml_content,
        )
        _save_raw_response_text(request_text, config)
        result = {
            "ticketId": ticket_dir.name,
            "header": header,
            "status": "LLM_REQUEST_PREPARED",
            "reason": "LLM call is temporarily disabled",
            "model": config.model,
            "url": config.url,
            "jsonMode": config.json_mode,
            "rawResponseTextFile": str(response_text_path),
            "pdfFiles": [str(path) for path in label_pdf_paths],
            "requiredFields": _required_label_fields(extracted_values),
            "requestCount": len(_question_payloads(incoming_json, extracted_values)),
        }
    except Exception as exc:
        _save_raw_response_text(_error_response_text(exc), config)
        result = {
            "ticketId": ticket_dir.name,
            "header": header,
            "status": "ERROR",
            "errorType": type(exc).__name__,
            "error": str(exc),
            "rawResponseTextFile": str(response_text_path),
            "pdfFiles": [str(path) for path in label_pdf_paths],
            "requiredFields": _required_label_fields(extracted_values),
        }

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path
