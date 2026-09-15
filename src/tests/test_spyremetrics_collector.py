 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest
from prometheus_client import CollectorRegistry, Gauge as _Gauge

# ---------------------------------------------------------------------------
# Stub out the `spyremetrics` C-extension so the collector can be imported
# on any Python version / environment that doesn't have it installed.
# ---------------------------------------------------------------------------
_spyremetrics_stub = types.ModuleType("spyremetrics")

class _FakeMetricFile:
    """Minimal stand-in for spyremetrics.MetricFile."""
    def __init__(self, path):
        self.path = path
        self.is_old_format = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read_metrics(self):
        return []

_spyremetrics_stub.MetricFile = _FakeMetricFile
sys.modules.setdefault("spyremetrics", _spyremetrics_stub)

import spyremetrics_collector as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_CONFIG = {
    "GENERAL": {"sen_bus_id": ["01.1"]},
    "METRICS": {"general": {"enable": True, "path": "/tmp/metrics.%BUSID"}},
}

MOCK_CONFIG_DISABLED = {
    "GENERAL": {"sen_bus_id": ["01.1"]},
    "METRICS": {"general": {"enable": False, "path": "/tmp/metrics.%BUSID"}},
}

MOCK_CONFIG_NO_ENABLE_KEY = {
    "GENERAL": {"sen_bus_id": ["01.1"]},
    "METRICS": {"general": {"path": "/tmp/metrics.%BUSID"}},
}


def _make_info(tmp_path, uuid="uuid1", config=None, write_pod_files=True, write_config=True):
    """Create a spyre_map_info with a temp filesystem layout."""
    config_dir = tmp_path / "config" / uuid
    metrics_dir = tmp_path / "metrics" / uuid
    config_dir.mkdir(parents=True)
    metrics_dir.mkdir(parents=True)

    if write_pod_files:
        (config_dir / "POD_NAME").write_text("pod-1")
        (config_dir / "POD_NAMESPACE").write_text("default")

    if write_config:
        cfg = config or MOCK_CONFIG
        (config_dir / "senlib_config.json").write_text(json.dumps(cfg))

    info = sc.spyre_map_info(str(tmp_path / "config"), str(tmp_path / "metrics"), uuid)
    return info, config_dir, metrics_dir


# ---------------------------------------------------------------------------
# Fixture: isolated prometheus registry so init_gauges() never double-registers.
# We patch the Gauge class *inside spyremetrics_collector* so every call to
# Gauge(...) registers into a fresh per-test registry instead of the global one.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Patch Gauge in spyremetrics_collector to use a fresh registry per test."""
    registry = CollectorRegistry()
    OrigGauge = _Gauge

    def FreshGauge(name, doc, labelnames=None, registry=registry, **kw):
        return OrigGauge(name, doc, labelnames=labelnames or [], registry=registry, **kw)

    monkeypatch.setattr(sc, "Gauge", FreshGauge)
    sc.spyre_metrics_guages.clear()
    yield registry


# ---------------------------------------------------------------------------
# init_gauges
# ---------------------------------------------------------------------------

class TestInitGauges:
    def test_returns_all_gauge_keys(self):
        gauges = list(sc.init_gauges())
        expected_keys = {"pwr", "tempr", "rdmem", "wrmem", "rxpci",
                         "txpci", "rdrdma", "wrrdma", "avgmem", "peakmem"}
        assert set(sc.spyre_metrics_guages.keys()) == expected_keys

    def test_idempotent_on_re_init(self):
        # init_gauges populates the dict; calling again reuses existing gauge objects
        sc.init_gauges()
        assert len(sc.spyre_metrics_guages) == 10


