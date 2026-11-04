 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
"""Unit tests for metric_discovery.

The discovery strategies are exercised against a real temporary directory, so
these tests need no libaiupti, no metric files and no Prometheus.  Strategy B1
is driven through a stub ``spyremetrics`` module.
"""

import sys
import types

import pytest

import metric_discovery as md


@pytest.fixture(autouse=True)
def no_device_plugin_env(monkeypatch):
    """Keep the host environment from selecting a strategy behind the test's back."""
    for name in ("SPYRE_METRIC_PATH", "PCIDEVICE_IBM_COM_AIU_PF", "AIU_WORLD_SIZE"):
        monkeypatch.delenv(name, raising=False)


def _install_open_metrics(monkeypatch, paths, raises=None):
    """Install a spyremetrics stub whose MetricFile.open_metrics returns *paths*."""

    class _FakeMetricFile:
        def __init__(self, path):
            self.path = path

    def open_metrics(metric_path, *args, **kwargs):
        if raises is not None:
            raise raises
        return [_FakeMetricFile(p) for p in paths]

    module = types.ModuleType("spyremetrics")
    module.MetricFile = types.SimpleNamespace(open_metrics=open_metrics)
    monkeypatch.setitem(sys.modules, "spyremetrics", module)


# ===========================================================================
# split_busid_path
# ===========================================================================

class TestSplitBusidPath:
    def test_flat_style(self):
        assert md.split_busid_path("/tmp/metrics.%BUSID") == ("/tmp", "metrics.%BUSID")

    def test_subdir_style(self):
        assert md.split_busid_path("/tmp/spyre-metrics/%BUSID/metrics.bin") == (
            "/tmp/spyre-metrics", "metrics.bin")

    def test_no_busid(self):
        assert md.split_busid_path("/tmp/test_metric.bin") == ("/tmp", "test_metric.bin")

    def test_daemonset_path(self):
        """The exporter DaemonSet sets SPYRE_METRIC_PATH=/tmp/spyre-metrics/metrics.%BUSID"""
        assert md.split_busid_path("/tmp/spyre-metrics/metrics.%BUSID") == (
            "/tmp/spyre-metrics", "metrics.%BUSID")


# ===========================================================================
# busid_from_path
# ===========================================================================

class TestBusidFromPath:
    def test_flat_style(self):
        assert md.busid_from_path("/tmp/metrics.0000:02:00.0", "/tmp", "metrics.%BUSID") == "0000:02:00.0"

    def test_subdir_style(self):
        assert md.busid_from_path("/tmp/spyre-metrics/0000:02:00.0/metrics.bin",
                                  "/tmp/spyre-metrics", "metrics.bin") == "0000:02:00.0"

    def test_daemonset_flat_style(self):
        assert md.busid_from_path("/tmp/spyre-metrics/metrics.0000:05:00.0",
                                  "/tmp/spyre-metrics", "metrics.%BUSID") == "0000:05:00.0"


# ===========================================================================
# Strategy A -- plain file
# ===========================================================================

class TestStrategyPlainFile:
    def test_file_found(self, tmp_path):
        metric_file = tmp_path / "test_metric.bin"
        metric_file.write_bytes(b"\x00" * 64)

        found = md.discover(str(metric_file))

        assert list(found) == [md.PLAIN_FILE_KEY]
        assert found[md.PLAIN_FILE_KEY].path == str(metric_file)
        assert found[md.PLAIN_FILE_KEY].strategy == md.STRATEGY_PLAIN_FILE

    def test_file_not_found(self, tmp_path):
        assert md.discover(str(tmp_path / "nonexistent.bin")) == {}

    def test_directory_instead_of_file(self, tmp_path):
        assert md.discover(str(tmp_path)) == {}


# ===========================================================================
# Strategy B2 -- flat layout
# ===========================================================================

