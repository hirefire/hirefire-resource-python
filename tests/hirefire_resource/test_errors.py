from hirefire_resource.errors import JobQueueLatencyUnsupportedError


def test_job_queue_latency_unsupported_error_names_the_adapter():
    error = JobQueueLatencyUnsupportedError("RQ")
    assert "RQ" in str(error)
    assert error.name == "RQ"
