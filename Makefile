SHELL := /bin/bash
ROOT_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
GPU_ID ?= 0
MIG_RESET ?= 0
USERS ?= 10
DURATION ?= 120
THINK ?= 0.5
MODE ?= all
TOKENS ?= 64

.PHONY: help host mig up demo stress-on stress-off load load-stop down

help:
	@echo "make host       Install prerequisites on Ubuntu (sudo)"
	@echo "make mig        Enable MIG and create the A100 40 GB layout; GPU_ID=$(GPU_ID)"
	@echo "                Existing layout replacement requires MIG_RESET=1"
	@echo "make up         Build and start services; wait for readiness"
	@echo "make demo       Smoke-test all three routes through the context guard"
	@echo "make stress-on  Trigger OOM on the dedicated stress slice"
	@echo "make stress-off Stop the stress worker"
	@echo "make load       USERS=$(USERS) DURATION=$(DURATION) THINK=$(THINK) MODE=$(MODE) TOKENS=$(TOKENS)"
	@echo "make load-stop  Stop the load generator"
	@echo "make down       Stop lab services; preserve model cache and data"

host:
	cd "$(ROOT_DIR)" && sudo bash scripts/00_host_prereqs.sh

mig:
	cd "$(ROOT_DIR)" && sudo env GPU_ID="$(GPU_ID)" bash scripts/01_enable_mig.sh
	cd "$(ROOT_DIR)" && sudo env GPU_ID="$(GPU_ID)" MIG_RESET="$(MIG_RESET)" bash scripts/02_create_layout.sh
	cd "$(ROOT_DIR)" && GPU_ID="$(GPU_ID)" bash scripts/03_export_env.sh
	cd "$(ROOT_DIR)" && GPU_ID="$(GPU_ID)" bash scripts/04_validate_host.sh

up:
	bash "$(ROOT_DIR)/scripts/05_start_stack.sh"

demo:
	bash "$(ROOT_DIR)/scripts/06_demo_queries.sh"

stress-on:
	bash "$(ROOT_DIR)/scripts/07_start_stress.sh"

stress-off:
	bash "$(ROOT_DIR)/scripts/08_stop_stress.sh"

load:
	LOAD_USERS=$(USERS) LOAD_DURATION_SEC=$(DURATION) LOAD_THINK_TIME_SEC=$(THINK) \
	LOAD_MODEL_MODE=$(MODE) LOAD_MAX_TOKENS=$(TOKENS) bash "$(ROOT_DIR)/scripts/09_start_text_load.sh"

load-stop:
	bash "$(ROOT_DIR)/scripts/10_stop_text_load.sh"

down:
	bash "$(ROOT_DIR)/scripts/99_cleanup.sh"
