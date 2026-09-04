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

def parse_spyre_topology(devices):
    processed_pairs = set()
    for src_pci, src_info in devices.items():
        name = src_info.get("name", "")
        if "SPYRE" not in name.upper():
            continue  # skip non-SPYRE devices

        src_numa = src_info.get("numanode", "-1")
        src_linkspeed = src_info.get("linkspeed", "NA")
        src_protocols = src_info.get("protocol", {})
        if src_protocols:
            for dst_pci, protocols in src_protocols.items():
                if "P2PDMA" in protocols:
                    protocol = "P2PDMA"
                elif "v_P2PDMA" in protocols:
                    protocol = "v_P2PDMA"
                elif "HDMA" in protocols:
                    protocol = "HDMA"
                else:
                    protocol = "NA"

                pair = tuple(sorted([src_pci, dst_pci]))
                if pair not in processed_pairs:
                    processed_pairs.add(pair)
                    connection_info_gauge.labels(node=node_name, src_addr=src_pci, dst_addr=dst_pci, protocol=protocol).set(1)
        src_metadata = src_info.get("metadata", {})
        if src_metadata is None:
            src_metadata = {}
        SOC_Clock = src_metadata.get("Clocks", {}).get("SOC", "NA")
        RPD_Clock = src_metadata.get("Clocks", {}).get("RPD", "NA")
        Boost = src_metadata.get("Memory", {}).get("Boostable", "NA")
        DRAM_Freq = src_metadata.get("Memory", {}).get("Freq", "NA")
        DDR_Speed = src_metadata.get("Memory", {}).get("Speed", "NA")
        DDR_Avail = src_metadata.get("Memory", {}).get("Size", "NA")
        Mem = src_metadata.get("Memory", {}).get("Make", "NA")
        SerialID = src_metadata.get("SerialID", "NA")

        metadata_gauge.labels(node=node_name, addr=src_pci, SOC_Clock=SOC_Clock, RPD_Clock=RPD_Clock, Mem=Mem, DDR_Speed=DDR_Speed, DRAM_Freq=DRAM_Freq, DDR_Avail=DDR_Avail, Boost=Boost, SerialID=SerialID).set(1)
        info_gauge.labels(node=node_name, addr=src_pci, numanode=src_numa, name=name, linkspeed=src_linkspeed).set(1)
