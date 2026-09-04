 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
from typing import Any
import os
import json
import numpy as np

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