class TestStrategyB2FlatStyle:
    def test_single_aiu(self, tmp_path):
        (tmp_path / "metrics.0000:02:00.0").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert found["0000:02:00.0"].path == str(tmp_path / "metrics.0000:02:00.0")
        assert found["0000:02:00.0"].strategy == md.STRATEGY_DIRECTORY_SCAN

    def test_multi_aiu(self, tmp_path):
        busids = ("0000:02:00.0", "0000:03:00.0", "0000:05:00.0")
        for busid in busids:
            (tmp_path / f"metrics.{busid}").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert set(found) == set(busids)

    def test_empty_directory(self, tmp_path):
        assert md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="") == {}

    def test_directory_not_found(self, tmp_path):
        assert md.discover(str(tmp_path / "missing" / "metrics.%BUSID"),
                           pcidev="", world_size="") == {}

    def test_non_matching_files_ignored(self, tmp_path):
        (tmp_path / "metrics.0000:02:00.0").write_bytes(b"\x00")
        (tmp_path / "other.0000:03:00.0").write_bytes(b"\x00")  # wrong prefix
        (tmp_path / "metrics").write_bytes(b"\x00")             # no Bus ID suffix

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert list(found) == ["0000:02:00.0"]


# ===========================================================================
# Strategy B2 -- named subdir layout (<dir>/%BUSID/<filename>)
# ===========================================================================

class TestStrategyB2NamedSubdir:
    def test_single_aiu(self, tmp_path):
        subdir = tmp_path / "0000:02:00.0"
        subdir.mkdir()
        (subdir / "metrics.bin").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "%BUSID" / "metrics.bin"), pcidev="", world_size="")

        assert found["0000:02:00.0"].path == str(subdir / "metrics.bin")

    def test_multi_aiu(self, tmp_path):
        busids = ["0000:02:00.0", "0000:03:00.0"]
        for busid in busids:
            subdir = tmp_path / busid
            subdir.mkdir()
            (subdir / "metrics.bin").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "%BUSID" / "metrics.bin"), pcidev="", world_size="")

        assert set(found) == set(busids)

    def test_subdir_without_expected_file_ignored(self, tmp_path):
        (tmp_path / "0000:02:00.0").mkdir()

        assert md.discover(str(tmp_path / "%BUSID" / "metrics.bin"),
                           pcidev="", world_size="") == {}


# ===========================================================================
# Strategy B2 -- per-pod UUID subdirs created by the device plugin
# ===========================================================================

class TestStrategyB2UuidSubdir:
    def test_single_aiu(self, tmp_path):
        uuid_dir = tmp_path / "eeef64e4-7785-46c2-8b0b-1f9cff60fd7a"
        uuid_dir.mkdir()
        (uuid_dir / "metrics.0002:00:00.0").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert found["0002:00:00.0"].path == str(uuid_dir / "metrics.0002:00:00.0")

    def test_multi_aiu_across_pods(self, tmp_path):
        for uuid, busid in (("uuid-a", "0001:00:00.0"), ("uuid-b", "0002:00:00.0")):
            uuid_dir = tmp_path / uuid
            uuid_dir.mkdir()
            (uuid_dir / f"metrics.{busid}").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert set(found) == {"0001:00:00.0", "0002:00:00.0"}

    def test_flat_file_takes_priority(self, tmp_path):
        """
        When both layouts expose the same Bus ID, the flat file wins and its
        path is the one published.
        """
        (tmp_path / "metrics.0002:00:00.0").write_bytes(b"\x00" * 64)
        uuid_dir = tmp_path / "uuid-a"
        uuid_dir.mkdir()
        (uuid_dir / "metrics.0002:00:00.0").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert found["0002:00:00.0"].path == str(tmp_path / "metrics.0002:00:00.0")

    def test_pod_info_read_from_uuid_dir(self, tmp_path):
        uuid_dir = tmp_path / "uuid-a"
        uuid_dir.mkdir()
        (uuid_dir / "metrics.0002:00:00.0").write_bytes(b"\x00" * 64)
        (uuid_dir / "POD_NAME").write_text("vllm-granite-predictor-abc\n")
        (uuid_dir / "POD_NAMESPACE").write_text("vllm-granite\n")

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert found["0002:00:00.0"].pod_name == "vllm-granite-predictor-abc"
        assert found["0002:00:00.0"].pod_namespace == "vllm-granite"

    def test_pod_info_absent_is_empty(self, tmp_path):
        uuid_dir = tmp_path / "uuid-a"
        uuid_dir.mkdir()
        (uuid_dir / "metrics.0002:00:00.0").write_bytes(b"\x00" * 64)

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="", world_size="")

        assert found["0002:00:00.0"].pod_name == ""
        assert found["0002:00:00.0"].pod_namespace == ""


