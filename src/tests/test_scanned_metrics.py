 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
"""Unit tests for ScannedMetrics in spyremetrics_collector.

These cover the wiring between discovery and the Prometheus gauges: only Bus
IDs the config-driven collector does not own are published, values reach the
same spyre_* gauges, and devices that disappear are unpublished.
"""

import sys
import types

import pytest
from prometheus_client import CollectorRegistry, Gauge as _Gauge

# Stub the spyremetrics C-extension so the collector imports on any host.
_stub = types.ModuleType("spyremetrics")


class _StubMetricFile:
    def __init__(self, path):
        self.path = path
        self.is_old_format = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read_metrics(self):
        return []


_stub.MetricFile = _StubMetricFile
sys.modules.setdefault("spyremetrics", _stub)

import spyremetrics_collector as sc

BUSID = "0000:02:00.0"


class _FakeMetric:
    def __init__(self, name):
        self.name = name


class _FakeMetricFile:
    def __init__(self, path, values, old_format=False):
        self.path = path
        self.values = values
        self.is_old_format = old_format

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read_metrics(self):
        return [(_FakeMetric(name), value) for name, value in self.values.items()]


@pytest.fixture(autouse=True)
def isolated_collector(monkeypatch):
    """Fresh gauge registry and a clean environment for every test."""
    for name in ("SPYRE_METRIC_PATH", "PCIDEVICE_IBM_COM_AIU_PF", "AIU_WORLD_SIZE", "AIUPTI_ENABLE_METRICS"):
        monkeypatch.delenv(name, raising=False)

    registry = CollectorRegistry()

    class _TestGauge(_Gauge):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("registry", registry)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sc, "Gauge", _TestGauge)
    sc.spyre_metrics_guages.clear()
    sc.init_gauges()
    return registry


@pytest.fixture()
def registry(isolated_collector):
    """The isolated registry populated by the autouse fixture."""
    return isolated_collector


def _write_metric_file(directory, busid=BUSID):
    path = directory / f"metrics.{busid}"
    path.write_bytes(b"\x00" * 64)
    return path


def _sample(registry, metric="spyre_power_watts", busid=BUSID, metric_type="vf",
            pod="", namespace=""):
    return registry.get_sample_value(metric, {
        "spyre": busid,
        "type": metric_type,
        "pod": pod,
        "namespace": namespace,
        "node": sc.node_name,
    })


class TestEnablement:
    def test_disabled_without_metric_path(self):
        manager = sc.ScannedMetrics(metric_path="")

        assert manager.enabled is False

        manager.refresh()
        assert manager.files == {}

        manager.start()
        assert manager.running is False

    def test_enabled_with_metric_path(self, tmp_path):
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        assert manager.enabled is True

    def test_reads_path_from_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPYRE_METRIC_PATH", str(tmp_path / "metrics.%BUSID"))
        manager = sc.ScannedMetrics()
        assert manager.metric_path == str(tmp_path / "metrics.%BUSID")

    def test_falls_back_to_the_design_document_default(self):
        """PR #217 scanned /tmp/metrics.%BUSID when the variable was unset."""
        manager = sc.ScannedMetrics()
        assert manager.metric_path == "/tmp/metrics.%BUSID"


class TestPollInterval:
    def test_default_is_one_second(self):
        manager = sc.ScannedMetrics(metric_path="/tmp/metrics.%BUSID")
        assert manager.poll_interval == sc.scanned_default_poll_interval

    def test_aiupti_enable_metrics_selects_fast_polling(self, monkeypatch):
        monkeypatch.setenv("AIUPTI_ENABLE_METRICS", "TRUE")
        manager = sc.ScannedMetrics(metric_path="/tmp/metrics.%BUSID")
        assert manager.poll_interval == sc.scanned_fast_poll_interval


class TestRefresh:
    def test_discovers_metric_files(self, tmp_path):
        _write_metric_file(tmp_path)
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))

        manager.refresh()

        assert list(manager.files) == [BUSID]

    def test_config_owned_bus_ids_are_not_published(
            self, tmp_path, registry, monkeypatch):
        """A device the config-driven collector owns is not published a second time."""
        path = _write_metric_file(tmp_path, "0000:02:00.0")
        _write_metric_file(tmp_path, "0000:03:00.0")
        monkeypatch.setattr(sc, "MetricFile", lambda _path: _FakeMetricFile(path, {"pwr": 42.0}))
        owned = {"0000:03:00.0": ("pod-3", "ns-3")}
        manager = sc.ScannedMetrics(
            metric_path=str(tmp_path / "metrics.%BUSID"),
            config_owned_provider=lambda: owned,
        )

        manager.refresh()
        manager.read()

        assert set(manager.files) == {"0000:02:00.0", "0000:03:00.0"}
        assert _sample(registry, busid="0000:02:00.0") == 42.0
        assert _sample(registry, busid="0000:03:00.0") is None

    def test_config_owned_provider_is_optional(self, tmp_path):
        _write_metric_file(tmp_path)
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))

        manager.refresh()

        assert list(manager.files) == [BUSID]


