import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.services.product_fields import ProductFieldCheck, get_product_fields
from app.services.ticket_header import TABLE_WINES_PRODUCT_TYPE_GROUP


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ET.Element) -> str | None:
    text = (element.text or "").strip()
    return text or None


def _child_values(element: ET.Element) -> dict[str, Any]:
    values: dict[str, Any] = {}

    for child in list(element):
        child_name = _local_name(child.tag)
        child_text = _element_text(child)
        value: Any = child_text if child_text is not None else _child_values(child)

        if value in ("", {}, []):
            continue

        if child_name in values:
            existing = values[child_name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                values[child_name] = [existing, value]
        else:
            values[child_name] = value

    return values


def _element_value(element: ET.Element) -> Any:
    text = _element_text(element)
    children = _child_values(element)

    if children and text:
        return {"text": text, "children": children}
    if children:
        return children
    return text


def extract_xml_tag_values(xml_content: bytes, tag_name: str) -> list[Any]:
    root = ET.fromstring(xml_content)
    values = []

    for element in root.iter():
        if _local_name(element.tag) != tag_name:
            continue

        value = _element_value(element)
        if value not in (None, "", {}, []):
            values.append(value)

    return values


def extract_check_values(xml_content: bytes, check: ProductFieldCheck) -> dict[str, list[Any]]:
    return {
        tag_name: extract_xml_tag_values(xml_content, tag_name)
        for tag_name in check.xml_tags
    }


def build_extracted_values(data: dict[str, Any], xml_content: bytes) -> dict[str, Any]:
    product_type_group = TABLE_WINES_PRODUCT_TYPE_GROUP
    product_fields = get_product_fields(product_type_group)
    checks = []

    for check in product_fields.checks:
        checks.append(
            {
                "origin": check.origin,
                "documentType": check.document_type,
                "documentDescription": check.document_description,
                "xmlTags": check.xml_tags,
                "values": extract_check_values(xml_content, check),
                "criteria": check.criteria,
            }
        )

    return {
        "id": data.get("id"),
        "type": data.get("type"),
        "date": data.get("date"),
        "uri": data.get("uri"),
        "productTypeGroup": product_type_group,
        "checks": checks,
    }


def save_extracted_values(
    data: dict[str, Any],
    xml_content: bytes,
    output_dir: str,
    source_stem: str,
) -> Path:
    directory = Path(output_dir) / "extracted_values"
    directory.mkdir(parents=True, exist_ok=True)

    file_path = directory / f"{source_stem}_values.json"
    file_path.write_text(
        json.dumps(build_extracted_values(data, xml_content), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return file_path
