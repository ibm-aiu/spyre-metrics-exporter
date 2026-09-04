# +-------------------------------------------------------------------+
# | (C) Copyright IBM Corp. 2025, 2026                                |
# | SPDX-License-Identifier: Apache-2.0                               |
# +-------------------------------------------------------------------+

# Enable automatic Go toolchain management
export GOTOOLCHAIN = auto

GOLANG_VERSION		?= $(shell cd $(REPO_ROOT) && go list -f {{.GoVersion}} -m)
MAKEFILE_PATH		:= $(abspath $(lastword $(MAKEFILE_LIST)))
REPO_ROOT			:= $(abspath $(patsubst %/,%,$(dir $(MAKEFILE_PATH))))
CURRENT_DIR			:= $(shell pwd)
VERSION				?= $(shell cat $(REPO_ROOT)/VERSION)
REGISTRY			?= docker.io/spyre-operator
DOCKER				?= $(shell command -v podman 2> /dev/null || echo docker)
DOCKERFILE			= $(REPO_ROOT)/Dockerfile
DOCKER_BUILD_OPTS	?= --progress=plain

IMAGE_TAG 			?= $(VERSION)

IMAGE_NAME 			:= $(REGISTRY)/spyre-metric-exporter
IMAGE 				?= $(IMAGE_NAME):$(IMAGE_TAG)

MOCK_IMAGE_NAME		:= $(REGISTRY)/spyre-metric-mock-user
MOCK_USER_IMG		?= $(MOCK_IMAGE_NAME):$(IMAGE_TAG)

TEST_IMG			?= $(IMAGE_NAME):dev
CODECOV_PERCENT		?= 45
GOCOVERDIR			?= $(REPO_ROOT)

KUBECTL				?= $(shell command -v oc 2> /dev/null || echo kubectl)
OC					?= $(shell command -v oc)

# Operating system
OS					?= $(shell go env GOOS)
ARCH				?= $(shell go env GOARCH)
# Architectures whose per-arch image tags ($(IMAGE)-<arch>) are combined into the manifest.

# Setting SHELL to bash allows bash commands to be executed by recipes.
# Options are set to exit when a recipe line exits non-zero or a piped command fails.
SHELL = /usr/bin/env bash -o pipefail
.SHELLFLAGS = -ec

LOCALBIN ?= $(shell pwd)/bin
$(LOCALBIN):
	mkdir -p $(LOCALBIN)

DOCKER_GO_BUILD_FLAGS ?= -race

## Tool Binaries
YQ			?= $(LOCALBIN)/yq
PYTHON      ?= python3
PIP         ?= pip3

# detect-secrets
DETECT_SECRETS_GIT ?= "https://github.com/ibm/detect-secrets.git@master\#egg=detect-secrets"

## Tool Versions
YQ_VERSION	?= v4.29.2

##@ Development tools

.PHONY: yq
yq: $(YQ) ## Download yq locally if necessary.
$(YQ): $(LOCALBIN)
	test -s $(YQ) || GOBIN=$(LOCALBIN) go install github.com/mikefarah/yq/v4@$(YQ_VERSION)

##@ Local test

.PHONY: test
test: docker-build exporter-test ## Run test with plain podman/docker run

##@ Image operations

.PHONY: docker-build
docker-build: vendor ## Build spyre device plugin init image for build host architecture
	$(DOCKER) build $(DOCKER_BUILD_OPTS) --pull \
	--tag $(IMAGE) \
	--build-arg VERSION="$(VERSION)" \
	--build-arg BUILD_FLAGS="$(DOCKER_GO_BUILD_FLAGS)" \
	--file $(DOCKERFILE) $(CURRENT_DIR)

.PHONY: docker-push
docker-push: ## Push spyre device plugin init image for the build host architecture.
	$(DOCKER) push $(IMAGE)

.PHONY: docker-build-push
docker-build-push: docker-build docker-push ## Build and push the spyre device plugin init image for the build host

.PHONY: docker-mock-user-build
docker-mock-user-build: vendor ## Build spyre device plugin init image for build host architecture
	podman build $(DOCKER_BUILD_OPTS) --pull \
		--tag $(MOCK_USER_IMG) \
		--file $(REPO_ROOT)/mock/Dockerfile .

.PHONY: docker-mock-user-push
	$(DOCKER) push $(MOCK_USER_IMG)

# ------------------------------------------------------------------------------------
# Python Tests
# require: pip install mypy pytest
# ------------------------------------------------------------------------------------

.PHONY: python-test
python-test:
	mypy src/pcie.py
	pytest --verbose

pyclean:
	@find . -name .mypy_cache -exec rm -rf {} +
	@find src -name __pycache__ -exec rm -rf {} +

# ------------------------------------------------------------------------------------
# Mock User Tests
# ------------------------------------------------------------------------------------

