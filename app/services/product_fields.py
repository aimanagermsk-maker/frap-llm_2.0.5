import json
from dataclasses import dataclass
from functools import lru_cache

from app.config.paths import SETTINGS_DIR


PRODUCT_FIELDS_FILE = SETTINGS_DIR / "product_fields.json"
COMMON_PRODUCT_TYPE_GROUP = "Все категории"


@dataclass(frozen=True)
class ProductFieldCheck:
    origin: str
    document_type: str
    document_description: str
    xml_tags: list[str]
    criteria: str


@dataclass(frozen=True)
class ProductFields:
    product_type_group: str
    checks: list[ProductFieldCheck]


def _load_product_fields_file() -> list[dict]:
    data = json.loads(PRODUCT_FIELDS_FILE.read_text(encoding="utf-8"))
    product_fields = data.get("productFields")
    if not isinstance(product_fields, list):
        raise ValueError(f"Некорректный формат справочника: {PRODUCT_FIELDS_FILE}")
    return product_fields


def _check_from_dict(data: dict) -> ProductFieldCheck:
    origin = data.get("origin")
    document_type = data.get("documentType")
    document_description = data.get("documentDescription")
    xml_tags = data.get("xmlTags", [])
    criteria = data.get("criteria")

    if not isinstance(origin, str) or not origin:
        raise ValueError("В проверке справочника поле origin должно быть непустой строкой")
    if not isinstance(document_type, str) or not document_type:
        raise ValueError("В проверке справочника поле documentType должно быть непустой строкой")
    if not isinstance(document_description, str) or not document_description:
        raise ValueError("В проверке справочника поле documentDescription должно быть непустой строкой")
    if not isinstance(xml_tags, list) or not all(isinstance(item, str) for item in xml_tags):
        raise ValueError("В проверке справочника поле xmlTags должно быть массивом строк")
    if not isinstance(criteria, str):
        raise ValueError("В проверке справочника поле criteria должно быть строкой")

    return ProductFieldCheck(
        origin=origin,
        document_type=document_type,
        document_description=document_description,
        xml_tags=xml_tags,
        criteria=criteria,
    )


@lru_cache
def get_product_fields_map() -> dict[str, ProductFields]:
    result: dict[str, ProductFields] = {}

    for item in _load_product_fields_file():
        if not isinstance(item, dict):
            raise ValueError(f"Элемент справочника должен быть объектом: {item!r}")

        product_type_group = item.get("productTypeGroup")
        if not isinstance(product_type_group, str) or not product_type_group:
            raise ValueError("В элементе справочника нет productTypeGroup")

        checks = item.get("checks")
        if not isinstance(checks, list):
            raise ValueError(f"В справочнике для {product_type_group!r} поле checks должно быть массивом")

        result[product_type_group] = ProductFields(
            product_type_group=product_type_group,
            checks=[_check_from_dict(check) for check in checks],
        )

    return result


def get_product_fields(product_type_group: str, include_common: bool = True) -> ProductFields:
    fields_map = get_product_fields_map()
    try:
        product_fields = fields_map[product_type_group]
    except KeyError as exc:
        available = ", ".join(sorted(fields_map))
        raise ValueError(
            f"Нет настроек полей для productTypeGroup={product_type_group!r}. "
            f"Доступные значения: {available}"
        ) from exc

    if not include_common or product_type_group == COMMON_PRODUCT_TYPE_GROUP:
        return product_fields

    common_fields = fields_map.get(COMMON_PRODUCT_TYPE_GROUP)
    if common_fields is None:
        return product_fields

    return ProductFields(
        product_type_group=product_fields.product_type_group,
        checks=[*common_fields.checks, *product_fields.checks],
    )
