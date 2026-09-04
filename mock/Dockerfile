 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
FROM registry.access.redhat.com/ubi9-minimal:9.6

WORKDIR /workspace
RUN microdnf -y update && microdnf install -y jq && microdnf clean all
COPY ./mock/default_senlib_config.json default_senlib_config.json
COPY ./mock/default_metrics default_metrics
COPY ./mock/user-copy.sh .
RUN chmod +x /workspace/user-copy.sh

ENTRYPOINT [ "/bin/bash", "-c" ]
HEALTHCHECK NONE