run-single-spyre-exporter: ## Run SPYRE exporter with single-SPYRE pods
	$(DOCKER) run --pull=missing -u root --name single-spyre-exporter -d \
	-e NODE_NAME=node1 -p 8000:8000 \
	-v ./mock/single-spyre-device-plugins/spyre-config:/tmp/spyre-config:Z \
	-v ./mock/single-spyre-device-plugins/spyre-metrics:/tmp/spyre-metrics:Z \
	-v ./mock/single-spyre-device-plugins/metadata:/tmp/spyre-metadata:Z \
	$(IMAGE)
	@echo "Waiting for single-spyre-exporter to be ready..."; \
	for i in $$(seq 1 30); do curl -sf localhost:8000/metrics > /dev/null && break || sleep 5; done

# Need to run the container as root for the Jenkins user to access the mounted files
run-multi-spyre-exporter: ## Run SPYRE exporter with multi-SPYRE pods
	$(DOCKER) run --pull=missing -u root --name multi-spyre-exporter -d \
	-e NODE_NAME=node1 -p 8001:8000 \
	-v ./mock/multi-spyre-device-plugins/spyre-config:/tmp/spyre-config:Z \
	-v ./mock/multi-spyre-device-plugins/spyre-metrics:/tmp/spyre-metrics:Z \
	-v ./mock/multi-spyre-device-plugins/metadata:/tmp/spyre-metadata:Z \
	$(IMAGE)
	@echo "Waiting for multi-spyre-exporter to be ready..."; \
	for i in $$(seq 1 30); do curl -sf localhost:8001/metrics > /dev/null && break || sleep 5; done

single-spyre-mock-user: ## Run Single SPYRE User
	$(DOCKER) run --pull=missing --name single-spyre-user -d -e PCIDEVICE_IBM_COM_SPYRE_PF=001 $(MOCK_USER_IMG) "./user-copy.sh && sleep infinity"

multi-spyre-mock-user: ## Run Multi-SPYRE User
	$(DOCKER) run --pull=missing --name multi-spyre-user -d -e PCIDEVICE_IBM_COM_SPYRE_PF=001,002 $(MOCK_USER_IMG) "./user-copy.sh && sleep infinity"

multi-spyre-user-no-splitter: ## Run Multi-SPYRE User without splitter
	$(DOCKER) run --pull=missing --name multi-spyre-user-no-splitter  -d -eNO_SPLITTER=true -e PCIDEVICE_IBM_COM_SPYRE_PF=001,002 $(MOCK_USER_IMG) "./user-copy.sh && sleep infinity"

mock-new-pod:
	$(DOCKER) cp ./mock/new-pod/spyre-config/4  single-spyre-exporter:/tmp/spyre-config/4
	$(DOCKER) cp ./mock/new-pod/spyre-metrics/4  single-spyre-exporter:/tmp/spyre-metrics/4

mock-delete-new-pod:
	$(DOCKER) exec single-spyre-exporter /bin/bash -c "rm -r /tmp/spyre-config/4 /tmp/spyre-metrics/4"

