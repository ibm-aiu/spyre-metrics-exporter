 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
"""Discovery of Spyre metric files written by libaiupti.

The metric file location is described by a path template containing the
``%BUSID`` wildcard, e.g. ``/tmp/spyre-metrics/metrics.%BUSID``.  libaiupti
expands it with the PCI Bus ID of the device it samples, so one exporter can
follow every AIU on the node.

Three strategies are selected from the template and the environment:

A   -- the template has no ``%BUSID``: it names a single metric file.
B1  -- the template has ``%BUSID`` and the device-plugin variables
       (``PCIDEVICE_IBM_COM_AIU_PF`` / ``AIU_WORLD_SIZE``) are present.
       Bus ID expansion is delegated to ``spyremetrics.MetricFile.open_metrics``.
B2  -- the template has ``%BUSID`` without those variables, which is the
       exporter DaemonSet case: the parent directory is scanned.  This finds
       the flat layout (``<prefix><BUSID>``), the per-pod UUID directories the
       device plugin creates (``<uuid>/<prefix><BUSID>``), and the named-subdir
       layout (``<dir>/%BUSID/<filename>``).

Discovery is stateless: :func:`discover` reports the files visible at the
moment it is called, and the caller decides what changed.  Nothing here
imports Prometheus, spawns threads or mutates global state, so the strategies
can be unit tested against a temporary directory.
"""

import os
from dataclasses import dataclass, replace
from typing import Dict, Tuple

BUSID_KEYWORD = "%BUSID"

# Default from the VF metrics design doc, used when the caller passes no path.
# The exporter DaemonSet always sets SPYRE_METRIC_PATH explicitly.
# NOSONAR: this location is read, never written, and its value is fixed by the
# design document and by the device plugin that produces the files.
DEFAULT_METRIC_PATH = "/tmp/metrics.%BUSID"  # NOSONAR

SPYRE_METRIC_PATH_ENV = "SPYRE_METRIC_PATH"
PCIDEVICE_ENV = "PCIDEVICE_IBM_COM_AIU_PF"
WORLD_SIZE_ENV = "AIU_WORLD_SIZE"

POD_NAME_FILE = "POD_NAME"
POD_NAMESPACE_FILE = "POD_NAMESPACE"

# Reported on every MetricFileRef so callers can log how a file was found.
STRATEGY_PLAIN_FILE = "A"
STRATEGY_ENV_TEMPLATE = "B1"
STRATEGY_DIRECTORY_SCAN = "B2"

# Key used for the single file of a template that has no %BUSID (strategy A).
PLAIN_FILE_KEY = "default"


@dataclass(frozen=True)
class MetricFileRef:
    """A metric file found on disk.

    ``pod_name`` / ``pod_namespace`` are filled in from the POD_NAME and
    POD_NAMESPACE files the device plugin writes next to the metric file.  They
    are empty when discovery ran outside a per-pod directory.
    """

    busid: str
    path: str
    pod_name: str = ""
    pod_namespace: str = ""
    strategy: str = ""


def split_busid_path(metric_path: str) -> Tuple[str, str]:
    """Split a path template into ``(parent_dir, filename_template)``.

        /tmp/metrics.%BUSID                    -> ('/tmp', 'metrics.%BUSID')
        /tmp/spyre-metrics/%BUSID/metrics.bin  -> ('/tmp/spyre-metrics', 'metrics.bin')
        /tmp/test_metric.bin                   -> ('/tmp', 'test_metric.bin')
    """
    parts = metric_path.split("/")
    busid_idx = next((i for i, part in enumerate(parts) if BUSID_KEYWORD in part), None)
    if busid_idx is None:
        return os.path.dirname(metric_path), os.path.basename(metric_path)

    parent_dir = "/".join(parts[:busid_idx]) or "/"

    if parts[busid_idx] == BUSID_KEYWORD:
        # <dir>/%BUSID/<filename> -- the Bus ID is a directory component.
        filename_template = "/".join(parts[busid_idx + 1:]) if busid_idx + 1 < len(parts) else ""
    else:
        # <dir>/<prefix>%BUSID -- the Bus ID is part of the file name.
        filename_template = parts[busid_idx]

    return parent_dir, filename_template


def busid_from_path(filepath: str, parent_dir: str, filename_template: str) -> str:
    """Recover the Bus ID from a discovered file path.

        subdir style: /tmp/spyre-metrics/0000:02:00.0/metrics.bin -> '0000:02:00.0'
        flat style:   /tmp/metrics.0000:02:00.0                   -> '0000:02:00.0'
    """
    relative = os.path.relpath(filepath, parent_dir)
    parts = relative.split("/", 1)
    if len(parts) == 2:
        return parts[0]

    prefix = filename_template.replace(BUSID_KEYWORD, "")
    basename = os.path.basename(filepath)
    return basename.replace(prefix, "", 1) if prefix else basename


def read_pod_info(directory: str) -> Tuple[str, str]:
    """Read POD_NAME / POD_NAMESPACE from *directory*, or return empty strings."""

    def _read(filename: str) -> str:
        try:
            with open(os.path.join(directory, filename), "r") as f:
                return f.read().strip()
        except OSError:
            return ""

    return _read(POD_NAME_FILE), _read(POD_NAMESPACE_FILE)


