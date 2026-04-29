from __future__ import annotations

import unittest

from vynex_vpn_client.config_builder import XrayConfigBuilder
from vynex_vpn_client.constants import LOCAL_PROXY_HOST
from vynex_vpn_client.models import LocalProxyCredentials, ServerEntry
from vynex_vpn_client.routing_profiles import RoutingProfile


class XrayConfigBuilderTests(unittest.TestCase):
    def test_proxy_inbounds_bind_only_to_loopback(self) -> None:
        builder = XrayConfigBuilder()
        config = builder.build(
            server=ServerEntry.new(
                name="test",
                protocol="vless",
                host="example.com",
                port=443,
                raw_link="vless://test@example.com:443",
                extra={"id": "11111111-1111-1111-1111-111111111111"},
            ),
            mode="proxy",
            routing_profile=RoutingProfile(
                profile_id="test",
                name="test",
                description="test",
                rules=[],
            ),
            socks_port=1080,
            http_port=8080,
            socks_credentials=LocalProxyCredentials(username="user", password="pass"),
        )

        self.assertEqual(len(config["inbounds"]), 2)
        self.assertTrue(all(inbound["listen"] == LOCAL_PROXY_HOST for inbound in config["inbounds"]))

    def test_build_supports_trojan_outbound(self) -> None:
        builder = XrayConfigBuilder()
        config = builder.build(
            server=ServerEntry.new(
                name="trojan",
                protocol="trojan",
                host="trojan.example.com",
                port=443,
                raw_link="trojan://secret@trojan.example.com:443?type=ws&security=tls&host=cdn.example.com&path=%2Fws&sni=edge.example.com#trojan",
                extra={
                    "password": "secret",
                    "network": "ws",
                    "security": "tls",
                    "host": "cdn.example.com",
                    "path": "/ws",
                    "sni": "edge.example.com",
                },
            ),
            mode="proxy",
            routing_profile=RoutingProfile(
                profile_id="test",
                name="test",
                description="test",
                rules=[],
            ),
            socks_port=1080,
            http_port=8080,
            socks_credentials=LocalProxyCredentials(username="user", password="pass"),
        )

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["protocol"], "trojan")
        self.assertEqual(outbound["settings"]["servers"][0]["password"], "secret")
        self.assertEqual(outbound["streamSettings"]["security"], "tls")
        self.assertEqual(outbound["streamSettings"]["tlsSettings"]["serverName"], "edge.example.com")
        self.assertEqual(outbound["streamSettings"]["wsSettings"]["path"], "/ws")
        self.assertEqual(outbound["streamSettings"]["wsSettings"]["headers"]["Host"], "cdn.example.com")

    def test_tun_config_uses_xray_tun_inbound_and_xray_routes(self) -> None:
        builder = XrayConfigBuilder()
        config = builder.build(
            server=ServerEntry.new(
                name="test",
                protocol="vless",
                host="example.com",
                port=443,
                raw_link="vless://test@example.com:443",
                extra={"id": "11111111-1111-1111-1111-111111111111"},
            ),
            mode="tun",
            routing_profile=RoutingProfile(
                profile_id="default",
                name="default",
                description="default",
                rules=[
                    {
                        "type": "field",
                        "ip": ["geoip:private"],
                        "outboundTag": "direct",
                    }
                ],
            ),
            outbound_interface_name="Ethernet",
        )

        self.assertEqual(config["inbounds"][0]["protocol"], "tun")
        self.assertEqual(config["inbounds"][0]["settings"]["name"], builder.TUN_INTERFACE_NAME)
        self.assertEqual(config["inbounds"][0]["settings"]["MTU"], builder.TUN_MTU)
        self.assertEqual(config["inbounds"][0]["sniffing"]["destOverride"], ["http", "tls", "quic"])
        self.assertEqual(len(config["inbounds"]), 1)
        self.assertEqual(config["outbounds"][0]["protocol"], "vless")
        self.assertEqual(config["outbounds"][0]["streamSettings"]["sockopt"]["interface"], "Ethernet")
        self.assertEqual(config["outbounds"][2]["protocol"], "freedom")
        self.assertEqual(config["outbounds"][2]["streamSettings"]["sockopt"]["interface"], "Ethernet")
        self.assertEqual(config["routing"]["rules"][0]["process"], ["self/", "xray/"])
        self.assertEqual(config["routing"]["rules"][1]["ip"], ["geoip:private"])
        self.assertEqual(config["routing"]["rules"][-1]["outboundTag"], "proxy")

    def test_tun_config_requires_outbound_interface(self) -> None:
        builder = XrayConfigBuilder()

        with self.assertRaisesRegex(ValueError, "активный сетевой интерфейс"):
            builder.build(
                server=ServerEntry.new(
                    name="test",
                    protocol="vless",
                    host="example.com",
                    port=443,
                    raw_link="vless://test@example.com:443",
                    extra={"id": "11111111-1111-1111-1111-111111111111"},
                ),
                mode="tun",
                routing_profile=RoutingProfile(
                    profile_id="default",
                    name="default",
                    description="default",
                    rules=[],
                ),
            )

    def test_tun_config_supports_trojan_server(self) -> None:
        builder = XrayConfigBuilder()
        config = builder.build(
            server=ServerEntry.new(
                name="trojan",
                protocol="trojan",
                host="trojan.example.com",
                port=443,
                raw_link="trojan://secret@trojan.example.com:443?type=ws&security=tls&host=cdn.example.com&path=%2Fws&sni=edge.example.com#trojan",
                extra={
                    "password": "secret",
                    "network": "ws",
                    "security": "tls",
                    "host": "cdn.example.com",
                    "path": "/ws",
                    "sni": "edge.example.com",
                },
            ),
            mode="tun",
            routing_profile=RoutingProfile(
                profile_id="default",
                name="default",
                description="default",
                rules=[],
            ),
            outbound_interface_name="Wi-Fi",
        )

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["protocol"], "trojan")
        self.assertEqual(outbound["settings"]["servers"][0]["password"], "secret")
        self.assertEqual(outbound["streamSettings"]["sockopt"]["interface"], "Wi-Fi")
        self.assertEqual(outbound["streamSettings"]["security"], "tls")
        self.assertEqual(outbound["streamSettings"]["tlsSettings"]["serverName"], "edge.example.com")
        self.assertEqual(outbound["streamSettings"]["wsSettings"]["path"], "/ws")
        self.assertEqual(outbound["streamSettings"]["wsSettings"]["headers"]["Host"], "cdn.example.com")

    def test_tun_config_supports_reality_xhttp_server(self) -> None:
        builder = XrayConfigBuilder()
        config = builder.build(
            server=ServerEntry.new(
                name="Reality XHTTP",
                protocol="vless",
                host="185.80.91.169",
                port=443,
                raw_link="vless://test@example.com:443?type=xhttp",
                extra={
                    "id": "11111111-1111-1111-1111-111111111111",
                    "network": "xhttp",
                    "security": "reality",
                    "public_key": "REALITYPUB",
                    "short_id": "abcd1234",
                    "fingerprint": "chrome",
                    "sni": "reality.example.com",
                    "host": "edge.example.com",
                    "path": "/xhttp",
                    "mode": "packet-up",
                    "xhttp_extra": {"xmux": {"maxConcurrency": "1-2"}},
                },
            ),
            mode="tun",
            routing_profile=RoutingProfile(
                profile_id="default",
                name="default",
                description="default",
                rules=[],
            ),
            outbound_interface_name="Wi-Fi",
        )

        stream_settings = config["outbounds"][0]["streamSettings"]
        self.assertEqual(stream_settings["network"], "xhttp")
        self.assertEqual(stream_settings["security"], "reality")
        self.assertEqual(stream_settings["realitySettings"]["serverName"], "reality.example.com")
        self.assertEqual(stream_settings["realitySettings"]["publicKey"], "REALITYPUB")
        self.assertEqual(stream_settings["xhttpSettings"]["host"], "edge.example.com")
        self.assertEqual(stream_settings["xhttpSettings"]["path"], "/xhttp")
        self.assertEqual(stream_settings["xhttpSettings"]["mode"], "packet-up")
        self.assertEqual(stream_settings["xhttpSettings"]["extra"], {"xmux": {"maxConcurrency": "1-2"}})
        self.assertEqual(stream_settings["sockopt"]["interface"], "Wi-Fi")


if __name__ == "__main__":
    unittest.main()
