 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
from prometheus_client import Gauge
import os

node_name = os.getenv('NODE_NAME', "unknown")

# addr=0000:29:00.0, numanode=0, name=SPYRE Device 06a7 (rev 02), linkspeed=63.02 GB/s
info_gauge = Gauge('spyre_info_device', 'Spyre device information', ['node', 'addr', 'numanode', 'name', 'linkspeed'])
# addr=0000:29:00.0, SOC_Clock=560.0 MHz, RPD_Clock=800.0 MHz, Mem=Samsung, DDR_Speed=6400.0 Mbps, DRAM_Freq=800 MHz, DDR_Avail=112.0 GB, Boost=False, SerialID=B3e3202
metadata_gauge = Gauge('spyre_info_metadata', 'Spyre device metadata information', ['node', 'addr', 'SOC_Clock', 'RPD_Clock', 'Mem', 'DDR_Speed', 'DRAM_Freq', 'DDR_Avail', 'Boost', 'SerialID'])
# src=0000:29:00.0 dst=0000:2a:00.0 numa=0 protocol=P2PDMA
# src=0000:29:00.0 dst=0000:3a:00.0 numa=0 protocol=v_P2PDMA
# src=0000:29:00.0 dst=0000:aa:00.0 numa=0 protocol=HDMA
connection_info_gauge = Gauge('spyre_info_connection_protocol', 'Spyre device connection information', ['node', 'src_addr', 'dst_addr', 'protocol'])

def _resolve_protocol(src_pci, dst_pci, protocols):
    """Return the protocol label string for a src/dst pair."""
    if protocols is None:
        print(f"WARNING: protocol info for src={src_pci} dst={dst_pci} is None; "
              "treating as unknown protocol")
        protocols = ""
    if "P2PDMA" in protocols:
        return "P2PDMA"
    if "v_P2PDMA" in protocols:
        return "v_P2PDMA"
    if "HDMA" in protocols:
        return "HDMA"
    return "NA"


def _record_connections(src_pci, src_protocols, processed_pairs):
    """Emit connection_info_gauge for each unprocessed (src, dst) pair."""
    for dst_pci, protocols in src_protocols.items():
        pair = tuple(sorted([src_pci, dst_pci]))
        if pair in processed_pairs:
            continue
        processed_pairs.add(pair)
        protocol = _resolve_protocol(src_pci, dst_pci, protocols)
        connection_info_gauge.labels(
            node=node_name, src_addr=src_pci, dst_addr=dst_pci, protocol=protocol
        ).set(1)


def _record_device(src_pci, src_info, processed_pairs):
    """Emit info/metadata/connection gauges for a single SPYRE/AIU device."""
    name = src_info.get("name") or ""
    if "SPYRE" not in name.upper() and "AIU" not in name.upper():
        return  # skip non-SPYRE or non-AIU devices

    src_numa = src_info.get("numanode", "-1")
    src_linkspeed = src_info.get("linkspeed", "NA")

    src_protocols = src_info.get("protocol") or {}
    if src_protocols:
        _record_connections(src_pci, src_protocols, processed_pairs)

    src_metadata = src_info.get("metadata") or {}
    soc_clock = src_metadata.get("Clocks", {}).get("SOC", "NA")
    rpd_clock = src_metadata.get("Clocks", {}).get("RPD", "NA")
    boost = src_metadata.get("Memory", {}).get("Boostable", "NA")
    dram_freq = src_metadata.get("Memory", {}).get("Freq", "NA")
    ddr_speed = src_metadata.get("Memory", {}).get("Speed", "NA")
    ddr_avail = src_metadata.get("Memory", {}).get("Size", "NA")
    mem = src_metadata.get("Memory", {}).get("Make", "NA")
    serial_id = src_metadata.get("SerialID", "NA")

    metadata_gauge.labels(
        node=node_name, addr=src_pci,
        SOC_Clock=soc_clock, RPD_Clock=rpd_clock, Mem=mem,
        DDR_Speed=ddr_speed, DRAM_Freq=dram_freq, DDR_Avail=ddr_avail,
        Boost=boost, SerialID=serial_id,
    ).set(1)
    info_gauge.labels(
        node=node_name, addr=src_pci, numanode=src_numa, name=name, linkspeed=src_linkspeed
    ).set(1)


def parse_spyre_topology(devices):
    if devices is None:
        print("WARNING: parse_spyre_topology got devices=None "
              "(topo.json may have an explicit \"devices\": null, or device discovery returned nothing); "
              "skipping topology metrics for this cycle")
        return
    if not devices:
        return
    processed_pairs = set()
    for src_pci, src_info in devices.items():
        if src_info is None:
            print(f"WARNING: device entry for {src_pci} is None in topo.json; skipping this device")
            continue
        _record_device(src_pci, src_info, processed_pairs)
