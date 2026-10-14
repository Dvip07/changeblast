#!/bin/bash
# EC2 first boot (Amazon Linux 2023): Docker + Compose, clone the public repo, start the stack.
set -euxo pipefail
dnf install -y docker git
systemctl enable --now docker

# Docker Compose v2 and Buildx plugins (official GitHub releases, pinned versions).
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
curl -fsSL https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod +x /usr/local/lib/docker/cli-plugins/*

# Swap: building images and indexing large repos can spike memory on 8 GB.
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

git clone https://github.com/Dvip07/changeblast.git /opt/changeblast
bash /opt/changeblast/deploy/start.sh > /var/log/changeblast-deploy.log 2>&1
