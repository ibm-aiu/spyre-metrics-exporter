 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
import json
from pathlib import Path

import topology


def load_topo(filename):
    path = Path(__file__).resolve().parent / filename
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers that inspect prometheus_client registry after parse_spyre_topology
# ---------------------------------------------------------------------------

def _collected_samples(gauge):
    """Return all samples currently held by a Gauge as a list of Sample objects."""
    return list(gauge.collect()[0].samples)


# ---------------------------------------------------------------------------
# topo-aiu.json  (AIU Device names, metadata=null, 2 devices)
# ---------------------------------------------------------------------------

class TestParseTopoAIU:
    """Tests against src/tests/data/topo-aiu.json"""

    @classmethod
    def setup_class(cls):
        data = load_topo("data/topo-aiu.json")
        topology.info_gauge.clear()
        topology.metadata_gauge.clear()
        topology.connection_info_gauge.clear()
        topology.parse_spyre_topology(data["devices"])

    def test_device_count(self):
        """Both AIU devices must produce an info gauge sample."""
        addrs = {s.labels["addr"] for s in _collected_samples(topology.info_gauge)}
        assert addrs == {"0000:01:00.0", "0000:02:00.0"}

    def test_device_name_contains_aiu(self):
        """All device names must contain 'AIU'."""
        for s in _collected_samples(topology.info_gauge):
            assert "AIU" in s.labels["name"].upper()

    def test_null_metadata_falls_back_to_na(self):
        """With metadata=null every metadata label must be 'NA'."""
        for s in _collected_samples(topology.metadata_gauge):
            for key in ("SOC_Clock", "RPD_Clock", "Mem", "DDR_Speed",
                        "DRAM_Freq", "DDR_Avail", "Boost", "SerialID"):
                assert s.labels[key] == "NA", (
                    f"Expected NA for {key} on {s.labels['addr']}, got {s.labels[key]!r}"
                )

    def test_p2pdma_connections_present(self):
        """At least one P2PDMA connection must be recorded."""
        protocols = {s.labels["protocol"]
                     for s in _collected_samples(topology.connection_info_gauge)}
        assert "P2PDMA" in protocols

    def test_only_one_connection_recorded(self):
        """Two symmetric devices must be deduplicated to exactly 1 connection."""
        assert len(_collected_samples(topology.connection_info_gauge)) == 1


# ---------------------------------------------------------------------------
# mock/single-spyre-device-plugins/metadata/topo.json
# (SPYRE Device names, full metadata present, 8 devices)
# ---------------------------------------------------------------------------

MOCK_TOPO = (
    Path(__file__).resolve().parent.parent.parent
    / "mock" / "single-spyre-device-plugins" / "metadata" / "topo.json"
)


class TestParseTopoSpyre:
    """Tests against mock/single-spyre-device-plugins/metadata/topo.json"""

    @classmethod
    def setup_class(cls):
        with open(MOCK_TOPO) as f:
            data = json.load(f)
        topology.info_gauge.clear()
        topology.metadata_gauge.clear()
        topology.connection_info_gauge.clear()
        topology.parse_spyre_topology(data["devices"])

    def test_device_count(self):
        """All 8 SPYRE devices must produce an info gauge sample."""
        addrs = {s.labels["addr"] for s in _collected_samples(topology.info_gauge)}
        assert addrs == {
            "0000:01:00.0", "0000:02:00.0", "0000:03:00.0", "0000:04:00.0",
            "0000:05:00.0", "0000:06:00.0", "0000:07:00.0", "0000:08:00.0",
        }

    def test_device_name_contains_spyre(self):
        """All device names must contain 'SPYRE'."""
        for s in _collected_samples(topology.info_gauge):
            assert "SPYRE" in s.labels["name"].upper()

    def test_metadata_populated(self):
        """With full metadata, SOC_Clock and RPD_Clock must not be 'NA'."""
        for s in _collected_samples(topology.metadata_gauge):
            assert s.labels["SOC_Clock"] != "NA", (
                f"SOC_Clock was NA for {s.labels['addr']}"
            )
            assert s.labels["RPD_Clock"] != "NA", (
                f"RPD_Clock was NA for {s.labels['addr']}"
            )

    def test_serial_ids_distinct(self):
        """SerialID must be non-NA and unique per device (8 distinct values)."""
        serial_ids = {s.labels["SerialID"]
                      for s in _collected_samples(topology.metadata_gauge)}
        assert "NA" not in serial_ids
        assert len(serial_ids) == 8

    def test_p2pdma_connections_present(self):
        """At least one P2PDMA connection must be recorded."""
        protocols = {s.labels["protocol"]
                     for s in _collected_samples(topology.connection_info_gauge)}
        assert "P2PDMA" in protocols

    def test_connections_deduped(self):
        """Each (src, dst) pair must appear at most once across all 28 unique pairs."""
        pairs = [(s.labels["src_addr"], s.labels["dst_addr"])
                 for s in _collected_samples(topology.connection_info_gauge)]
        assert len(pairs) == len(set(pairs))
        assert len(pairs) == 28  # 8 * 7 / 2
