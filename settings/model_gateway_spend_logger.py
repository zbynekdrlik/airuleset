"""airuleset model-gateway spend logger — a LiteLLM CustomLogger (#1062, Lane L1).

LiteLLM's `/spend/logs` + the `LiteLLM_SpendLogs` table require a Postgres
database (docs.litellm.ai/docs/proxy/cost_tracking), which the pilot deliberately
avoids ("DATABASE_URL is optional"). The documented DB-free way to a file-based
spend log is a success callback
(docs.litellm.ai/docs/observability/custom_callback): this module appends ONE
JSON line per successful request to ~/.config/airuleset/model-gateway-spend.jsonl,
which `airuleset.py model-gateway spend` parses into $ per alias per day.

Installed to ~/.config/airuleset/ by cli_model_gateway.setup_model_gateway_service
and wired via `litellm_settings.callbacks: model_gateway_spend_logger.spend_logger_instance`
in the rendered config.yaml; imported by litellm inside its own venv (so `import
litellm...` here is expected NOT to resolve in the airuleset stdlib environment).

Record shape (MUST match cli_model_gateway.aggregate_spend / load_spend_records):
    {"ts": "<ISO8601>", "alias": "<model_name>", "cost_usd": <float>,
     "prompt_tokens": <int>, "completion_tokens": <int>, "total_tokens": <int>}
"""
import datetime
import json
import os
import sys

from litellm.integrations.custom_logger import CustomLogger

SPEND_LOG_PATH = os.path.expanduser("~/.config/airuleset/model-gateway-spend.jsonl")


def _num(value, cast, default):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


class _ModelGatewaySpendLogger(CustomLogger):
    """Appends a JSONL spend record per successful request. Logging must NEVER
    break a request, so a write failure is reported to stderr and swallowed."""

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, end_time)

    def _record(self, kwargs, end_time):
        try:
            slo = kwargs.get("standard_logging_object") or {}
            # `model_group` is the requested model_name (the alias) per the
            # StandardLoggingPayload spec (docs.litellm.ai/docs/proxy/logging_spec);
            # fall back to the payload `model`, then the raw kwarg, so a spend row
            # is always labelled even if the payload shape shifts.
            rec = {
                "ts": self._ts(end_time),
                "alias": (slo.get("model_group") or slo.get("model")
                          or kwargs.get("model") or "?"),
                "cost_usd": _num(slo.get("response_cost",
                                         kwargs.get("response_cost")), float, 0.0),
                "prompt_tokens": _num(slo.get("prompt_tokens"), int, 0),
                "completion_tokens": _num(slo.get("completion_tokens"), int, 0),
                "total_tokens": _num(slo.get("total_tokens"), int, 0),
            }
            os.makedirs(os.path.dirname(SPEND_LOG_PATH), exist_ok=True)
            with open(SPEND_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception as e:
            print("model-gateway spend-logger: %s" % e, file=sys.stderr)

    @staticmethod
    def _ts(end_time):
        if isinstance(end_time, datetime.datetime):
            return end_time.isoformat()
        return datetime.datetime.now(datetime.timezone.utc).isoformat()


spend_logger_instance = _ModelGatewaySpendLogger()
