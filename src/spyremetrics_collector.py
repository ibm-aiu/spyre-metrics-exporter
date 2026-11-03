 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
from typing import Any
import os
import json
import threading
import time
import numpy as np

from metric_discovery import (
    DEFAULT_METRIC_PATH,
    SPYRE_METRIC_PATH_ENV,
    diff_discovered,
    discover,
)

senlib_config_file = os.getenv("CONFIG_FILENAME", "senlib_config.json")
local_metric_file_prefix = "metrics."

node_name = os.getenv('NODE_NAME', "unknown")

from prometheus_client import Gauge
from spyremetrics import MetricFile
import struct

pod_spyre_gauge = Gauge('spyre_allocation', 'Spyre card allocation result', labelnames=['pod', 'namespace', 'spyre', 'node'])
spyre_metrics_guages = dict()

def init_gauges():
    loaded_gauges = []
    defs = [
            ("pwr",     "spyre_power_watts",          "power consumption in watts"),
            ("tempr",   "spyre_temperature_celsius",  "temperature in celsius"),
            ("rdmem",   "spyre_read_memory_bytes",    "device memory read in bytes"),
            ("wrmem",   "spyre_write_memory_bytes",   "device memory write in bytes"),
            ("rxpci",   "spyre_rxpci_bytes",          "PCIe host-to-device bytes"),
            ("txpci",   "spyre_txpci_bytes",          "PCIe device-to-host bytes"),
            ("rdrdma",  "spyre_rdma_read_bytes",      "RDMA read bytes"),
            ("wrrdma",  "spyre_rdma_write_bytes",     "RDMA write bytes"),
            ("avgmem",  "spyre_avg_memory_bytes",     "average device memory usage in bytes"),
            ("peakmem", "spyre_peak_memory_bytes",    "peak device memory usage in bytes"),
        ]
    for key, name, doc in defs:
        spyre_metrics_guages[key] = Gauge(name, doc, labelnames=["spyre", "type", "pod", "namespace", "node"])
    return spyre_metrics_guages.values()

#####################################
# functions to load configuration
def getConfig(config_file):
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
            return config
    except:
        return None

class SpyreMetrics():
    """
    SpyreMetrics contains 'per-device' information of config and metric file.
    """
    def __init__(self, config, metrics_file):
        self.config = config
        self.metrics_file = metrics_file
        # This is set for local config to make metric parser work correctly.
        self.config["METRICS"]["general"]["path"] = metrics_file
        self.loaded = False
        self.type = ""

    def set_type(self, type) :
        self.type = type

    def load(self):
        self.loaded = True

