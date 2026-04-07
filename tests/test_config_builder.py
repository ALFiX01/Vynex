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


if __name__ == "__main__":
    unittest.main()
