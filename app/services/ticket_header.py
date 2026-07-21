import base64
import hashlib
import xml.etree.ElementTree as ET
from typing import Any

TABLE_WINES_PRODUCT_TYPE_GROUP = "\u0412\u0438\u043d\u0430 \u0441\u0442\u043e\u043b\u043e\u0432\u044b\u0435 \u0438 \u0432\u0438\u043d\u043e\u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root: ET.Element, tag_name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) != tag_name:
            continue
        text = (element.text or "").strip()
        if text:
            return text
    return None


def _texts(root: ET.Element, tag_name: str) -> list[str]:
    values = []
    for element in root.iter():
        if _local_name(element.tag) != tag_name:
            continue
        text = (element.text or "").strip()
        if text:
            values.append(text)
    return values


def _product_type_group_from_vid_ap(vid_ap: str | None) -> str | None:
    if not vid_ap:
        return None

    value = vid_ap.lower()
    if "пиво" in value:
        return "Пиво и пивные напитки"
    if "игрист" in value or "шипуч" in value:
        return "Вина игристые и шипучие"
    if "вино" in value or "виномат" in value:
        return "Вина столовые и виноматериалы"
    if "коньяк" in value or "бренди" in value:
        return "Коньяки и бренди"
    if "водк" in value:
        return "Водка и водки особые"
    if "сидр" in value or "пуаре" in value or "медовух" in value:
        return "Сидр, пуаре, медовуха"
    if "ликеро" in value or "ликёр" in value or "ликер" in value:
        return "Ликеро-водочные изделия"
    return None


def _origin_from_xml(root: ET.Element) -> str | None:
    country_origin = _first_text(root, "CountryOrigin")
    if country_origin == "643":
        return "РФ"
    if country_origin:
        return "Импорт"

    capacity_descriptions = " ".join(_texts(root, "CapacityDescrVal")).lower()
    if "импорт" in capacity_descriptions:
        return "Импорт"
    if "россий" in capacity_descriptions or "рф" in capacity_descriptions:
        return "РФ"
    return None


def _label_foto_hashes(root: ET.Element) -> list[str]:
    hashes = []
    for element in root.iter():
        if _local_name(element.tag) != "LabelFoto":
            continue
        text = (element.text or "").strip()
        if not text:
            continue
        hashes.append(hashlib.md5(base64.b64decode(text)).hexdigest())
    return hashes


def build_ticket_header(incoming_json: dict[str, Any], xml_content: bytes) -> dict[str, Any]:
    root = ET.fromstring(xml_content)
    vid_ap = _first_text(root, "VidAP")
    origin = (
        incoming_json.get("original")
        or incoming_json.get("origin")
    )

    return {
        "id": incoming_json.get("id"),
        "type": incoming_json.get("type"),
        "date": incoming_json.get("date"),
        "uri": incoming_json.get("uri"),
        "productTypeGroup": TABLE_WINES_PRODUCT_TYPE_GROUP,
        "original": origin,
        "capacityList": [
            {"md5Hash": md5_hash}
            for md5_hash in _label_foto_hashes(root)
        ],
        "xmlSource": {
            "VidAP": vid_ap,
            "CountryOrigin": _first_text(root, "CountryOrigin"),
        },
    }