class spyre_map_info():
    """
    spyre_map_info contains 'per-pod' information of senlib config and spyre metrics.

    - try_load() must be called to initialize the prometheus gauge labels when the corresponding metric files are ready.
    - clear() must be called on deletion to remove the labels.
    - safeRead functions are defined to return zero if the metric files are deleted before the labels are cleared.

    """
    def __init__(self, config_top_path, metrics_top_path, uuid):
        self.uuid = uuid
        config_folder = os.path.join(config_top_path, uuid)
        self.config_file = os.path.join(config_folder, senlib_config_file)
        self.pod_name_file = os.path.join(config_folder, "POD_NAME")
        self.pod_namespace_file = os.path.join(config_folder, "POD_NAMESPACE")
        self.metrics_folder = os.path.join(metrics_top_path, uuid)
        self.alternative_pod_name_file = os.path.join(self.metrics_folder, "POD_NAME")
        self.alternative_pod_namespace_file = os.path.join(self.metrics_folder, "POD_NAMESPACE")
        self.config = None
        self.config_ready = False
        self.all_loaded = False
        self.allocation_gauge_added = False

        self.bus_ids = [] # defined in config
        self.spyre_id_metrics_map = dict() # added by user pod
        self.pod_name = "" # added by device plugin
        self.pod_namespace = "" # added by device plugin

    def try_load(self):
        if not os.path.exists(self.pod_name_file) or not os.path.exists(self.pod_namespace_file):
            # fallback approach
            # set alternative pod info path if present
            if not os.path.exists(self.alternative_pod_name_file) or not os.path.exists(self.alternative_pod_namespace_file):
                print(f"pod info file does not exist, skip {self.uuid}")
                return
            self.pod_name_file = self.alternative_pod_name_file
            self.pod_namespace_file = self.alternative_pod_namespace_file
            print(f"set alternative pod info file: {self.pod_name} for {self.uuid} to {self.pod_name_file}")

        if self.pod_name == "":
            # update pod name value if not set yet
            with open(self.pod_name_file, 'r') as f:
                self.pod_name = f.read()
            print(f"set pod name: {self.pod_name} for {self.uuid}")
        if self.pod_namespace == "":
            # update pod namespace value if not set yet
            with open(self.pod_namespace_file, 'r') as f:
                self.pod_namespace = f.read()
            print(f"set pod namespace: {self.pod_namespace} for {self.uuid}")

        if self.config_ready and self.all_loaded:
            return

        if not self.config_ready:
            self.try_load_config()

        if not self.allocation_gauge_added and len(self.bus_ids) > 0:
            self.allocation_gauge_added = True
            self.add_allocation_gauge()

        self.all_loaded = self.config_ready
        for spyreId, metrics in self.spyre_id_metrics_map.items():
            if metrics is None:
                continue
            if metrics.loaded:
                continue
            if not os.path.exists(metrics.metrics_file):
                print(f"{metrics.metrics_file} not available yet, skip")
                self.all_loaded = False
                continue
            print(f"load metric file for {spyreId}")
            try:
                with MetricFile(metrics.metrics_file) as mf:
                    type = "pf" if mf.is_old_format else "vf"
                    metrics.type = type
                    for metric, value in mf.read_metrics():
                        gauge = spyre_metrics_guages.get(metric.name)
                        if gauge is not None:
                            gauge.labels(spyre=spyreId, type=type, pod=self.pod_name, namespace=self.pod_namespace, node=node_name).set(value)
            except Exception as exc:
                print(f"metrics: read error {metrics.metrics_file} (spyre={spyreId}): {exc}")
            metrics.loaded = True

    def try_load_config(self):
        if os.path.exists(self.config_file):
            try:
                if self.config is None:
                    with open(self.config_file, 'r') as f:
                        self.config = json.load(f)
            except Exception as e:
                print(f"failed to load config for {self.uuid}: {e}, set None")
                self.spyre_id_metrics_map[""] = None
                self.config_ready = True
                return

            bus_ids = self.config["GENERAL"]["sen_bus_id"]
            self.bus_ids = bus_ids

            try:
                metric_disabled = not self.config["METRICS"]["general"]["enable"]
            except Exception as e:
                print(f"failed to get METRICS.general.enable of {self.uuid}: {e}")
                metric_disabled = True
            if metric_disabled:
                print(f"{self.uuid} spyre metrics disabled, set None")
                self.spyre_id_metrics_map[""] = None
                self.config_ready = True
                return

            for bus_id in bus_ids:
                # append %BUSID
                metric_filename = local_metric_file_prefix + bus_id
                metrics_file = os.path.join(self.metrics_folder, metric_filename)
                metrics = SpyreMetrics(self.config, metrics_file)
                self.spyre_id_metrics_map[bus_id] = metrics
                print(f"{self.uuid} config points {metrics_file} for {bus_id}")
            self.config_ready = True
        else:
            self.config_ready = False
        if self.config_ready:
            print(f"{self.uuid} config successfully ready")

    def is_deleted(self):
        return not os.path.exists(self.config_file)

    def clear(self):
        for spyreId, metrics in self.spyre_id_metrics_map.items():
            if metrics is None:
                continue
            self.remove_gauge_labels(spyreId, metrics.type)
        self.remove_allocation_gauge()

    def remove_gauge_labels(self, spyreId, type):
        for metric_guage in spyre_metrics_guages.keys():
            try:
                spyre_metrics_guages[metric_guage].remove(spyreId, type, self.pod_name, self.pod_namespace, node_name)
            except (KeyError, ValueError) as e:
                print(f"skip removing label {spyreId}/{type}/{self.pod_name}/{self.pod_namespace} from {metric_guage}: {e}")

    def add_allocation_gauge(self):
        for spyreId in self.bus_ids:
            pod_spyre_gauge.labels(pod=self.pod_name, namespace=self.pod_namespace, spyre=spyreId, node=node_name).set_function(self.is_alive)

    def remove_allocation_gauge(self):
        for spyreId in self.bus_ids:
            try:
                pod_spyre_gauge.remove(self.pod_name, self.pod_namespace, spyreId, node_name)
            except (KeyError, ValueError) as e:
                print(f"skip removing label {spyreId}/{self.pod_name}/{self.pod_namespace} from spyre_allocation: {e}")

    def is_alive(self):
        if not os.path.exists(self.config_file):
            return 0
        return 1


# Poll interval for reading metric files. Matches the libaiupti sampling
# interval selected by AIUPTI_ENABLE_METRICS.
scanned_default_poll_interval = 1.0
scanned_fast_poll_interval = 0.1
# Metric files are re-enumerated less often than they are read, so hot-plugged
# devices are picked up without listing the directory on every poll.
rediscovery_interval = 10.0


