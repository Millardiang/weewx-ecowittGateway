"""Shared test set-up for weewx-ecowittGateway.

Run from the repository root, in an environment with WeeWX 5.x, configobj and pytest installed:

    python3 -m pytest

(pytest.ini runs these tests and Gary Roderick's unittest suite in bin/user/tests.)

The fake gateway serves responses from a JSON file in the format written by
'weectl device --dump-api', so a dump from a real gateway can be added to tests/fixtures and used
by the same tests. FakeGw1000 answers the GW1000's binary TCP API with the same readings.
"""

import copy
import importlib
import json
import logging
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')
for path in (os.path.join(ROOT, 'bin'), ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import weewx  # noqa: E402

egw_module = importlib.import_module('user.weewx-ecowittGateway')


def load_dump(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return json.load(f)


class FakeGateway:
    """A local HTTP server answering Ecowitt API commands from a --dump-api style dict.

    It also serves SD card history files (port 81 on a real gateway) from 'files', so one server
    stands in for both."""

    def __init__(self, responses, files=None):
        self.responses = copy.deepcopy(responses)
        self.files = dict(files or {})
        self.requests = []
        self.down = False
        self.delay = 0                  # seconds to wait before answering
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                command = url.path.strip('/')
                page = urllib.parse.parse_qs(url.query).get('page', [None])[0]
                gateway.requests.append(command)
                if gateway.delay:
                    time.sleep(gateway.delay)
                key = f'{command}?page={page}' if page else command
                if not gateway.down and command in gateway.files:
                    self.reply(gateway.files[command].encode(), 'text/csv')
                elif gateway.down or key not in gateway.responses:
                    self.send_response(404)
                    self.end_headers()
                else:
                    self.reply(json.dumps(gateway.responses[key]).encode(), 'application/json')

            def reply(self, body, content_type):
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    @property
    def address(self):
        return f'127.0.0.1:{self.port}'

    def live(self, group, item_id=None):
        """The live data entry for group (and common_list style id), to change between polls."""
        data = self.responses['get_livedata_info'][group]
        if not isinstance(data, list):
            return data
        if item_id is None:
            return data[0]
        return next(item for item in data if item.get('id') == item_id)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _be(value, size):
    return int(value).to_bytes(size, 'big', signed=value < 0)


class FakeGw1000:
    """A GW1000 answering its binary TCP API on 127.0.0.1, with the readings of gw2000_ws90.json."""

    LIVE = b''.join(bytes([c]) + v for c, v in [
        (0x01, _be(214, 2)), (0x06, _be(45, 1)), (0x08, _be(10012, 2)), (0x09, _be(10153, 2)),
        (0x02, _be(123, 2)), (0x07, _be(81, 1)), (0x0A, _be(231, 2)), (0x0B, _be(32, 2)), (0x0C, _be(51, 2)),
        (0x19, _be(89, 2)), (0x15, _be(272912, 4)), (0x16, _be(25, 2)), (0x17, _be(2, 1)),
        (0x0D, _be(12, 2)), (0x0E, _be(0, 2)), (0x10, _be(12, 2)), (0x11, _be(54, 2)), (0x12, _be(201, 4)),
        (0x13, _be(4506, 4)),
        (0x1A, _be(198, 2)), (0x22, _be(52, 1)), (0x2E, _be(33, 1)), (0x2A, _be(60, 2)), (0x4D, _be(220, 2)),
        (0x60, _be(12, 1)), (0x61, _be(1793887921, 4)), (0x62, _be(3, 4)),
    ])

    @staticmethod
    def sensor(addr, sid, batt, sig):
        return bytes([addr]) + _be(sid, 4) + bytes([batt, sig])

    LONG = (0x27, 0x3C)

    def __init__(self):
        self.data = {0x50: bytes([14]) + b'GW1000B_V1.7.7', 0x26: bytes.fromhex('AABBCCDDEEFF'),
                     0x27: self.LIVE,
                     # WS90 (address 48), WN31 CH1, WH51 CH2, WH41 CH1; no WH40
                     0x3C: (self.sensor(48, 0xD5A2, 161, 4) + self.sensor(6, 0x5E, 0, 4)
                            + self.sensor(15, 0xC0FFEE, 14, 2) + self.sensor(22, 0xC3, 5, 4)
                            + self.sensor(3, 0xFFFFFFFE, 0, 0))}
        self.requests = []
        self.down = False
        gw = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                req = self.request.recv(64)
                if gw.down or len(req) < 5 or req[:2] != b'\xff\xff' or req[2] not in gw.data:
                    return
                cmd, data = req[2], gw.data[req[2]]
                gw.requests.append(cmd)
                size_len = 2 if cmd in gw.LONG else 1
                body = bytes([cmd]) + (size_len + 1 + len(data) + 1).to_bytes(size_len, 'big') + data
                self.request.sendall(b'\xff\xff' + body + bytes([sum(body) & 0xFF]))

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeBroker:
    """Just enough of an MQTT 3.1.1 broker to accept one client and record what it publishes."""

    def __init__(self):
        self.published = []
        self.connected = threading.Event()
        broker = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                sock = self.request
                sock.settimeout(10)
                try:
                    while True:
                        head = self.read(1)
                        length, mult = 0, 1
                        while True:
                            byte = self.read(1)[0]
                            length += (byte & 0x7F) * mult
                            mult *= 128
                            if not byte & 0x80:
                                break
                        body = self.read(length)
                        kind = head[0] >> 4
                        if kind == 1:                       # CONNECT
                            sock.sendall(b'\x20\x02\x00\x00')
                            broker.connected.set()
                        elif kind == 3:                     # PUBLISH
                            tlen = int.from_bytes(body[:2], 'big')
                            topic = body[2:2 + tlen].decode()
                            rest = body[2 + tlen:]
                            qos = (head[0] >> 1) & 3
                            if qos:
                                packet_id, rest = rest[:2], rest[2:]
                                sock.sendall(b'\x40\x02' + packet_id)
                            broker.published.append((topic, rest.decode()))
                        elif kind == 12:                    # PINGREQ
                            sock.sendall(b'\xd0\x00')
                        elif kind == 14:                    # DISCONNECT
                            return
                except (OSError, IndexError):
                    return

            def read(self, n):
                data = b''
                while len(data) < n:
                    chunk = self.request.recv(n - len(data))
                    if not chunk:
                        raise OSError('closed')
                    data += chunk
                return data

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def egw():
    """The driver module, with WeeWX debug off and units defined."""
    weewx.debug = 0
    egw_module.define_units()
    yield egw_module
    weewx.debug = 0


@pytest.fixture
def gateway():
    gw = FakeGateway(load_dump('gw2000_ws90.json'))
    yield gw
    gw.close()


@pytest.fixture
def gw1000():
    gw = FakeGw1000()
    yield gw
    gw.close()


@pytest.fixture
def station(gateway):
    """Driver configuration ([EcowittGateway]) pointing at the fake gateway."""
    return {'ip_address': gateway.address, 'api': 'http', 'poll_interval': '1', 'max_tries': '1',
            'retry_wait': '0', 'url_timeout': '2', 'firmware_update_check_interval': '0',
            'catchup': {'source': 'none'}}


@pytest.fixture
def make_driver(egw):
    """Make drivers (EcowittHttpDriver(**config)); they are closed after the test."""
    drivers = []

    def make(**config):
        drv = egw.EcowittHttpDriver(**config)
        drivers.append(drv)
        return drv
    yield make
    for drv in drivers:
        drv.closePort()


@pytest.fixture
def driver(make_driver, station):
    return make_driver(**station)


@pytest.fixture
def info_log(caplog):
    caplog.set_level(logging.INFO)
    return caplog
