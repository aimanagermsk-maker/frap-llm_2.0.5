import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from confluent_kafka import Consumer, KafkaError, Producer

from app.services.xml_value_extractor import save_extracted_values


DEFAULT_BOOTSTRAP_SERVERS = "gitlab-ci.ru:9092"
DEFAULT_INPUT_TOPIC = "frap-llm-helper-in"
DEFAULT_OUTPUT_TOPIC = "frap-llm-helper-out"
DEFAULT_GROUP_ID = "frap-llm-helper-reader"
DEFAULT_SEND_JSON_FILE = "testFrapLLM.json"
DEFAULT_FILES_ROOT = "."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Получает одно JSON-сообщение из Kafka, сохраняет его на диск "
            "берет связанный файл по пути ./date/type/uri "
            "и отправляет JSON из testFrapLLM.json в Kafka."
        )
    )
    parser.add_argument(
        "--bootstrap-servers",
        default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", DEFAULT_BOOTSTRAP_SERVERS),
        help=f"Адрес Kafka bootstrap server. По умолчанию: {DEFAULT_BOOTSTRAP_SERVERS}",
    )
    parser.add_argument(
        "--input-topic",
        default=os.getenv("KAFKA_INPUT_TOPIC", DEFAULT_INPUT_TOPIC),
        help="Топик, из которого читаем сообщение.",
    )
    parser.add_argument(
        "--output-topic",
        default=os.getenv("KAFKA_OUTPUT_TOPIC", DEFAULT_OUTPUT_TOPIC),
        help="Топик, в который отправляем произвольный JSON.",
    )
    parser.add_argument(
        "--group-id",
        default=os.getenv("KAFKA_GROUP_ID", DEFAULT_GROUP_ID),
        help="Consumer group id.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("KAFKA_OUTPUT_DIR", "received_messages"),
        help="Папка для сохранения входящего JSON.",
    )
    parser.add_argument(
        "--files-root",
        default=os.getenv("KAFKA_FILES_ROOT", DEFAULT_FILES_ROOT),
        help="Корневая папка для поиска файла по пути date/type/uri.",
    )
    parser.add_argument(
        "--send-json",
        default=os.getenv("KAFKA_SEND_JSON"),
        help='JSON-строка для отправки, например: {"status":"ok"}',
    )
    parser.add_argument(
        "--send-json-file",
        default=os.getenv("KAFKA_SEND_JSON_FILE"),
        help=f"Путь к JSON-файлу, содержимое которого нужно отправить. По умолчанию: {DEFAULT_SEND_JSON_FILE}",
    )
    return parser.parse_args()


def load_json_from_message(raw_value: bytes | str | None) -> Any:
    if raw_value is None:
        raise ValueError("Сообщение Kafka пустое")

    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")

    return json.loads(raw_value)


def kafka_config(args: argparse.Namespace, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"bootstrap.servers": args.bootstrap_servers}

    optional_env_map = {
        "KAFKA_SECURITY_PROTOCOL": "security.protocol",
        "KAFKA_SASL_MECHANISM": "sasl.mechanism",
        "KAFKA_SASL_USERNAME": "sasl.username",
        "KAFKA_SASL_PASSWORD": "sasl.password",
        "KAFKA_SSL_CA_LOCATION": "ssl.ca.location",
    }
    for env_name, kafka_name in optional_env_map.items():
        value = os.getenv(env_name)
        if value:
            config[kafka_name] = value

    if extra:
        config.update(extra)

    return config


def save_json_to_disk(data: Any, output_dir: str) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    file_path = directory / f"kafka_message_{timestamp}.json"
    file_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return file_path