# ===========================================================================
# Strategy B1 -- Bus ID expansion delegated to spyremetrics
# ===========================================================================

class TestStrategyB1:
    def test_uses_open_metrics_when_device_plugin_env_present(self, tmp_path, monkeypatch):
        metric_file = tmp_path / "metrics.0000:02:00.0"
        metric_file.write_bytes(b"\x00" * 64)
        _install_open_metrics(monkeypatch, [str(metric_file)])

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="0000:02:00.0")

        assert found["0000:02:00.0"].strategy == md.STRATEGY_ENV_TEMPLATE

    def test_falls_back_to_scan_when_open_metrics_empty(self, tmp_path, monkeypatch):
        metric_file = tmp_path / "metrics.0000:02:00.0"
        metric_file.write_bytes(b"\x00" * 64)
        _install_open_metrics(monkeypatch, [])

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="0000:02:00.0")

        assert found["0000:02:00.0"].strategy == md.STRATEGY_DIRECTORY_SCAN

    def test_falls_back_to_scan_when_open_metrics_raises(self, tmp_path, monkeypatch):
        metric_file = tmp_path / "metrics.0000:02:00.0"
        metric_file.write_bytes(b"\x00" * 64)
        _install_open_metrics(monkeypatch, [], raises=RuntimeError("boom"))

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="0000:02:00.0")

        assert found["0000:02:00.0"].strategy == md.STRATEGY_DIRECTORY_SCAN

    def test_skips_paths_that_do_not_exist(self, tmp_path, monkeypatch):
        _install_open_metrics(monkeypatch, [str(tmp_path / "metrics.0000:09:00.0")])

        found = md.discover(str(tmp_path / "metrics.%BUSID"), pcidev="0000:09:00.0")

        assert found == {}


# ===========================================================================
# discover() argument handling
# ===========================================================================

class TestDiscoverArguments:
    def test_empty_path_returns_nothing(self):
        assert md.discover("") == {}

    def test_env_selects_strategy(self, tmp_path, monkeypatch):
        (tmp_path / "metrics.0000:02:00.0").write_bytes(b"\x00" * 64)
        monkeypatch.setenv("SPYRE_METRIC_PATH", str(tmp_path / "metrics.%BUSID"))
        monkeypatch.delenv("PCIDEVICE_IBM_COM_AIU_PF", raising=False)

        assert "0000:02:00.0" in md.discover()

    def test_world_size_selects_b1(self, tmp_path, monkeypatch):
        metric_file = tmp_path / "metrics.0000:02:00.0"
        metric_file.write_bytes(b"\x00" * 64)
        _install_open_metrics(monkeypatch, [str(metric_file)])

        found = md.discover(str(tmp_path / "metrics.%BUSID"), world_size="2")

        assert found["0000:02:00.0"].strategy == md.STRATEGY_ENV_TEMPLATE


# ===========================================================================
# diff_discovered -- add / remove tracking used for hot-plug
# ===========================================================================

class TestDiffDiscovered:
    @staticmethod
    def _ref(busid):
        return md.MetricFileRef(busid=busid, path=f"/tmp/metrics.{busid}")

    def test_added_and_removed(self):
        previous = {"a": self._ref("a"), "b": self._ref("b")}
        current = {"b": self._ref("b"), "c": self._ref("c")}

        added, removed = md.diff_discovered(previous, current)

        assert added == ["c"]
        assert removed == ["a"]

    def test_no_change(self):
        current = {"a": self._ref("a")}
        assert md.diff_discovered(current, current) == ([], [])

    def test_handles_empty_sides(self):
        added, removed = md.diff_discovered({}, {"a": self._ref("a")})
        assert (added, removed) == (["a"], [])
