 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
import os
import json
import time
from spyremetrics_collector import init_gauges, spyre_map_info, pod_spyre_gauge, spyre_metrics_guages
from pcie import PCIE_GAUGE, get_pcie_metrics, PCIeInfo
from topology import metadata_gauge, info_gauge, connection_info_gauge, parse_spyre_topology
from prometheus_client import start_http_server, Info

interval_time = int(os.getenv('POLL_INTERVAL_SEC', 10))
prom_port = int(os.getenv('MONITOR_PORT', 8000))

gauges = [ ]

config_top_path = "/tmp/spyre-config"
metrics_top_path = "/tmp/spyre-metrics"
topology_file_path = "/tmp/spyre-metadata/topo.json"

# metrics_info_collection is a collection mapping uuid value to spyre_map_info
metrics_info_collection = dict()

def traverse_for_spyre_configs():
    """
    traverse_for_spyre_configs traverses spyre-config folder and initializes spyre_map_info for each uuid (pod).

    If the config file has been deleted, the corresponding info will be also cleared and removed from the collection.
    """
    global metrics_info_collection
    curr_uuids = [f for f in os.listdir(config_top_path) if os.path.isdir(os.path.join(config_top_path, f))]
    for uuid in curr_uuids:
        if uuid not in metrics_info_collection:
            print(f"init spyre_map_info {uuid}")
            metrics_info_collection[uuid] = spyre_map_info(config_top_path, metrics_top_path, uuid)
        metrics_info_collection[uuid].try_load()
    uuids = set(metrics_info_collection.keys())
    for uuid in uuids:
        info = metrics_info_collection[uuid]
        if info.is_deleted():
            print(f"remove deleted info {info.pod_namespace}/{info.pod_name}")
            metrics_info_collection[uuid].clear()
            del metrics_info_collection[uuid]

pcie_info_collection = dict()

def update_pcie():
    """
    update_pcie probes latest pcie information.
    This function updates gauge labels if needed
    and set missing flag for devices which are not discovered.
    """
    global pcie_info_collection
    pcie_metrics = get_pcie_metrics()
    found_list = []
    for key, elem in pcie_metrics.items():
        new_info = PCIeInfo(key, elem)
        found_list.append(key)
        if key not in pcie_info_collection:
            print(f"init pcie_info {key}")
            pcie_info_collection[key] = new_info
        else:
            pcie_info_collection[key].update_if_change(new_info)
    missingList = [key for key in pcie_metrics.keys() if key not in found_list]
    for key in missingList:
        pcie_info_collection[key].set_missing_flag()

def init_prometheus_monitor():
    print("Intitating prometheus monitor...")
    global gauges
    add_metadata_information_to_prometheus()
    init_gauges()
    for gauge in spyre_metrics_guages.values():
        gauges.append(gauge)
    gauges.append(pod_spyre_gauge)
    gauges.append(PCIE_GAUGE)
    gauges.append(info_gauge)
    gauges.append(connection_info_gauge)
    start_http_server(prom_port)

def add_metadata_information_to_prometheus():
    global gauges
    try:
        with open(topology_file_path, 'r') as f:
            data =json.load(f)
    except Exception as e:
        print("Failed to get metadata from topo.json {}, skip".format(e))
        return
    devices = data.get("devices", {})
    gauges.append(metadata_gauge)
    parse_spyre_topology(devices)

if __name__ == "__main__":
    if not os.path.exists(config_top_path):
        print("/data does not exist")
        exit(1)

    init_prometheus_monitor()
    print(f"Exporter serving at :{prom_port}")
    while(1):
        update_pcie()
        traverse_for_spyre_configs()
        time.sleep(10)
