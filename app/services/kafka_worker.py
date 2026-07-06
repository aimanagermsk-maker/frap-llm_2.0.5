import json
import logging
import threading
from types import SimpleNamespace
from typing import Any

from confluent_kafka import Consumer, KafkaError

from app.config.app_config import KafkaConfig
from app.kafka_test import (
    kafka_config,
    load_json_from_message,
    read_related_file,
    save_extracted_pdfs,
    save_json_to_disk,
    send_json,
    validate_pdf_hashes,
)
from app.services.xml_value_extractor import save_extracted_values

logger = logging.getLogger(__name__)


class KafkaWorker:
    def __init__(self, config: KafkaConfig) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._run,
            name="frap-llm-helper-kafka-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Kafka worker started: bootstrap=%s input_topic=%s group_id=%s",
            self._config.bootstrap_servers,
            self._config.input_topic,
            self._config.group_id,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                logger.warning("Kafka worker did not stop within timeout")

    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(
            bootstrap_servers=self._config.bootstrap_servers,
            input_topic=self._config.input_topic,
            output_topic=self._config.output_topic,
            group_id=self._config.group_id,
            output_dir=self._config.output_dir,
            documents_home_dir=self._config.documents_home_dir,
            send_json=self._config.send_json,
            send_json_file=self._config.send_json_file,
        )

    def _run(self) -> None:
        args = self._args()
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
            while not self._stop_event.is_set():
                message = consumer.poll(1.0)
                if message is None:
                    continue

                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error("Kafka consumer error: %s", message.error())
                    continue

                try:
                    self._process_message(args, message.value())
                    consumer.commit(message=message, asynchronous=False)
                except Exception:
                    logger.exception("Kafka message processing failed")
        finally:
            consumer.close()
            logger.info("Kafka worker stopped")

    def _process_message(self, args: SimpleNamespace, raw_value: bytes | str | None) -> None:
        incoming_json = load_json_from_message(raw_value)
        saved_path = save_json_to_disk(incoming_json, args.output_dir)
        logger.info("Incoming Kafka JSON saved: %s", saved_path)

        related_file_path, related_file_content = read_related_file(
            incoming_json,
            args.documents_home_dir,
        )
        logger.info(
            "Related XML file loaded: %s (%s bytes)",
            related_file_path,
            len(related_file_content),
        )

        validate_pdf_hashes(incoming_json, related_file_content)
        saved_pdf_paths = save_extracted_pdfs(
            related_file_content,
            args.output_dir,
            related_file_path.stem,
        )
        for pdf_path in saved_pdf_paths:
            logger.info("PDF saved: %s", pdf_path)

        extracted_values_path = save_extracted_values(
            incoming_json,
            related_file_content,
            args.output_dir,
            related_file_path.stem,
        )
        logger.info("XML values saved: %s", extracted_values_path)

        outgoing_json = self._get_optional_json_to_send(args)
        if outgoing_json is not None:
            send_json(args, outgoing_json)

    @staticmethod
    def _get_optional_json_to_send(args: SimpleNamespace) -> Any | None:
        if args.send_json and args.send_json_file:
            raise ValueError("Use only one Kafka response source: send_json or send_json_file")

        if args.send_json_file:
            with open(args.send_json_file, encoding="utf-8") as file:
                return json.load(file)

        if args.send_json:
            return json.loads(args.send_json)

        return None