def discover(metric_path: str = None, pcidev: str = None, world_size: str = None) -> Dict[str, MetricFileRef]:
    """Return ``{busid: MetricFileRef}`` for the metric files currently visible.

    ``metric_path`` defaults to SPYRE_METRIC_PATH, then to DEFAULT_METRIC_PATH.
    ``pcidev`` / ``world_size`` default to the device-plugin environment
    variables and decide between strategy B1 and B2.
    """
    path = metric_path if metric_path is not None else os.getenv(SPYRE_METRIC_PATH_ENV, DEFAULT_METRIC_PATH)
    if not path:
        return {}

    if BUSID_KEYWORD not in path:
        return _discover_plain_file(path)

    pcidev = os.getenv(PCIDEVICE_ENV, "") if pcidev is None else pcidev
    world_size = os.getenv(WORLD_SIZE_ENV, "") if world_size is None else world_size
    if pcidev or world_size:
        found = _discover_from_env(path)
        if found:
            return found

    return _scan_directory(path)


def diff_discovered(previous: Dict[str, MetricFileRef],
                    current: Dict[str, MetricFileRef]) -> Tuple[list, list]:
    """Return ``(added, removed)`` Bus IDs between two discovery results."""
    previous = previous or {}
    current = current or {}
    added = [busid for busid in current if busid not in previous]
    removed = [busid for busid in previous if busid not in current]
    return added, removed


def _discover_plain_file(metric_path: str) -> Dict[str, MetricFileRef]:
    """Strategy A: the path names one file, with no Bus ID substitution."""
    if not os.path.isfile(metric_path):
        return {}
    return {
        PLAIN_FILE_KEY: MetricFileRef(
            busid=PLAIN_FILE_KEY,
            path=metric_path,
            strategy=STRATEGY_PLAIN_FILE,
        )
    }


def _discover_from_env(metric_path: str) -> Dict[str, MetricFileRef]:
    """Strategy B1: let spyremetrics expand %BUSID from the device-plugin env."""
    try:
        from spyremetrics import MetricFile

        opened = MetricFile.open_metrics(metric_path)
    except Exception as exc:
        print(f"metric discovery (B1): {exc}")
        return {}

    parent_dir, filename_template = split_busid_path(metric_path)
    found: Dict[str, MetricFileRef] = {}
    for metric_file in opened:
        filepath = str(metric_file.path)
        if not os.path.isfile(filepath):
            continue
        busid = busid_from_path(filepath, parent_dir, filename_template)
        if not busid or busid in found:
            continue
        pod_name, pod_namespace = read_pod_info(os.path.dirname(filepath))
        found[busid] = MetricFileRef(
            busid=busid,
            path=filepath,
            pod_name=pod_name,
            pod_namespace=pod_namespace,
            strategy=STRATEGY_ENV_TEMPLATE,
        )
    return found


def _scan_directory(metric_path: str) -> Dict[str, MetricFileRef]:
    """Strategy B2: walk the parent directory and match file names to the template."""
    parent_dir, filename_template = split_busid_path(metric_path)

    if not os.path.isdir(parent_dir):
        return {}

    try:
        entries = sorted(os.listdir(parent_dir))
    except OSError as exc:
        print(f"metric discovery (B2): cannot list {parent_dir}: {exc}")
        return {}

    if filename_template and BUSID_KEYWORD not in filename_template:
        return _scan_named_subdirs(parent_dir, entries, filename_template)
    return _scan_flat_and_uuid_subdirs(parent_dir, entries, filename_template)


def _scan_named_subdirs(parent_dir: str, entries: list, filename_template: str) -> Dict[str, MetricFileRef]:
    """Layout ``<parent>/%BUSID/<filename>`` -- the Bus ID is the directory name."""
    found: Dict[str, MetricFileRef] = {}
    for entry in entries:
        entry_path = os.path.join(parent_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        candidate = os.path.join(entry_path, filename_template)
        if not os.path.isfile(candidate):
            continue
        pod_name, pod_namespace = read_pod_info(entry_path)
        found[entry] = MetricFileRef(
            busid=entry,
            path=candidate,
            pod_name=pod_name,
            pod_namespace=pod_namespace,
            strategy=STRATEGY_DIRECTORY_SCAN,
        )
    return found


def _scan_flat_and_uuid_subdirs(parent_dir: str, entries: list, filename_template: str) -> Dict[str, MetricFileRef]:
    """Layout ``<prefix><BUSID>`` directly in parent, or one UUID directory below it."""
    prefix = filename_template.replace(BUSID_KEYWORD, "")

    found: Dict[str, MetricFileRef] = {}
    uuid_dirs = []
    for entry in entries:
        entry_path = os.path.join(parent_dir, entry)
        ref = _flat_file_ref(entry_path, entry, prefix)
        if ref is not None:
            found[ref.busid] = ref
        elif os.path.isdir(entry_path):
            uuid_dirs.append(entry_path)

    # UUID directories are visited after the flat files so that a flat file wins
    # when both layouts expose the same Bus ID, whatever order listdir returned.
    for uuid_dir in uuid_dirs:
        for ref in _uuid_subdir_refs(uuid_dir, prefix):
            found.setdefault(ref.busid, ref)

    return found


def _flat_file_ref(entry_path: str, name: str, prefix: str):
    """Return a MetricFileRef when *name* is a flat ``<prefix><BUSID>`` file."""
    if not name.startswith(prefix) or not os.path.isfile(entry_path):
        return None
    busid = name[len(prefix):]
    if not busid:
        return None
    return MetricFileRef(busid=busid, path=entry_path, strategy=STRATEGY_DIRECTORY_SCAN)


def _uuid_subdir_refs(directory: str, prefix: str) -> list:
    """Return refs for the metric files inside a per-pod UUID directory."""
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return []

    pod_name, pod_namespace = read_pod_info(directory)
    refs = []
    for entry in entries:
        ref = _flat_file_ref(os.path.join(directory, entry), entry, prefix)
        if ref is not None:
            refs.append(replace(ref, pod_name=pod_name, pod_namespace=pod_namespace))
    return refs
