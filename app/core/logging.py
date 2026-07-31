import logging
import sys

import structlog

from app.core.config import settings

# DATA-2: these keys never reach a log line, regardless of where they enter the event dict.
_DENYLIST_KEYS = {
    "body",
    "text",
    "message",
    "message_body",
    "transcript",
    "transcription",
    "email_subject",
    "email_body",
    "email_content",
    "ocr_text",
    "ocr_output",
    "latitude",
    "longitude",
    "coordinates",
    "location",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "auth_token",
    "password",
    "patient_name",
}
_MAX_FIELD_LEN = 200


def _redact_processor(logger, method_name, event_dict):
    for key in list(event_dict.keys()):
        if key.lower() in _DENYLIST_KEYS:
            event_dict[key] = "[REDACTED]"
            continue
        value = event_dict[key]
        if isinstance(value, str) and len(value) > _MAX_FIELD_LEN:
            event_dict[key] = value[:_MAX_FIELD_LEN] + "...[TRUNCATED]"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level.upper()]
        ),
        context_class=dict,
        # PrintLoggerFactory writes via bare print() - stdout is block-buffered
        # (not line-buffered) once it's not a tty, e.g. piped to a deploy
        # platform's log collector, so lines can sit unflushed indefinitely.
        # stdlib LoggerFactory routes through the same handler configured
        # above via logging.basicConfig, whose StreamHandler flushes on every
        # record - the same path third-party libs (httpx/Twilio/APScheduler)
        # already use and that reliably shows up in logs.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(*args, **kwargs):
    return structlog.get_logger(*args, **kwargs)