exporter-test: run-single-spyre-exporter run-multi-spyre-exporter  ## Unit test for SPYRE exporter
	@echo "collect metrics"
	@curl -s localhost:8000/metrics|grep -q 'spyre_info_metadata{Boost="False",DDR_Avail="112.0 GB",DDR_Speed="6400.0 Mbps",DRAM_Freq="800 MHz",Mem="Samsung",RPD_Clock="800.0 MHz",SOC_Clock="560.0 MHz",SerialID="B3e3205",addr="0000:ba:00.0",node="node1"}' || (echo "Failed to get spyre_info_metadata"; $(MAKE) clean-exit)
	@curl -s localhost:8000/metrics|grep -q 'spyre_info_device{addr="0000:29:00.0",linkspeed="63.02 GB/s",name="SPYRE Device 06a7 (rev 02)",node="node1",numanode="0"}' || (echo "Failed to get spyre_info_device"; $(MAKE) clean-exit)
	@curl -s localhost:8000/metrics|grep -q 'spyre_info_connection_protocol{dst_addr="0000:2a:00.0",node="node1",protocol="P2PDMA",src_addr="0000:29:00.0"}' || (echo "Failed to get spyre_info_connection_protocol"; $(MAKE) clean-exit)
	@echo "METADATA/INFO/DEVICE_CONNECTION_INFO Labels: Passed"
	@curl -s localhost:8000/metrics|grep -q 'spyre_allocation{namespace="default",node="node1",pod="pod-1",spyre="01.1"}'|| (echo "Failed to get spyre_allocation"; $(MAKE) clean-exit)
	@echo "Pod Allocation Label: Passed"
	@curl -s localhost:8000/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-1",spyre="01.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 01"; $(MAKE) clean-exit)
	@curl -s localhost:8000/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-2",spyre="02.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 02"; $(MAKE) clean-exit)
	@echo "Single-SPYRE: Passed"
	@curl -s localhost:8001/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-1",spyre="01.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 01"; $(MAKE) clean-exit)
	@curl -s localhost:8001/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-1",spyre="02.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 02"; $(MAKE) clean-exit)
	@curl -s localhost:8001/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-2",spyre="03.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 03"; $(MAKE) clean-exit)
	@curl -s localhost:8001/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-2",spyre="04.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 04"; $(MAKE) clean-exit)
	@curl -s localhost:8001/metrics|grep -q 'spyre_allocation{namespace="default",node="node1",pod="pod-3",spyre="05.1"}'|| (echo "Failed to get spyre_allocation of pod-3"; $(MAKE) clean-exit)
	@curl -s localhost:8001/metrics|grep -q 'spyre_allocation{namespace="default",node="node1",pod="pod-3",spyre="06.1"}'|| (echo "Failed to get spyre_allocation of pod-3"; $(MAKE) clean-exit)
	@echo "Multi-SPYRE: Passed"
	@$(MAKE) mock-new-pod && sleep 10
	@curl -s localhost:8000/metrics|grep -c 'spyre_power_watts{'|grep -q 3||(echo "Failed to add new pod"; $(MAKE) clean-exit)
	@curl -s localhost:8000/metrics|grep -q 'spyre_power_watts{namespace="default",node="node1",pod="pod-4",spyre="07.1",type="vf"}'|| (echo "Failed to get spyre_power_watts 01 of new pod"; $(MAKE) clean-exit)
	@curl -s localhost:8000/metrics|grep -q 'spyre_allocation{namespace="default",node="node1",pod="pod-4",spyre="07.1"}'|| (echo "Failed to get spyre_allocation of new pod"; $(MAKE) clean-exit)
	@echo "Add new pod: Passed"
	@$(MAKE) mock-delete-new-pod && sleep 10
	@curl -s localhost:8000/metrics|grep -c 'spyre_power_watts{'|grep -q 2||(echo "Failed to remove metric file label when pod is deleted"; $(MAKE) clean-exit)
	@curl -s localhost:8000/metrics|grep -c 'spyre_allocation{.*spyre='|grep -q 2||(echo "Failed to remove allocation label when pod is deleted"; $(MAKE) clean-exit)
	@echo "Delete new pod: Passed"
	@$(MAKE) exporter-cleanup

metrics: docker-build ## Run mock example and generate METRICS.md
	@$(MAKE) run-single-spyre-exporter
	@curl localhost:8000 > prom.out;./hack/prom-to-markdown.bash prom.out > METRICS.md;rm prom.out
	@$(DOCKER) kill single-spyre-exporter || true
	@$(DOCKER) rm single-spyre-exporter || true

clean-exit: ## Cleanup with exit 1
	@$(DOCKER) ps
	@$(MAKE) exporter-cleanup
	@exit 1

exporter-cleanup: ## Cleanup mock-up exporter
	@echo "Clean up"
	@$(DOCKER) kill single-spyre-exporter || true
	@$(DOCKER) rm single-spyre-exporter || true
	@$(DOCKER) kill multi-spyre-exporter || true
	@$(DOCKER) rm multi-spyre-exporter || true

mock-user-cleanup:
	@$(DOCKER) kill single-spyre-user || true
	@$(DOCKER) rm single-spyre-user || true
	@$(DOCKER) kill multi-spyre-user || true
	@$(DOCKER) rm multi-spyre-user || true
	@$(DOCKER) kill multi-spyre-no-splitter-user || true
	@$(DOCKER) rm multi-spyre-no-splitter-user || true

##@ General

.PHONY: help
help: ## Display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

.PHONY: version
version: ## Display image version
	@echo "Image version: $(VERSION)"

.PHONY: echo-version
echo-version: ## Print (echo) the current version
	@echo "$(VERSION)"

.PHONY: clean
clean: ## Clean-up intermediate artifacts
	-rm -rf $(LOCALBIN)
	-rm -rf local.mkg

.PHONY: venv
venv: ## Setup and activate venv
	$(PYTHON) -m venv venv

.PHONY: detect-secrets-install
detect-secrets-install: venv ## Install detect-secret tool
	. venv/bin/activate; $(PIP) install "git+$(DETECT_SECRETS_GIT)"

.PHONY: secrets-scan
secrets-scan: venv detect-secrets-install ## Scan secrets and create secret-baseline for repo
	. venv/bin/activate; detect-secrets scan --no-ghe-scan --exclude-files go.sum --update .secrets.baseline

.PHONY: secrets-audit
secrets-audit: venv detect-secrets-install ## Audit secrets
	. venv/bin/activate; detect-secrets audit .secrets.baseline

# helper target for viewing the value of makefile variables.
print-%  : ;@echo $* = $($*)

.PHONY: vendor
vendor:
	@:
