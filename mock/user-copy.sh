#!/bin/bash
# +-------------------------------------------------------------------+
# | (C) Copyright IBM Corp. 2025, 2026                                |
# | SPDX-License-Identifier: Apache-2.0                               |
# +-------------------------------------------------------------------+

mkdir -p /data

if [[ -z ${PCIDEVICE_IBM_COM_AIU_PF} ]]; then
	echo "need to set PCIDEVICE_IBM_COM_AIU_PF"
	exit 1
fi

IFS=',' read -r -a array <<<"$PCIDEVICE_IBM_COM_AIU_PF"

nosplitter=${NO_SPLITTER:-false}

if [[ ${#array[@]} == "0" ]]; then
	echo "no PCIDEVICE_IBM_COM_AIU_PF"
	exit 1
fi

for busid in "${array[@]}"; do
	cp default_metrics /data/metrics.$busid
	((i++))
done
