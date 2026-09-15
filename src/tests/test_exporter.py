 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub `spyremetrics` before any project import touches it
# ---------------------------------------------------------------------------
if "spyremetrics" not in sys.modules:
    _stub = types.ModuleType("spyremetrics")
    class _FakeMetricFile:
        is_old_format = False
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read_metrics(self): return []
    _stub.MetricFile = _FakeMetricFile
    sys.modules["spyremetrics"] = _stub

import exporter
import spyremetrics_collector as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOPO_DATA = {
    "devices": {
        "0000:01:00.0": {
            "name": "SPYRE Device 06a7 (rev 02)",
            "numanode": 0,
            "linkspeed": "63.02 GB/s",
            "protocol": {"0000:02:00.0": ["P2PDMA"]},
            "metadata": None,
        }
    }
}

SENLIB_CONFIG = {
    "GENERAL": {"sen_bus_id": ["01.1"]},
    "METRICS": {"general": {"enable": True, "path": "/tmp/metrics.%BUSID"}},
}


# ---------------------------------------------------------------------------
# add_metadata_information_to_prometheus
# ---------------------------------------------------------------------------

class TestAddMetadataInformationToPrometheus:
    def test_reads_topo_file_and_calls_parse(self, tmp_path, monkeypatch):
        topo_file = tmp_path / "topo.json"
        topo_file.write_text(json.dumps(TOPO_DATA))
        monkeypatch.setattr(exporter, "topology_file_path", str(topo_file))

        called_with = {}
        def fake_parse(devices):
            called_with["devices"] = devices
        monkeypatch.setattr(exporter, "parse_spyre_topology", fake_parse)

        exporter.gauges.clear()
        exporter.add_metadata_information_to_prometheus()

        assert called_with["devices"] == TOPO_DATA["devices"]
        from topology import metadata_gauge
        assert metadata_gauge in exporter.gauges

    def test_missing_topo_file_is_handled_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(exporter, "topology_file_path", str(tmp_path / "missing.json"))
        exporter.gauges.clear()
        exporter.add_metadata_information_to_prometheus()  # must not raise
        assert exporter.gauges == []

    def test_invalid_json_is_handled_gracefully(self, tmp_path, monkeypatch):
        topo_file = tmp_path / "topo.json"
        topo_file.write_text("INVALID {{{")
        monkeypatch.setattr(exporter, "topology_file_path", str(topo_file))
        exporter.gauges.clear()
        exporter.add_metadata_information_to_prometheus()  # must not raise
        assert exporter.gauges == []


# ---------------------------------------------------------------------------
# traverse_for_spyre_configs
# ---------------------------------------------------------------------------

class TestTraverseForSpyreConfigs:
    def _setup_config_dir(self, tmp_path, uuid="uuid1", write_pod_files=True):
        config_dir = tmp_path / "config" / uuid
        metrics_dir = tmp_path / "metrics" / uuid
        config_dir.mkdir(parents=True)
        metrics_dir.mkdir(parents=True)
        (config_dir / "senlib_config.json").write_text(json.dumps(SENLIB_CONFIG))
        if write_pod_files:
            (config_dir / "POD_NAME").write_text("pod-1")
            (config_dir / "POD_NAMESPACE").write_text("default")
        return config_dir, metrics_dir

    def test_new_uuid_is_added_to_collection(self, tmp_path, monkeypatch):
        self._setup_config_dir(tmp_path)
        monkeypatch.setattr(exporter, "config_top_path", str(tmp_path / "config"))
        monkeypatch.setattr(exporter, "metrics_top_path", str(tmp_path / "metrics"))
        exporter.metrics_info_collection.clear()

        exporter.traverse_for_spyre_configs()
        assert "uuid1" in exporter.metrics_info_collection

    def test_existing_uuid_is_not_re_created(self, tmp_path, monkeypatch):
        self._setup_config_dir(tmp_path)
        monkeypatch.setattr(exporter, "config_top_path", str(tmp_path / "config"))
        monkeypatch.setattr(exporter, "metrics_top_path", str(tmp_path / "metrics"))
        exporter.metrics_info_collection.clear()

        exporter.traverse_for_spyre_configs()
        first_obj = exporter.metrics_info_collection["uuid1"]
        exporter.traverse_for_spyre_configs()
        assert exporter.metrics_info_collection["uuid1"] is first_obj

    def test_deleted_uuid_is_removed_from_collection(self, tmp_path, monkeypatch):
        cfg_dir, _ = self._setup_config_dir(tmp_path)
        monkeypatch.setattr(exporter, "config_top_path", str(tmp_path / "config"))
        monkeypatch.setattr(exporter, "metrics_top_path", str(tmp_path / "metrics"))
        exporter.metrics_info_collection.clear()

        # First pass: add it
        exporter.traverse_for_spyre_configs()
        assert "uuid1" in exporter.metrics_info_collection

        # Delete the config file so is_deleted() returns True, then remove the dir
        (cfg_dir / "senlib_config.json").unlink()
        import shutil
        shutil.rmtree(str(tmp_path / "config" / "uuid1"))

        # Second pass: should be purged
        exporter.traverse_for_spyre_configs()
        assert "uuid1" not in exporter.metrics_info_collection

    def test_empty_config_dir_leaves_collection_empty(self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        monkeypatch.setattr(exporter, "config_top_path", str(tmp_path / "config"))
        monkeypatch.setattr(exporter, "metrics_top_path", str(tmp_path / "metrics"))
        exporter.metrics_info_collection.clear()

        exporter.traverse_for_spyre_configs()
        assert exporter.metrics_info_collection == {}


# ---------------------------------------------------------------------------
# update_pcie
# ---------------------------------------------------------------------------

class TestUpdatePcie:
    def test_new_device_added_to_collection(self, monkeypatch):
        fake_metrics = {
            "70:00.0": {"maxpayload": "512", "maxreadreq": "4096",
                        "speed": "16GT/s", "width": "x16 (ok)"}
        }
        monkeypatch.setattr(exporter, "get_pcie_metrics", lambda: fake_metrics)
        exporter.pcie_info_collection.clear()

        exporter.update_pcie()
        assert "70:00.0" in exporter.pcie_info_collection

    def test_existing_device_calls_update_if_change(self, monkeypatch):
        fake_metrics = {
            "70:00.0": {"maxpayload": "512", "maxreadreq": "4096",
                        "speed": "16GT/s", "width": "x16 (ok)"}
        }
        monkeypatch.setattr(exporter, "get_pcie_metrics", lambda: fake_metrics)
        exporter.pcie_info_collection.clear()

        exporter.update_pcie()
        existing = exporter.pcie_info_collection["70:00.0"]
        updated = MagicMock()
        exporter.pcie_info_collection["70:00.0"] = updated

        exporter.update_pcie()
        updated.update_if_change.assert_called_once()

    def test_empty_metrics_leaves_collection_unchanged(self, monkeypatch):
        monkeypatch.setattr(exporter, "get_pcie_metrics", lambda: {})
        exporter.pcie_info_collection.clear()

        exporter.update_pcie()
        assert exporter.pcie_info_collection == {}
