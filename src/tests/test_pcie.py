 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
import os
import pcie
from pathlib import Path


def load_test_data(filename):
    path = Path(__file__).resolve().parent
    path = path / filename
    with open(path) as f:
        output = f.read()

    return output


def test_get_spyre_devices():
    # Load fake lspci data from file
    filename = "data/lspci_z.txt"
    pci_data = load_test_data(filename)
    devices = pcie.get_spyre_devices(pci_data)
    body_length = len(devices.get("0007:00:00.0"))
    assert len(devices) == 1
    assert body_length == 211

    filename = "data/lspci_amd64.txt"
    pci_data = load_test_data(filename)
    devices = pcie.get_spyre_devices(pci_data)
    assert len(devices) == 2

    filename = "data/lspci_ppc64le.txt"
    pci_data = load_test_data(filename)
    devices = pcie.get_spyre_devices(pci_data)
    assert len(devices) == 3

def test_get_devctl_metrics():
    filename = "data/lspci_z.txt"
    pci_data = load_test_data(filename)
    devices = pcie.get_spyre_devices(pci_data)
    result = pcie.get_devctl_metrics(devices.get("0007:00:00.0"))
    assert result is not None


def test_get_lnksta_metrics():
    filename = "data/lspci_z.txt"
    pci_data = load_test_data(filename)
    devices = pcie.get_spyre_devices(pci_data)
    result = pcie.get_lnksta_metrics(devices.get("0007:00:00.0"))
    assert result is not None


def test_get_metrics(monkeypatch):
    data_file = os.path.join(os.path.dirname(__file__), "data", "lspci_ppc64le.txt")
    with open(data_file) as f:
        fake_output = f.read()

    # Replace get_pci_output with a fake that returns our test data
    monkeypatch.setattr(pcie, "get_pci_output", lambda: fake_output)

    metrics = pcie.get_pcie_metrics()
    print("metrics:")
    print(metrics)
    assert len(metrics) == 3
    assert len(metrics["0483:70:00.0"]) == 4


def test_get_metrics_lspci_failed(monkeypatch):
    # Replace get_pci_output with a fake that returns empty
    monkeypatch.setattr(pcie, "get_pci_output", lambda: "")
    metrics = pcie.get_pcie_metrics()
    assert len(metrics) == 0


def test_get_missing_metrics(monkeypatch):
    data_file = os.path.join(os.path.dirname(__file__), "data", "lspci_missing_data.txt")
    with open(data_file) as f:
        fake_output = f.read()

    # Replace get_pci_output with a fake that returns our test data
    monkeypatch.setattr(pcie, "get_pci_output", lambda: fake_output)

    metrics = pcie.get_pcie_metrics()
    key = next(iter(metrics))
    assert len(metrics[key]) == 4

    for metric, value in metrics[key].items():
        print(f"{metric}: {value}")
        assert value == "NA"
