import os

from common import boot
from flask import Flask

from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware

app = Flask(__name__)
app.wsgi_app = HireFireMiddleware(app.wsgi_app)


@app.route("/")
def index():
    return f"pid={os.getpid()}"


boot()
application = app
