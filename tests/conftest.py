"""Shared test set-up for weewx-EcowittGateway.

Run from the repository root, in an environment with WeeWX 5.4 or later and pytest installed:

    python3 -m pytest tests

The fake gateway serves responses from a JSON file in the format written by
'weectl device --dump-api', so a dump from a real gateway can be added to tests/fixtures and used
by the same tests.
"""

import copy
import importlib
import json
import logging
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')
sys.path.insert(0, os.path.join(ROOT, 'bin', 'user'))
sys.path.insert(0, ROOT)

import weewx  # noqa: E402

egw_module = importlib.import_module('weewx-EcowittGateway')


def load_dump(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return json.load(f)


class FakeGateway:
    """A local HTTP server answering Ecowitt API commands from a --dump-api style dict."""

    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.requests = []
        self.down = False
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                command = url.path.strip('/')
                page = urllib.parse.parse_qs(url.query).get('page', [None])[0]
                gateway.requests.append(command)
                key = f'{command}?page={page}' if page else command
                if gateway.down or key not in gateway.responses:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(gateway.responses[key]).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self):
        return f'127.0.0.1:{self.server.server_address[1]}'

    def live(self, group, item_id=None):
        """The live data entry for group (and common_list style id), to change between polls."""
        data = self.responses['get_livedata_info'][group]
        if item_id is None:
            return data[0]
        return next(item for item in data if item.get('id') == item_id)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def egw():
    """The driver module, with WeeWX debug off and units defined."""
    weewx.debug = 0
    egw_module.define_units()
    return egw_module


@pytest.fixture
def gateway():
    gw = FakeGateway(load_dump('gw2000_ws90.json'))
    yield gw
    gw.close()


@pytest.fixture
def station(gateway, tmp_path):
    """Driver configuration ([EcowittGateway]) pointing at the fake gateway."""
    return {'ip_address': gateway.address, 'api': 'http', 'poll_interval': '1', 'max_tries': '1',
            'retry_wait': '0', 'url_timeout': '2', 'firmware_update_check_interval': '0',
            'catchup': {'source': 'none'}}


@pytest.fixture
def info_log(caplog):
    caplog.set_level(logging.INFO)
    return caplog
