"""#1308: the filedrop never binds on, or advertises, the Dante audio VLAN
(10.77.7.0/24, VLAN 70). dev1's Dante leg had TCP 8788-8807 listening there;
the Dante leg carries only Dante/PTP UDP, mDNS and ICMP."""
import unittest
from unittest import mock

import filedrop


class TestDanteLeg(unittest.TestCase):

    def test_interface_path_drops_the_dante_leg(self):
        pairs = [("100.104.8.125", "tailscale0"), ("10.77.9.195", "enp38s0"),
                 ("10.77.7.112", "enp38s0.70")]
        with mock.patch.object(filedrop, "_iface_ips", return_value=pairs):
            ips = filedrop.bind_ips()
        self.assertNotIn("10.77.7.112", ips)
        self.assertEqual(ips, ["100.104.8.125", "10.77.9.195"])

    def test_old_usb_nic_leg_is_dropped_too(self):
        pairs = [("10.77.9.195", "enp38s0"), ("10.77.7.112", "enx00e04c680001")]
        with mock.patch.object(filedrop, "_iface_ips", return_value=pairs):
            self.assertEqual(filedrop.bind_ips(), ["10.77.9.195"])

    def test_cidr_fallback_drops_the_dante_leg(self):
        with mock.patch.object(filedrop, "_iface_ips", return_value=[]), \
                mock.patch.object(filedrop, "_ordered_ips",
                                  return_value=["10.77.7.112", "10.77.9.195"]):
            self.assertEqual(filedrop.bind_ips(), ["10.77.9.195"])

    def test_neighbour_subnets_stay(self):
        for ip in ("10.77.6.255", "10.77.8.1", "10.77.9.195"):
            self.assertTrue(filedrop._is_private(ip), ip)
        self.assertFalse(filedrop._is_private("10.77.7.1"))


if __name__ == "__main__":
    unittest.main()
