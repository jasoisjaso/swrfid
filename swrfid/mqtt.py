"""
swrfid.mqtt — Bridge tag reads to an MQTT broker for Home Assistant /
Node-RED / general IoT integration.

Requires the optional ``paho-mqtt`` dependency::

    pip install swrfid[mqtt]
    # or: pip install paho-mqtt

Usage as a daemon::

    swrfid-mqtt --port /dev/ttyUSB0 \\
                --broker mqtt://homeassistant.local:1883 \\
                --topic rfid/swrfid \\
                --dedup 2.0

Each tag read publishes a JSON message to ``<topic>/tags``:

    {"epc": "E20012...22", "scheme": "sgtin-96", "antenna": 2,
     "rssi": 73, "timestamp": "2026-05-15T01:34:22.123Z"}

If ``--ha-discovery`` is passed, a Home Assistant MQTT-discovery
configuration is also published so HA auto-creates a sensor entity.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from . import __version__
from .epc import describe as epc_describe
from .ports import find_readers
from .reader import RFIDReader, ReaderError


logger = logging.getLogger(__name__)

DEFAULT_BROKER = 'mqtt://localhost:1883'
DEFAULT_TOPIC = 'rfid/swrfid'


def _require_paho():
    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "paho-mqtt is not installed.\n"
            "Install with: pip install swrfid[mqtt]   or: pip install paho-mqtt\n"
        )
        sys.exit(1)


class MqttBridge:
    """MQTT bridge that connects a reader to a broker.

    Designed to run for long periods. Recovers automatically from broker
    disconnections via paho-mqtt's built-in reconnect loop.
    """

    def __init__(
        self,
        port: str,
        broker_url: str = DEFAULT_BROKER,
        topic_prefix: str = DEFAULT_TOPIC,
        dedup_window: float = 0.0,
        rssi_min: int = 0,
        ha_discovery: bool = False,
        client_id: Optional[str] = None,
    ) -> None:
        _require_paho()
        import paho.mqtt.client as mqtt

        self.port = port
        self.topic_prefix = topic_prefix.rstrip('/')
        self.dedup_window = dedup_window
        self.rssi_min = rssi_min
        self.ha_discovery = ha_discovery
        self._stop = threading.Event()
        self._reader: Optional[RFIDReader] = None

        u = urlparse(broker_url if '://' in broker_url else 'mqtt://' + broker_url)
        self.broker_host = u.hostname or 'localhost'
        self.broker_port = u.port or (8883 if u.scheme == 'mqtts' else 1883)
        self.use_tls = (u.scheme == 'mqtts')
        self.broker_user = u.username
        self.broker_pass = u.password

        cid = client_id or ('swrfid-%s' % int(time.time()))
        self._client = mqtt.Client(client_id=cid)
        if self.broker_user:
            self._client.username_pw_set(self.broker_user, self.broker_pass)
        if self.use_tls:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info('MQTT connected to %s:%d', self.broker_host, self.broker_port)
            client.publish(self.topic_prefix + '/status', 'online', retain=True)
            if self.ha_discovery:
                self._publish_ha_discovery()
        else:
            logger.warning('MQTT connect rc=%d', rc)

    def _on_disconnect(self, client, userdata, rc):
        logger.warning('MQTT disconnected rc=%d', rc)

    def _publish_ha_discovery(self):
        # Home Assistant MQTT discovery — adds an "swrfid Last Tag" sensor.
        node_id = self.topic_prefix.replace('/', '_')
        config_topic = 'homeassistant/sensor/%s/last_tag/config' % node_id
        payload = {
            'name': 'swrfid Last Tag',
            'state_topic': self.topic_prefix + '/tags',
            'value_template': '{{ value_json.epc }}',
            'json_attributes_topic': self.topic_prefix + '/tags',
            'unique_id': '%s_last_tag' % node_id,
            'device': {
                'identifiers': [node_id],
                'name': 'swrfid bridge',
                'model': 'SW UHF RFID',
                'manufacturer': 'swrfid v%s' % __version__,
            },
        }
        self._client.publish(config_topic, json.dumps(payload), retain=True)

    def _on_tags(self, dev_sn, tags):
        ts = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        for t in tags:
            if t.rssi < self.rssi_min:
                continue
            desc = epc_describe(t.epc)
            msg = {
                'epc': t.epc_hex,
                'scheme': desc.scheme,
                'antenna': t.antenna,
                'rssi': t.rssi,
                'timestamp': ts,
                'reader_sn': dev_sn.hex().upper(),
            }
            if desc.sgtin is not None:
                msg['gtin14'] = desc.sgtin.gtin14
                msg['serial'] = desc.sgtin.serial
            self._client.publish(
                self.topic_prefix + '/tags', json.dumps(msg),
            )

    def run(self):
        self._reader = RFIDReader(self.port)
        self._reader.open()
        self._client.will_set(self.topic_prefix + '/status', 'offline', retain=True)
        self._client.connect(self.broker_host, self.broker_port, keepalive=60)
        self._client.loop_start()

        try:
            self._reader.start_active_listener(
                self._on_tags,
                dedup_window=self.dedup_window,
                rssi_min=self.rssi_min,
            )
            self._reader.start_read()
            logger.info('Bridge running. Ctrl-C to stop.')
            while not self._stop.is_set():
                self._stop.wait(timeout=1.0)
        finally:
            try:
                self._reader.stop_read()
            except Exception:
                pass
            self._reader.stop_active_listener()
            self._reader.close()
            self._client.publish(self.topic_prefix + '/status', 'offline', retain=True)
            self._client.loop_stop()
            self._client.disconnect()

    def stop(self):
        self._stop.set()


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog='swrfid-mqtt',
        description='Bridge SW UHF RFID tags to an MQTT broker.',
    )
    p.add_argument('--port', help='Serial port; auto-detected if omitted.')
    p.add_argument('--broker', default=DEFAULT_BROKER,
                   help='MQTT broker URL, e.g. mqtt://host:1883 or mqtts://host:8883')
    p.add_argument('--topic', default=DEFAULT_TOPIC,
                   help='Topic prefix; tags are published to <prefix>/tags')
    p.add_argument('--dedup', type=float, default=2.0,
                   help='Dedup window in seconds (0 = no dedup)')
    p.add_argument('--rssi-min', type=lambda s: int(s, 0), default=0,
                   help='Drop tag reads with RSSI byte below this (0 = no filter)')
    p.add_argument('--ha-discovery', action='store_true',
                   help='Publish a Home Assistant MQTT-discovery config')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    port = args.port
    if port is None:
        found = find_readers()
        if not found:
            sys.stderr.write('No reader auto-detected. Use --port.\n')
            return 2
        port = found[0]
        logger.info('Auto-detected reader on %s', port)

    bridge = MqttBridge(
        port=port,
        broker_url=args.broker,
        topic_prefix=args.topic,
        dedup_window=args.dedup,
        rssi_min=args.rssi_min,
        ha_discovery=args.ha_discovery,
    )

    def _handle_sigterm(signum, frame):
        logger.info('Received signal %d, shutting down...', signum)
        bridge.stop()

    signal.signal(signal.SIGINT, _handle_sigterm)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        bridge.run()
    except ReaderError as exc:
        sys.stderr.write('Reader error: %s\n' % exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