# ---------------------------------------------------------------------------
# getConfig
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_valid_json(self, tmp_path):
        f = tmp_path / "cfg.json"
        f.write_text(json.dumps(MOCK_CONFIG))
        result = sc.getConfig(str(f))
        assert result["GENERAL"]["sen_bus_id"] == ["01.1"]

    def test_missing_file_returns_none(self, tmp_path):
        result = sc.getConfig(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json {{{")
        result = sc.getConfig(str(f))
        assert result is None


# ---------------------------------------------------------------------------
# SpyreMetrics
# ---------------------------------------------------------------------------

class TestSpyreMetrics:
    def test_init_sets_metrics_file_path(self):
        cfg = json.loads(json.dumps(MOCK_CONFIG))
        m = sc.SpyreMetrics(cfg, "/tmp/metrics.01.1")
        assert m.metrics_file == "/tmp/metrics.01.1"
        assert cfg["METRICS"]["general"]["path"] == "/tmp/metrics.01.1"
        assert m.loaded is False
        assert m.type == ""

    def test_set_type(self):
        cfg = json.loads(json.dumps(MOCK_CONFIG))
        m = sc.SpyreMetrics(cfg, "/tmp/metrics.01.1")
        m.set_type("vf")
        assert m.type == "vf"

    def test_load(self):
        cfg = json.loads(json.dumps(MOCK_CONFIG))
        m = sc.SpyreMetrics(cfg, "/tmp/metrics.01.1")
        m.load()
        assert m.loaded is True


# ---------------------------------------------------------------------------
# spyre_map_info — construction
# ---------------------------------------------------------------------------

class TestSpyreMapInfoInit:
    def test_paths_are_set_correctly(self, tmp_path):
        info, cfg_dir, met_dir = _make_info(tmp_path)
        assert info.uuid == "uuid1"
        assert info.config_file == str(cfg_dir / "senlib_config.json")
        assert info.pod_name_file == str(cfg_dir / "POD_NAME")
        assert info.pod_namespace_file == str(cfg_dir / "POD_NAMESPACE")
        assert info.metrics_folder == str(met_dir)


# ---------------------------------------------------------------------------
# spyre_map_info.try_load_config
# ---------------------------------------------------------------------------

class TestTryLoadConfig:
    def test_config_not_ready_when_file_missing(self, tmp_path):
        info, cfg_dir, _ = _make_info(tmp_path, write_config=False)
        info.try_load_config()
        assert info.config_ready is False

    def test_config_ready_when_file_present(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        info.try_load_config()
        assert info.config_ready is True
        assert info.bus_ids == ["01.1"]

    def test_metrics_disabled_sets_none_entry(self, tmp_path):
        info, _, _ = _make_info(tmp_path, config=MOCK_CONFIG_DISABLED)
        info.try_load_config()
        assert info.config_ready is True
        assert info.spyre_id_metrics_map.get("") is None

    def test_missing_enable_key_disables_metrics(self, tmp_path):
        info, _, _ = _make_info(tmp_path, config=MOCK_CONFIG_NO_ENABLE_KEY)
        info.try_load_config()
        assert info.config_ready is True
        assert info.spyre_id_metrics_map.get("") is None

    def test_invalid_json_sets_none_and_ready(self, tmp_path):
        config_dir = tmp_path / "config" / "uuid-bad"
        metrics_dir = tmp_path / "metrics" / "uuid-bad"
        config_dir.mkdir(parents=True)
        metrics_dir.mkdir(parents=True)
        (config_dir / "senlib_config.json").write_text("INVALID {{{")
        info = sc.spyre_map_info(str(tmp_path / "config"), str(tmp_path / "metrics"), "uuid-bad")
        info.try_load_config()
        assert info.config_ready is True
        assert info.spyre_id_metrics_map.get("") is None

    def test_metrics_file_entry_created_per_bus_id(self, tmp_path):
        info, _, met_dir = _make_info(tmp_path)
        info.try_load_config()
        assert "01.1" in info.spyre_id_metrics_map
        assert info.spyre_id_metrics_map["01.1"].metrics_file == str(met_dir / "metrics.01.1")


# ---------------------------------------------------------------------------
# spyre_map_info.try_load
# ---------------------------------------------------------------------------

class TestTryLoad:
    def test_skip_when_no_pod_files_and_no_alternative(self, tmp_path):
        info, _, _ = _make_info(tmp_path, write_pod_files=False)
        info.try_load()  # should not raise
        assert info.pod_name == ""

    def test_skip_when_only_one_pod_file_missing(self, tmp_path):
        info, cfg_dir, _ = _make_info(tmp_path, write_pod_files=False)
        (cfg_dir / "POD_NAME").write_text("pod-x")
        # POD_NAMESPACE is missing — should fall through to alternative check
        info.try_load()
        assert info.pod_name == ""

    def test_fallback_to_alternative_pod_files(self, tmp_path):
        info, cfg_dir, met_dir = _make_info(tmp_path, write_pod_files=False)
        (met_dir / "POD_NAME").write_text("alt-pod")
        (met_dir / "POD_NAMESPACE").write_text("alt-ns")
        info.try_load()
        assert info.pod_name == "alt-pod"
        assert info.pod_namespace == "alt-ns"

    def test_pod_name_and_namespace_read_from_files(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        info.try_load()
        assert info.pod_name == "pod-1"
        assert info.pod_namespace == "default"

    def test_pod_name_not_re_read_when_already_set(self, tmp_path):
        info, cfg_dir, _ = _make_info(tmp_path)
        info.pod_name = "already-set"
        info.try_load()
        assert info.pod_name == "already-set"

    def test_config_becomes_ready_after_try_load(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        info.try_load()
        assert info.config_ready is True

    def test_all_loaded_true_when_no_metric_files_needed(self, tmp_path):
        """Metrics disabled → spyre_id_metrics_map has None entry → all_loaded=True."""
        info, _, _ = _make_info(tmp_path, config=MOCK_CONFIG_DISABLED)
        info.try_load()
        assert info.all_loaded is True

    def test_all_loaded_false_when_metric_file_missing(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        info.try_load()
        # metrics file doesn't exist yet → all_loaded should be False
        assert info.all_loaded is False

    def test_metric_file_loaded_with_real_binary(self, tmp_path):
        """Use mock/default_metrics as the binary metrics file."""
        sc.init_gauges()

        default_metrics = Path(__file__).resolve().parent.parent.parent / "mock" / "default_metrics"
        info, cfg_dir, met_dir = _make_info(tmp_path)
        # copy default_metrics to the expected filename
        import shutil
        shutil.copy(str(default_metrics), str(met_dir / "metrics.01.1"))

        # Stub MetricFile to simulate reading metrics from the binary
        class FakeMetric:
            def __init__(self, name):
                self.name = name

        class FakeMetricFile:
            def __init__(self, path):
                self.is_old_format = False
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read_metrics(self):
                return [(FakeMetric("pwr"), 42.0), (FakeMetric("tempr"), 55.0)]

        with patch("spyremetrics_collector.MetricFile", FakeMetricFile):
            info.try_load()

        assert info.all_loaded is True

    def test_early_return_when_already_loaded(self, tmp_path):
        info, _, _ = _make_info(tmp_path, config=MOCK_CONFIG_DISABLED)
        info.try_load()
        assert info.all_loaded is True
        # calling again should return early without error
        info.try_load()

    def test_metric_read_error_is_caught(self, tmp_path):
        sc.init_gauges()

        info, cfg_dir, met_dir = _make_info(tmp_path)
        (met_dir / "metrics.01.1").write_bytes(b"\x00" * 16)

        class BrokenMetricFile:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read_metrics(self): raise RuntimeError("parse error")
            is_old_format = False

        with patch("spyremetrics_collector.MetricFile", BrokenMetricFile):
            info.try_load()  # must not raise

        assert info.spyre_id_metrics_map["01.1"].loaded is True


# ---------------------------------------------------------------------------
# spyre_map_info.is_deleted / is_alive
# ---------------------------------------------------------------------------

class TestIsDeletedIsAlive:
    def test_not_deleted_when_config_exists(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        assert info.is_deleted() is False

    def test_deleted_when_config_removed(self, tmp_path):
        info, cfg_dir, _ = _make_info(tmp_path)
        (cfg_dir / "senlib_config.json").unlink()
        assert info.is_deleted() is True

    def test_is_alive_returns_1_when_config_exists(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        assert info.is_alive() == 1

    def test_is_alive_returns_0_when_config_deleted(self, tmp_path):
        info, cfg_dir, _ = _make_info(tmp_path)
        (cfg_dir / "senlib_config.json").unlink()
        assert info.is_alive() == 0


# ---------------------------------------------------------------------------
# spyre_map_info.clear / remove_gauge_labels / allocation gauge
# ---------------------------------------------------------------------------

class TestClearAndGauges:
    def test_clear_does_not_raise_when_labels_never_added(self, tmp_path):
        sc.init_gauges()
        info, _, _ = _make_info(tmp_path)
        info.try_load_config()
        info.pod_name = "pod-1"
        info.pod_namespace = "default"
        info.clear()  # must not raise

    def test_add_and_remove_allocation_gauge(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        info.pod_name = "pod-1"
        info.pod_namespace = "default"
        info.bus_ids = ["01.1"]
        info.add_allocation_gauge()
        # remove should not raise
        info.remove_allocation_gauge()

    def test_remove_allocation_gauge_missing_label_is_skipped(self, tmp_path):
        info, _, _ = _make_info(tmp_path)
        info.pod_name = "pod-1"
        info.pod_namespace = "default"
        info.bus_ids = ["99.9"]
        # never added — remove should swallow KeyError/ValueError
        info.remove_allocation_gauge()

    def test_remove_gauge_labels_missing_is_skipped(self, tmp_path):
        sc.init_gauges()
        info, _, _ = _make_info(tmp_path)
        info.pod_name = "pod-1"
        info.pod_namespace = "default"
        # Never set any gauge labels — remove should not raise
        info.remove_gauge_labels("99.9", "vf")
