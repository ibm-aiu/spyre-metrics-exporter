 # +-------------------------------------------------------------------+
 # | (C) Copyright IBM Corp. 2025, 2026                                |
 # | SPDX-License-Identifier: Apache-2.0                               |
 # +-------------------------------------------------------------------+
ARG BASE_UBI_IMAGE_TAG=9.6
ARG PYTHON_VERSION=3.12
ARG SENLIB_INSTALL_DIR="/opt/ibm/spyre/senlib"

FROM registry.access.redhat.com/ubi9/ubi-minimal:${BASE_UBI_IMAGE_TAG}

ARG PYTHON_VERSION
ARG VERSION
ARG SENLIB_INSTALL_DIR

ENV LANG=C.UTF-8 \
	LC_ALL=C.UTF-8 \
	LD_LIBRARY_PATH="${SENLIB_INSTALL_DIR}/lib" \
	PYTHONPATH="${SENLIB_INSTALL_DIR}/lib"

RUN microdnf install -y \
		pciutils jq \
		libicu \
		python${PYTHON_VERSION} && \
	microdnf -y upgrade && \
	update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 0 && \
	update-alternatives --install /usr/bin/python python /usr/bin/python${PYTHON_VERSION} 0 && \
	update-alternatives --set python3 /usr/bin/python${PYTHON_VERSION} && \
	update-alternatives --set python /usr/bin/python${PYTHON_VERSION} && \
	microdnf clean all

LABEL io.k8s.display-name="IBM Spyre Metrics Exporter Container"
LABEL name="IBM Spyre Metrics Exporter Container"
LABEL vendor="IBM"
LABEL version="${VERSION}"
LABEL release="N/A"
LABEL summary="Automate the monitoring of IBM Spyre devices."
LABEL description="See summary"

ENV PATH=${SENLIB_INSTALL_DIR}/bin:$VIRTUAL_ENV/bin:$PATH
ENV LD_LIBRARY_PATH=${SENLIB_INSTALL_DIR}/lib:${LD_LIBRARY_PATH}
ENV PYTHONPATH=${SENLIB_INSTALL_DIR}/lib${PYTHONPATH:+:${PYTHONPATH}}
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Install required dependencies for building numpy & psutil from source on Power (ppc64le) and Z (s390x) architectures
RUN if [ "$(uname -m)" != "x86_64" ]; then \
    microdnf install -y gcc gcc-c++ glibc-devel make python-devel python${PYTHON_VERSION}-devel && \
    microdnf clean all; \
    fi

WORKDIR /workspace
COPY requirements.txt /workspace/requirements.txt

# Setup virtual environment outside of senlib/etc so that etc/ can be bind-mounted freely
ENV VIRTUAL_ENV=/opt/ibm/spyre/venv
RUN python${PYTHON_VERSION} -m venv ${VIRTUAL_ENV} && python${PYTHON_VERSION} -m ensurepip --upgrade && pip${PYTHON_VERSION} install --upgrade pip wheel
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install requirements
RUN pip${PYTHON_VERSION} install --upgrade pip && \
    pip${PYTHON_VERSION} install -r /workspace/requirements.txt --require-virtualenv --no-cache-dir && \
    pip${PYTHON_VERSION} list -v

VOLUME /etc/aiu
COPY src .
COPY mock/default_metrics .
COPY ./LICENSE /licenses/LICENSE

# Switch to non-root user (UID 1001) with root group (GID 0)
# Just set permissions without creating a named user
RUN chown -R 1001:0 /opt/ibm/spyre/venv \
    && chmod -R g+rw /opt/ibm/spyre/venv

USER 1001:0

EXPOSE 8000

ENTRYPOINT ["python3", "-u", "./exporter.py"]
HEALTHCHECK NONE