def validate_path_part(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Поле {field_name!r} должно быть непустой строкой")

    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or ".." in path.parts:
        raise ValueError(f"Поле {field_name!r} содержит недопустимое имя пути: {value!r}")

    return value


def get_related_file_path(data: Any, files_root: str) -> Path:
    if not isinstance(data, dict):
        raise ValueError("Входящий JSON должен быть объектом с полями type, date и uri")

    date = validate_path_part("date", data.get("date"))
    type_ = validate_path_part("type", data.get("type"))
    uri = validate_path_part("uri", data.get("uri"))

    return Path(files_root) / date / type_ / uri


def read_related_file(data: Any, files_root: str) -> tuple[Path, bytes]:
    file_path = get_related_file_path(data, files_root)
    if not file_path.is_file():
        raise FileNotFoundError(f"Файл из входящего JSON не найден: {file_path}")

    return file_path, file_path.read_bytes()


def extract_pdfs(xml_content: bytes) -> dict[str, list[bytes]]:
    root = ET.fromstring(xml_content)
    pdfs: dict[str, list[bytes]] = {
        "LabelFoto": [],
        "TDElectronicView": [],
    }

    for element in root.iter():
        tag_name = element.tag.rsplit("}", 1)[-1]
        text = (element.text or "").strip()
        if tag_name not in pdfs or not text.startswith("JVBERi0"):
            continue

        pdfs[tag_name].append(base64.b64decode(text))

    return pdfs


def extract_pdf_hashes(xml_content: bytes) -> dict[str, list[str]]:
    return {
        tag_name: [hashlib.md5(pdf_content).hexdigest() for pdf_content in pdf_list]
        for tag_name, pdf_list in extract_pdfs(xml_content).items()
    }


def save_extracted_pdfs(xml_content: bytes, output_dir: str, source_stem: str) -> list[Path]:
    pdfs = extract_pdfs(xml_content)
    directory = Path(output_dir) / "pdf_files"
    directory.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for tag_name, pdf_list in pdfs.items():
        for index, pdf_content in enumerate(pdf_list, start=1):
            file_path = directory / f"{source_stem}_{tag_name}_{index}.pdf"
            file_path.write_bytes(pdf_content)
            saved_paths.append(file_path)

    return saved_paths


def expected_hashes(data: Any, collection_name: str) -> list[str]:
    if not isinstance(data, dict):
        raise ValueError("Входящий JSON должен быть объектом")

    collection = data.get(collection_name, [])
    if not isinstance(collection, list):
        raise ValueError(f"Поле {collection_name!r} должно быть массивом")

    hashes = []
    for index, item in enumerate(collection, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Элемент {collection_name}[{index}] должен быть объектом")

        md5_hash = item.get("md5Hash")
        if not isinstance(md5_hash, str) or not md5_hash:
            raise ValueError(f"В элементе {collection_name}[{index}] нет md5Hash")

        hashes.append(md5_hash.lower())

    return hashes


def compare_hash_group(name: str, expected: list[str], actual: list[str]) -> list[str]:
    errors = []
    if len(expected) != len(actual):
        errors.append(
            f"{name}: ожидается {len(expected)} PDF, найдено {len(actual)}"
        )

    for index, expected_hash in enumerate(expected):
        actual_hash = actual[index] if index < len(actual) else None
        if actual_hash != expected_hash:
            errors.append(
                f"{name}[{index + 1}]: ожидается md5={expected_hash}, "
                f"получено md5={actual_hash or 'нет PDF'}"
            )

    return errors


def validate_pdf_hashes(data: Any, xml_content: bytes) -> None:
    actual = extract_pdf_hashes(xml_content)

    errors = []
    errors.extend(
        compare_hash_group(
            "capacityList/LabelFoto",
            expected_hashes(data, "capacityList"),
            actual["LabelFoto"],
        )
    )
    errors.extend(
        compare_hash_group(
            "technicalDocumentation/TDElectronicView",
            expected_hashes(data, "technicalDocumentation"),
            actual["TDElectronicView"],
        )
    )

    if errors:
        raise ValueError("MD5 PDF не совпадают:\n" + "\n".join(errors))

    print(
        "MD5 PDF совпадают: "
        f"LabelFoto={len(actual['LabelFoto'])}, "
        f"TDElectronicView={len(actual['TDElectronicView'])}"
    )


def receive_one_json(args: argparse.Namespace) -> tuple[Any, Path]:
    consumer = Consumer(
        kafka_config(
            args,
            {
                "group.id": args.group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            },
        )
    )

    try:
        consumer.subscribe([args.input_topic])
        message = None
        while message is None:
            message = consumer.poll(1.0)

        if message.error():
            if message.error().code() == KafkaError._PARTITION_EOF:
                raise RuntimeError("Достигнут конец партиции, сообщения не получены")
            raise RuntimeError(f"Ошибка Kafka consumer: {message.error()}")

        data = load_json_from_message(message.value())
        file_path = save_json_to_disk(data, args.output_dir)
        consumer.commit(message=message, asynchronous=False)
        return data, file_path
    finally:
        consumer.close()


def get_json_to_send(args: argparse.Namespace) -> Any:
    if args.send_json and args.send_json_file:
        raise ValueError("Укажите только один параметр: --send-json или --send-json-file")

    if args.send_json_file:
        return json.loads(Path(args.send_json_file).read_text(encoding="utf-8"))

    if args.send_json:
        return json.loads(args.send_json)

    return json.loads(Path(DEFAULT_SEND_JSON_FILE).read_text(encoding="utf-8"))


def delivery_report(error: Exception | None, message: Any) -> None:
    if error is not None:
        print(f"Не удалось отправить сообщение: {error}", file=sys.stderr)
        return

    print(
        "Сообщение отправлено: "
        f"topic={message.topic()} partition={message.partition()} offset={message.offset()}"
    )


def send_json(args: argparse.Namespace, data: Any) -> None:
    delivery_errors: list[Exception] = []

    def on_delivery(error: Exception | None, message: Any) -> None:
        if error is not None:
            delivery_errors.append(error)
        delivery_report(error, message)

    producer = Producer(kafka_config(args))
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

    producer.produce(
        topic=args.output_topic,
        value=payload,
        callback=on_delivery,
    )
    remaining_messages = producer.flush()

    if remaining_messages:
        raise RuntimeError(f"Не все сообщения отправлены, осталось: {remaining_messages}")
    if delivery_errors:
        raise RuntimeError(f"Kafka producer вернул ошибку: {delivery_errors[0]}")


def main() -> int:
    args = parse_args()

    try:
        incoming_json, saved_path = receive_one_json(args)
        print(f"Входящий JSON сохранен: {saved_path}")

        related_file_path, related_file_content = read_related_file(
            incoming_json,
            args.files_root,
        )
        print(
            f"Связанный файл получен: {related_file_path} "
            f"({len(related_file_content)} байт)"
        )
        validate_pdf_hashes(incoming_json, related_file_content)
        saved_pdf_paths = save_extracted_pdfs(
            related_file_content,
            args.output_dir,
            related_file_path.stem,
        )
        for pdf_path in saved_pdf_paths:
            print(f"PDF сохранен: {pdf_path}")
        extracted_values_path = save_extracted_values(
            incoming_json,
            related_file_content,
            args.output_dir,
            related_file_path.stem,
        )
        print(f"Значения XML сохранены: {extracted_values_path}")

        outgoing_json = get_json_to_send(args)
        send_json(args, outgoing_json)
        return 0
    except json.JSONDecodeError as exc:
        print(f"Некорректный JSON: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
