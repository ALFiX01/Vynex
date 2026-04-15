from __future__ import annotations

import unittest

from vynex_vpn_client.config_builder import XrayConfigBuilder
from vynex_vpn_client.constants import LOCAL_PROXY_HOST
from vynex_vpn_client.models import LocalProxyCredentials, ServerEntry
from vynex_vpn_client.routing_profiles import RoutingProfile
from vynex_vpn_client.singbox_config_builder import SingboxConfigBuilder


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


class SingboxConfigBuilderTests(unittest.TestCase):
    def test_tun_config_routes_into_local_socks_backend(self) -> None:
        builder = SingboxConfigBuilder()
        config = builder.build_tun(
            socks_port=1080,
            socks_credentials=LocalProxyCredentials(username="user", password="pass"),
        )

        self.assertEqual(config["inbounds"][0]["type"], "tun")
        self.assertEqual(config["inbounds"][0]["interface_name"], builder.TUN_INTERFACE_NAME)
        self.assertEqual(len(config["inbounds"]), 1)
        self.assertEqual(config["outbounds"][0]["type"], "socks")
        self.assertEqual(config["outbounds"][0]["server"], LOCAL_PROXY_HOST)
        self.assertEqual(config["outbounds"][0]["server_port"], 1080)
        self.assertEqual(config["outbounds"][0]["username"], "user")
        self.assertEqual(config["route"]["final"], "proxy")

    def test_tun_config_supports_trojan_server(self) -> None:
        builder = SingboxConfigBuilder()
        config = builder.build_tun(
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
        )

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["type"], "trojan")
        self.assertEqual(outbound["password"], "secret")
        self.assertTrue(outbound["tls"]["enabled"])
        self.assertEqual(outbound["tls"]["server_name"], "edge.example.com")
        self.assertEqual(outbound["transport"]["type"], "ws")
        self.assertEqual(outbound["transport"]["path"], "/ws")
        self.assertEqual(outbound["transport"]["headers"]["Host"], "cdn.example.com")
        self.assertEqual(config["route"]["final"], "direct")


if __name__ == "__main__":
    unittest.main()
