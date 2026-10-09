from app.services.result_normalizer import normalize_result


def test_normalizes_success_and_intentional_skip():
    assert normalize_result({"status": "success"}).state == "success"
    assert normalize_result(503).retryable is True
    assert normalize_result(409).state == "success"
    skipped = normalize_result({"status": "skipped", "reason": "Unsupported live playback"})
    assert (skipped.state, skipped.category, skipped.retryable) == ("skipped", "intentional_skip", False)


def test_normalizes_nested_idempotent_conflict_as_success():
    normalized = normalize_result({"status": "error", "code": 409, "detail": "already recorded"})
    assert normalized.state == "success"
    assert normalized.retryable is False


def test_normalizes_transient_http_and_network_errors():
    for result in (
        {"status": 429, "error": "rate limited"},
        {"status": "error", "code": 503},
        {"status": "error", "error": "HTTP 503 Service Unavailable"},
        {"status": "error", "error": "network timeout"},
        {"status": "slow_down"},
    ):
        normalized = normalize_result(result)
        assert (normalized.state, normalized.category, normalized.retryable) == ("failed", "transient", True)


def test_normalizes_permanent_and_authorization_errors():
    unauthorized = normalize_result({"status": "error", "code": 401, "detail": "private response"})
    assert (unauthorized.state, unauthorized.category, unauthorized.retryable) == (
        "failed", "authorization_or_configuration", False
    )
    assert normalize_result({"status": 404, "error": "Not found"}).category == "permanent"


def test_nested_delivery_aggregates_errors_before_successes():
    result = normalize_result({"trakt": {"status": "success"}, "simkl": {"status": "error", "code": 503}})
    assert (result.state, result.category, result.retryable) == ("failed", "transient", True)