class ScannedMetrics():
    """Publishes metric files found by path-template discovery.

    spyre_map_info can only see devices that a senlib_config.json under
    /tmp/spyre-config/<uuid>/ enumerates. When that config is missing, does not
    list a Bus ID, or is regenerated with metrics disabled, the device still
    writes metrics but the exporter would never look at them.

    This class scans the tree described by SPYRE_METRIC_PATH, falling back to
    DEFAULT_METRIC_PATH, and publishes what it finds on the same spyre_* gauges
    the config-driven path writes, with type="vf" or type="pf" from the file
    format. Bus IDs already owned by spyre_map_info are skipped, so a device is
    never published twice with different labels.
    """

    def __init__(self, metric_path=None, config_owned_provider=None):
        self.metric_path = metric_path if metric_path is not None else os.getenv(SPYRE_METRIC_PATH_ENV, DEFAULT_METRIC_PATH)
        self.config_owned_provider = config_owned_provider
        self.files = {}
        # busid -> (type, pod_name, pod_namespace) set on the spyre_* gauges
        self.published = {}
        self.last_discovery = 0.0
        self.running = False
        self.thread = None

    @property
    def enabled(self):
        return bool(self.metric_path)

    @property
    def poll_interval(self):
        if os.getenv("AIUPTI_ENABLE_METRICS", "false").lower() == "true":
            return scanned_fast_poll_interval
        return scanned_default_poll_interval

    def refresh(self):
        """Re-enumerate metric files and release devices that are gone or taken over."""
        if not self.enabled:
            return
        config_owned = self.config_owned_provider() if self.config_owned_provider else {}
        current = discover(self.metric_path)

        added, removed = diff_discovered(self.files, current)
        for busid in removed:
            self.release(busid)
        # keys() & keys() builds a set first, so hand_over() may mutate the dict
        # while this loop runs.
        for busid in self.published.keys() & config_owned.keys():
            self.hand_over(busid, config_owned[busid])
        for busid in added:
            ref = current[busid]
            print(f"scanned metrics: + {ref.path} (busid={busid}, strategy={ref.strategy})")

        self.files = current
        self.last_discovery = time.time()

    def read(self):
        """Read every discovered metric file and update the gauges.

        Only Bus IDs the config-driven collector does not own are published, so
        a device never appears twice with different labels.
        """
        config_owned = self.config_owned_provider() if self.config_owned_provider else {}
        for busid, ref in self.files.items():
            if busid in config_owned:
                continue
            try:
                self.read_one(busid, ref)
            except Exception as e:
                print(f"scanned metrics: read error {ref.path} (busid={busid}): {e}")

    def read_one(self, busid, ref):
        """Publish one metric file on the spyre_* gauges."""
        with MetricFile(ref.path) as metric_file:
            metric_type = "pf" if metric_file.is_old_format else "vf"
            # Record the labels before setting values: if a metric raises halfway
            # through, release() still knows what to remove.
            self.published[busid] = (metric_type, ref.pod_name, ref.pod_namespace)
            for metric, value in metric_file.read_metrics():
                self.set_spyre_gauge(busid, ref, metric, value, metric_type)

    @staticmethod
    def set_spyre_gauge(busid, ref, metric, value, metric_type):
        gauge = spyre_metrics_guages.get(metric.name)
        if gauge is not None:
            gauge.labels(spyre=busid, type=metric_type, pod=ref.pod_name,
                         namespace=ref.pod_namespace, node=node_name).set(value)

    def release(self, busid):
        """Remove every series a Bus ID had set, because its file vanished."""
        published = self.published.pop(busid, None)
        if published is not None:
            self.remove_spyre_labels(busid, *published)
        print(f"scanned metrics: - {busid}")

    def hand_over(self, busid, new_owner_labels):
        """Give up a Bus ID when the config-driven collector takes it over.

        If spyre_map_info uses the same pod and namespace this class used, the
        labels are left in place: removing them would delete the series it is
        now writing.
        """
        published = self.published.pop(busid, None)
        if published is None:
            return
        if new_owner_labels is not None and published[1:] == tuple(new_owner_labels):
            print(f"scanned metrics: {busid} series handed over to the config-driven collector")
        else:
            self.remove_spyre_labels(busid, *published)

    def remove_spyre_labels(self, busid, metric_type, pod_name, pod_namespace):
        for metric_guage in spyre_metrics_guages.keys():
            try:
                spyre_metrics_guages[metric_guage].remove(busid, metric_type, pod_name, pod_namespace, node_name)
            except (KeyError, ValueError) as e:
                print(f"skip removing label {busid}/{metric_type} from {metric_guage}: {e}")

    def start(self):
        if not self.enabled:
            print(f"scanned metrics: {SPYRE_METRIC_PATH_ENV} is not set, discovery disabled")
            return
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.poll_loop, daemon=True, name="ScannedMetrics")
        self.thread.start()
        print(f"scanned metrics: started path={self.metric_path} interval={self.poll_interval}s")

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=5.0)

    def poll_loop(self):
        self.refresh()
        while self.running:
            start = time.time()
            self.read()
            if (time.time() - self.last_discovery) >= rediscovery_interval:
                self.refresh()
            remaining = self.poll_interval - (time.time() - start)
            if remaining > 0:
                time.sleep(remaining)