class TestRead:
    def test_values_reach_the_shared_gauges(self, tmp_path, registry, monkeypatch):
        path = _write_metric_file(tmp_path)
        monkeypatch.setattr(sc, "MetricFile",
                            lambda _path: _FakeMetricFile(path, {"pwr": 42.0, "tempr": 75.5}))
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.refresh()

        manager.read()

        assert _sample(registry) == 42.0
        assert _sample(registry, "spyre_temperature_celsius") == 75.5

    def test_pod_labels_come_from_the_uuid_directory(self, tmp_path, registry, monkeypatch):
        uuid_dir = tmp_path / "uuid-a"
        uuid_dir.mkdir()
        path = _write_metric_file(uuid_dir)
        (uuid_dir / "POD_NAME").write_text("vllm-granite-predictor-abc")
        (uuid_dir / "POD_NAMESPACE").write_text("vllm-granite")
        monkeypatch.setattr(sc, "MetricFile", lambda _path: _FakeMetricFile(path, {"pwr": 12.0}))
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.refresh()

        manager.read()

        assert _sample(registry, pod="vllm-granite-predictor-abc",
                       namespace="vllm-granite") == 12.0

    def test_old_format_is_labelled_pf(self, tmp_path, registry, monkeypatch):
        path = _write_metric_file(tmp_path)
        monkeypatch.setattr(sc, "MetricFile",
                            lambda _path: _FakeMetricFile(path, {"pwr": 1.0}, old_format=True))
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.refresh()

        manager.read()

        assert _sample(registry, metric_type="pf") == 1.0

    def test_read_error_does_not_raise(self, tmp_path, monkeypatch):
        _write_metric_file(tmp_path)

        def _boom(_path):
            raise OSError("unreadable")

        monkeypatch.setattr(sc, "MetricFile", _boom)
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.refresh()

        manager.read()  # must not raise
        assert manager.published == {}

    def test_unknown_metric_names_are_ignored(self, tmp_path, registry, monkeypatch):
        path = _write_metric_file(tmp_path)
        monkeypatch.setattr(sc, "MetricFile",
                            lambda _path: _FakeMetricFile(path, {"pwr": 42.0, "not_a_metric": 9.0}))
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.refresh()

        manager.read()

        assert _sample(registry) == 42.0


class TestHotPlug:
    def test_disappeared_device_is_unpublished(self, tmp_path, registry, monkeypatch):
        path = _write_metric_file(tmp_path)
        monkeypatch.setattr(sc, "MetricFile", lambda _path: _FakeMetricFile(path, {"pwr": 42.0}))
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.refresh()
        manager.read()
        assert _sample(registry) == 42.0

        path.unlink()
        manager.refresh()

        assert manager.files == {}
        assert manager.published == {}
        assert _sample(registry) is None

    def test_bus_id_taken_over_by_config_is_removed(
            self, tmp_path, registry, monkeypatch):
        """Config takes ownership with different labels: the scanned series must go."""
        path = _write_metric_file(tmp_path)
        monkeypatch.setattr(sc, "MetricFile", lambda _path: _FakeMetricFile(path, {"pwr": 42.0}))
        owned = {}
        manager = sc.ScannedMetrics(
            metric_path=str(tmp_path / "metrics.%BUSID"),
            config_owned_provider=lambda: owned,
        )
        manager.refresh()
        manager.read()
        assert _sample(registry) == 42.0

        owned[BUSID] = ("other-pod", "other-namespace")
        manager.refresh()

        assert _sample(registry) is None

    def test_bus_id_handed_over_with_same_labels_keeps_the_series(
            self, tmp_path, registry, monkeypatch):
        """Config owns an identical label set: removing it would delete config's values."""
        path = _write_metric_file(tmp_path)
        monkeypatch.setattr(sc, "MetricFile", lambda _path: _FakeMetricFile(path, {"pwr": 42.0}))
        owned = {}
        manager = sc.ScannedMetrics(
            metric_path=str(tmp_path / "metrics.%BUSID"),
            config_owned_provider=lambda: owned,
        )
        manager.refresh()
        manager.read()

        owned[BUSID] = ("", "")  # same pod/namespace the scan published
        manager.refresh()

        assert manager.published == {}
        assert _sample(registry) == 42.0


class TestLifecycle:
    def test_start_and_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "scanned_default_poll_interval", 0.05)
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))

        manager.start()
        assert manager.running is True
        assert manager.thread is not None
        assert manager.thread.is_alive()

        manager.stop()
        assert manager.running is False

    def test_double_start_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "scanned_default_poll_interval", 0.05)
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.start()
        thread = manager.thread
        manager.start()
        assert manager.thread is thread
        manager.stop()

    def test_stop_before_start_is_noop(self, tmp_path):
        manager = sc.ScannedMetrics(metric_path=str(tmp_path / "metrics.%BUSID"))
        manager.stop()  # must not raise
