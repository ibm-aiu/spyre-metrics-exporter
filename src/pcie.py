 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
import subprocess
from subprocess import TimeoutExpired
from prometheus_client import Gauge
import re
import os

# Regular expression to parse *lspci* output device and body.
SPYRE_LSPCI_HEADER_RX = re.compile(
    r"""
    ^                               # Start of line
    (?P<device_id>
       (?:[\d\w]+:)?                    # Optional domain
       [\d\w]{2}:[\d\w]{2}\.\d              # bus:device.function
    )
    .*?                             # Other chars
    06a7                            # Card identifier
    .*?\n                           # Extra stuff then newline
    (?P<body>.*?                    # Get entire body
       (?:^[ \t].+\n)*
    )
    (?:\n|\Z)                       # empty line or EOF
""",
    re.VERBOSE | re.MULTILINE,
)


# Regular expression to parse *lspci* DevCtl metrics
SPYRE_DEVCTL_METRICS_RX = re.compile(
    r"""
    ^\s+DevCtl:.*\n                                    # Start of DevCtl
    (?:^\s+.*\n)*?                                     # Arbitrary useless 2nd line
    ^\s+MaxPayload\s+(?P<maxpayload>\d+)\s+bytes,      # group: maxpayload
    \s+MaxReadReq\s+(?P<maxreadreq>\d+)\s+bytes\n      # group: maxreadreq
""",
    re.VERBOSE | re.MULTILINE,
)

# Regular expression to parse *lspci* LnkSta metrics
SPYRE_LNKSTA_METRICS_RX = re.compile(
    r"""
    ^\s+LnkSta:\s+                            # Start of LnkSta
    Speed\s+(?P<speed>\d+GT/s)\s\(\w+\),\s+   # group: speed
    Width\s+(?P<width>x\d+\s\(\w+\))\n        # group: width
""",
    re.VERBOSE | re.MULTILINE,
)


PCIE_GAUGE = Gauge(
    "spyre_info_pci_cfg",
    "PCIe information of Spyre",
    ["node", "addr", "MaxPayload", "MaxReadReq", "Speed", "Width"],
)

class PCIeInfo:
    """
    pcie_info contains each PCIe information to check when the labels should be updated.
    """

    def __init__(self, key, elem):
        pcie_addr = "0000:" + key
        self.key = key
        self.addr = pcie_addr
        self.maxpayload = elem["maxpayload"]
        self.maxreadreq = elem["maxreadreq"]
        self.node_name = os.getenv("NODE_NAME", "unknown")
        self.speed = elem["speed"]
        self.width = elem["width"]
        self.label()

    def update_if_change(self, new_info):
        if self.key != new_info.key:
            # different key, cannot compare
            return None
        if (
            self.missing
            or self.maxpayload != new_info.maxpayload
            or self.maxreadreq != new_info.maxreadreq
            or self.speed != new_info.speed
            or self.width != new_info.width
        ):
            self.clear()
            self.maxpayload = new_info.maxpayload
            self.maxreadreq = new_info.maxreadreq
            self.speed = new_info.speed
            self.width = new_info.width
            self.label()

    def label(self):
        PCIE_GAUGE.labels(
            node=self.node_name,
            addr=self.addr,
            MaxPayload=self.maxpayload,
            MaxReadReq=self.maxreadreq,
            Speed=self.speed,
            Width=self.width,
        ).set(1)
        self.missing = False

    def clear(self):
        PCIE_GAUGE.remove(
            self.node_name,
            self.addr,
            self.maxpayload,
            self.maxreadreq,
            self.speed,
            self.width,
        )

    def set_missing_flag(self):
        PCIE_GAUGE.labels(
            node=self.node_name,
            addr=self.addr,
            MaxPayload=self.maxpayload,
            MaxReadReq=self.maxreadreq,
            Speed=self.speed,
            Width=self.width,
        ).set(0)
        self.missing = True


def get_pci_output(timeout_seconds: int = 10) -> str:
    """Use 'lspci -vvvnn' to get Spyre card raw info

    Output return stdout result of lspci subprocess.run if success. Otherwise, return ""
    """
    try:
        result = subprocess.run(
            ["lspci", "-nnvvv"],
            timeout=timeout_seconds,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return ""
        return result.stdout
    except TimeoutExpired:
        return ""
    except:
        return ""


def get_spyre_devices(text: str) -> dict[str, str]:
    """Use the custom regex to parse lspci data into a dictionary:

    Output should be a dictionary that maps: device_id -> device_body
    and it should capture all devices output by lspci. This makes it fairly
    easy to process the body later into metrics.
    """
    devices = {}
    matches = SPYRE_LSPCI_HEADER_RX.finditer(text)

    for match in matches:
        if match:
            devices[match.group("device_id")] = match.group("body")

    return devices


def get_devctl_metrics(text: str) -> dict[str, str]:
    """Get DevCtl Metrics from lspci body. The get_spyre_devices() func provides
    a lspci device body parsed to extract the DevCtl using a custom regex.

    Input Parameters:
        text (str): Input lspci body text

    Returns:
        dict[str, str]: {"maxpayload": str, "maxredreq": str}

    """
    match = SPYRE_DEVCTL_METRICS_RX.search(text)
    result: dict[str, str] = {}

    if not match:
        result.update({"maxpayload": "NA"})
        result.update({"maxreadreq": "NA"})
    else:
        result.update({"maxpayload": match.group("maxpayload")})
        result.update({"maxreadreq": match.group("maxreadreq")})

    return result


def get_lnksta_metrics(text: str) -> dict[str, str]:
    """Get LnkSta Metrics from lspci body. The get_spyre_devices() func provides
    a lspci device body parsed to extract the LnkSta using a custom regex.

    Input Parameters:
        text (str): Input lspci body text

    Returns:
        dict[str, str]: {"speed": str, "width": str}

    """
    match = SPYRE_LNKSTA_METRICS_RX.search(text)
    result: dict[str, str] = {}

    if not match:
        result.update({"speed": "NA"})
        result.update({"width": "NA"})
    else:
        result.update({"speed": match.group("speed")})
        result.update({"width": match.group("width")})

    return result


def get_pcie_metrics() -> dict[str, dict[str, str]]:
    """Extract the final pcie metrics needed by the metrics-exporter.
    Leverages get_spyre_devices(), get_devctl_metrics(), and get_lnksta_metrics()
    to create the final metrics dictionary.

    Returns:
        dict[device_id: str, dict[metric_name: str, metric_value: str]

        Example:
        {'0483:70:00.0':
           {'maxpayload': '512', 'maxreadreq': '4096', 'speed': '16GT/s', 'width': 'x16 (ok)'},
           ...
        }

    """
    lspci_data = get_pci_output()
    if lspci_data == "":
        # failed to execute lspci
        return {}
    devices = get_spyre_devices(lspci_data)
    metrics = {}
    for device in devices:
        tmp = {}
        body = devices.get(device)
        if body:
            tmp.update(get_devctl_metrics(body))
            tmp.update(get_lnksta_metrics(body))
            metrics.update({device: tmp})

    return metrics
