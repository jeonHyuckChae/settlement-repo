#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# VirtualBox Ubuntu 24.04 VM 초기 셋업 (Day 2·3 실습 워크북 Part 1.4)
#   - Docker, kind, kubectl, Trivy 설치 (이미 있으면 건너뜀 → Day 1에서 설치했어도 안전)
#   - kind 클러스터 생성 (이름: settlement-local, control-plane 1 + worker 1)
#   - GitHub Actions self-hosted runner를 위한 사용자 준비
#
# 실행: sudo bash scripts/setup-runner-vm.sh
#
# 변경 이력 (Day 1 실습과 맞춤)
#   - kind 버전을 Day 1과 같은 v0.31.0으로 통일
#   - 클러스터를 root가 아닌 실습 사용자 권한으로 생성
#     (sudo로 만들면 kubeconfig가 /root/.kube 에 저장되어 사용자 · runner가 접속하지 못함)
#   - 2노드 구성 (교안과 동일), Day 1의 lab 클러스터가 남아 있으면 경고
#   - Trivy 설치 버전 고정 (2026년 3월 공급망 공격 이후 권장 사항)
# ─────────────────────────────────────────────────────────────
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "sudo로 실행하세요: sudo bash scripts/setup-runner-vm.sh" >&2
  exit 1
fi

RUNNER_USER="${SUDO_USER:-$(whoami)}"
KIND_CLUSTER="settlement-local"
KIND_VERSION="v0.31.0"     # Day 1 워크북 Lab 0.4와 동일
TRIVY_VERSION="v0.76.1"    # 고정 버전 (업데이트 시 릴리스 노트 확인 후 변경)
ARCH="$(dpkg --print-architecture)"   # amd64 / arm64

# 실습 사용자 권한으로 명령 실행 (docker 그룹이 반영된 새 세션)
as_user() { sudo -u "${RUNNER_USER}" -H -- "$@"; }

echo ">> 1. 기본 패키지 업데이트"
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release

echo ">> 2. Docker 설치"
if ! command -v docker &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
else
  echo "   이미 설치됨 ($(docker --version)), 건너뜀"
fi

# runner 유저가 sudo 없이 docker 명령 실행 가능하도록
usermod -aG docker "${RUNNER_USER}"

echo ">> 3. kubectl 설치"
if ! command -v kubectl &>/dev/null; then
  curl -fsSLo /usr/local/bin/kubectl \
    "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl"
  chmod +x /usr/local/bin/kubectl
else
  echo "   이미 설치됨, 건너뜀"
fi

echo ">> 4. kind 설치"
if ! command -v kind &>/dev/null; then
  curl -fsSLo /usr/local/bin/kind \
    "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${ARCH}"
  chmod +x /usr/local/bin/kind
else
  echo "   이미 설치됨 ($(kind version)), 건너뜀"
fi

echo ">> 5. kind 클러스터 생성 (${KIND_CLUSTER}) — ${RUNNER_USER} 사용자 권한"
if as_user kind get clusters 2>/dev/null | grep -qx "lab"; then
  echo "   [경고] Day 1 실습용 'lab' 클러스터가 남아 있습니다. 메모리 절약을 위해 삭제를 권장합니다:"
  echo "          kind delete cluster --name lab"
fi
if ! as_user kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
  as_user kind create cluster --name "${KIND_CLUSTER}" --config=- <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
EOF
else
  echo "   이미 존재함, 건너뜀"
  # 예전 스크립트로 root가 만든 경우에도 사용자 kubeconfig에 컨텍스트를 등록
  as_user kind export kubeconfig --name "${KIND_CLUSTER}"
fi
as_user kubectl config use-context "kind-${KIND_CLUSTER}"

echo ">> 6. Trivy 설치 (버전 고정: ${TRIVY_VERSION})"
if ! command -v trivy &>/dev/null; then
  curl -sfL "https://raw.githubusercontent.com/aquasecurity/trivy/${TRIVY_VERSION}/contrib/install.sh" \
    | sh -s -- -b /usr/local/bin "${TRIVY_VERSION}"
else
  echo "   이미 설치됨 ($(trivy --version | head -1)), 건너뜀"
fi

echo ""
echo "완료. 이후 절차:"
echo "  1) 로그아웃/재로그인하여 docker 그룹 적용"
echo "  2) GitHub 레포 Settings > Actions > Runners > New self-hosted runner 안내에 따라"
echo "     actions-runner 설치 및 './config.sh' 실행 (label: settlement 추가)"
echo "     예) ./config.sh --url https://github.com/<org>/<repo> --token <TOKEN> --labels settlement"
echo "     ※ runner는 v2.328.0 이상이어야 Node 24 기반 액션(checkout@v6 등)이 동작합니다"
echo "  3) './run.sh' 또는 systemd 서비스로 등록: sudo ./svc.sh install && sudo ./svc.sh start"
echo "  4) (sudo 없이) kubectl config current-context → kind-${KIND_CLUSTER}"
echo "     kubectl get nodes → ${KIND_CLUSTER}-control-plane, ${KIND_CLUSTER}-worker 가 Ready"
