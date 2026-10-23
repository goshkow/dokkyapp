from koddi.logs.service import log_event, search_logs


def test_log_search_and_secret_redaction() -> None:
    log_event("INFO", "test.logs", "token=visible-value", {"api_key": "hidden", "query": "needle"})
    records = search_logs("test.logs")
    record = next(item for item in records if item["event"] == "test.logs")
    assert "visible-value" not in record["message"]
    assert record["data"]["api_key"] == "[redacted]"
