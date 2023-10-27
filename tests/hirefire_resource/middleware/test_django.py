from unittest.mock import Mock

from django.http import HttpResponse

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.django import Middleware


def mock_get_response(request):
    return HttpResponse("Default response")


def create_custom_request(path="/", method="GET", META=None):
    request = Mock()
    request.path = path
    request.method = method
    request.META = META or {}
    return request


def test_something():
    Resource.configuration = Configuration().dyno("web")
    middleware = Middleware(mock_get_response)
    request = create_custom_request()
    response = middleware(request)
    print(response)
