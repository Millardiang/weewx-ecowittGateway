"""Issue #11: with the gateway out of reach while WeeWX builds the engine, each failed build left a
collector thread polling, with an unbounded queue nothing read: memory and the request rate grew
until the process was killed. The collectors now start when WeeWX runs the engine, hardware_name
can't raise, and the queue is bounded."""

import queue
import socket
import threading
import time

import pytest


def collector_threads():
    return [t for t in threading.enumerate() if t.name == 'EcowittHttpCollectorThread' and t.is_alive()]


@pytest.fixture
def unreachable():
    """An address nothing answers on."""
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return {'ip_address': f'127.0.0.1:{port}', 'api': 'http', 'max_tries': '1', 'retry_wait': '0',
            'url_timeout': '1', 'poll_interval': '1', 'firmware_update_check_interval': '0',
            'catchup': {'source': 'none'}}


def test_building_the_driver_starts_no_collector(egw, make_driver, station):
    before = len(collector_threads())
    driver = make_driver(**station)
    time.sleep(0.3)
    assert len(collector_threads()) == before
    packets = driver.genLoopPackets()
    next(packets)                       # WeeWX runs the engine: now it polls
    assert len(collector_threads()) == before + 1
    driver.closePort()
    time.sleep(1.5)
    assert len(collector_threads()) == before


def test_hardware_name_does_not_raise_with_the_gateway_out_of_reach(egw, make_driver, unreachable):
    driver = make_driver(**unreachable)
    assert driver.hardware_name == egw.DRIVER_NAME


def test_hardware_name_is_the_model_once_read(egw, make_driver, station, gateway):
    driver = make_driver(**station)
    assert driver.hardware_name == 'GW2000'
    gateway.requests.clear()
    assert driver.hardware_name == 'GW2000'
    assert 'get_version' not in gateway.requests


def test_failed_engine_builds_leave_no_threads(egw, unreachable):
    """As weewxd does when building the engine raises: build again, without shutting down."""
    before = len(collector_threads())
    for _ in range(3):
        driver = egw.EcowittHttpDriver(**unreachable)
        _ = driver.hardware_name        # weewx.station.StationInfo reads it
        # ... a service fails to load; the engine is dropped, closePort() never called
    time.sleep(0.5)
    assert len(collector_threads()) == before


def test_the_collector_queue_is_bounded(egw):
    collector = egw.Collector()
    for n in range(collector.QUEUE_SIZE + 50):
        collector.put(n)
    assert collector.queue.qsize() == collector.QUEUE_SIZE
    assert collector.queue.get_nowait() == 50          # the oldest were dropped
