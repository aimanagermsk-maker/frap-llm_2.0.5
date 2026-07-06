# frap-llm-helper

Шаблон Python-сервиса 



## Конфигурация

### Базовые переменные (общие для всех контуров)

[`settings/application.yaml`](settings/application.yaml) — дефолты, которые дополняются профилем.



### Песок и другие контуры

[`settings/application-sandbox.yaml`](settings/application-sandbox.yaml) — полный конфиг песка (БД, logging).

Другие профили — [`settings/application-{profile}.yaml`](settings/) (`application-test.yaml`, `application-prod.yaml` и т.д.).
Справочник всех полей: [`settings/config.reference.yaml`](settings/config.reference.yaml).
User settings: [`settings/user_settings.yaml`](settings/user_settings.yaml).



### Override через environment (тестировщики / стенд)





## Профиль при запуске

Профиль задаётся **при запуске контейнера**

```bash
-e PYTHON_PROFILES_ACTIVE=sandbox
```

CI только собирает образ и триггерит деплой Center-Inform (`DEPLOY_IMAGE_TAG`, `SERVICE_NAME`).
На стенде профиль и override задаёт эксплуатация / тестировщики через **environment** в манифесте деплоя или `docker run`.


## Тестовое приложение (вывод в консоль)

При старте в лог пишется активный конфиг:

- [`app/main.py`](app/main.py) — точка входа, `lifespan` вызывает `log_app_config()`
- [`app/config/app_config.py`](app/config/app_config.py) — загрузка yaml, merge environment, вывод в stdout


Проверка через API:
- [`app/routers/hello_router.py`](app/routers/hello_router.py) — `GET /hello`


## Сборка и локальный запуск в Docker

[`local-deploy.sh`](local-deploy.sh):


Логи:

```bash
docker logs -f frap-llm-helper
```

## Kafka

**Чтение**
```bash
/opt/kafka/kafka/bin/kafka-console-consumer.sh --bootstrap-server gitlab-ci.ru:9092 --topic frap-llm-helper-in --from-beginning
```

```bash
/opt/kafka/kafka/bin/kafka-console-consumer.sh --bootstrap-server gitlab-ci.ru:9092 --topic frap-llm-helper-out --from-beginning
```

**Запись**
```bash
/opt/kafka/kafka/bin/kafka-console-producer.sh --broker-list gitlab-ci.ru:9092 --topic frap-llm-helper-in
```

```bash
/opt/kafka/kafka/bin/kafka-console-producer.sh --broker-list gitlab-ci.ru:9092 --topic frap-llm-helper-out
```
