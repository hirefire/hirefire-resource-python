import json
from unittest.mock import MagicMock, patch

from autoscale_agent.util import dispatch
from tests.helpers import TOKEN


@patch("http.client.HTTPSConnection")
def test_dispatch(mock_conn):
    body = json.dumps({"metric": "worker", "value": 80})
    mock_response = MagicMock()
    mock_response.status = 200
    mock_conn.return_value.getresponse.return_value = mock_response
    response = dispatch(body, TOKEN)
    mock_conn.return_value.request.assert_called_once_with(
        "POST",
        "/",
        body='{"metric": "worker", "value": 80}'.encode("utf-8"),
        headers={
            "User-Agent": "Autoscale Agent (Python)",
            "Content-Type": "application/json",
            "Autoscale-Metric-Token": TOKEN,
        },
    )
    assert response.status == 200


@patch("http.client.HTTPSConnection")
def test_dispatch_unsuccessful(mock_conn):
    body = json.dumps({"metric": "worker", "value": 80})
    mock_response = MagicMock()
    mock_response.status = 400
    mock_conn.return_value.getresponse.return_value = mock_response
    response = dispatch(body, TOKEN)
    assert response.status == 400
