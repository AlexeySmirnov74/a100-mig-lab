#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/00_host_prereqs.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "ERROR: this installer supports Ubuntu. Install Docker and NVIDIA Container Toolkit manually on other distributions." >&2
  exit 1
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

echo "[1/6] Installing base packages..."
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  jq \
  git \
  unzip \
  python3 \
  python3-venv \
  python3-pip \
  software-properties-common

echo "[2/6] Checking NVIDIA driver visibility..."
if ! need_cmd nvidia-smi; then
  echo "ERROR: nvidia-smi is not available. Rent/provision the host with a working NVIDIA driver first."
  exit 2
fi
nvidia-smi >/dev/null

echo "[3/6] Installing Docker Engine and Compose plugin..."
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
ARCH=$(dpkg --print-architecture)
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

if id -nG "${SUDO_USER:-$USER}" | grep -qw docker; then
  true
else
  usermod -aG docker "${SUDO_USER:-$USER}" || true
fi

echo "[4/6] Installing NVIDIA Container Toolkit..."
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

echo "[5/6] Validating GPU access from Docker..."
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=utility nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null

echo "[6/6] Done. You may need to log out and log back in for docker group membership to take effect."
docker --version
if docker compose version >/dev/null 2>&1; then
  docker compose version
fi
