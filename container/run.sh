#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${AI_SCIENTIST_CONTAINER_IMAGE:-ai-scientist:cpu}"
ENV_FILE="${AI_SCIENTIST_ENV_FILE:-${ROOT_DIR}/.env}"
CPUS="${AI_SCIENTIST_CONTAINER_CPUS:-4}"
MEMORY="${AI_SCIENTIST_CONTAINER_MEMORY:-8G}"

if [[ ! -f "${ENV_FILE}" ]]; then
    printf 'Missing %s. Copy .env.example to .env and add your API keys.\n' "${ENV_FILE}" >&2
    exit 1
fi

mkdir -p \
    "${ROOT_DIR}/ai_scientist/ideas" \
    "${ROOT_DIR}/cache" \
    "${ROOT_DIR}/experiments" \
    "${ROOT_DIR}/slurm_logs"

if [[ "$#" -eq 0 ]]; then
    set -- bash
fi

CONTAINER_ARGS=(
    run
    --rm
    --init
    --env-file "${ENV_FILE}"
    --env HOME=/cache/home
    --env HF_HOME=/cache/huggingface
    --env TRANSFORMERS_CACHE=/cache/huggingface
    --env MPLCONFIGDIR=/cache/matplotlib
    --env PIP_CACHE_DIR=/cache/pip
    --ssh
    --uid "$(id -u)"
    --gid "$(id -g)"
    --cpus "${CPUS}"
    --memory "${MEMORY}"
    --volume "${ROOT_DIR}:/workspace/AI-Scientist-v2:ro"
    --volume "${ROOT_DIR}/ai_scientist/ideas:/workspace/AI-Scientist-v2/ai_scientist/ideas"
    --volume "${ROOT_DIR}/cache:/cache"
    --volume "${ROOT_DIR}/experiments:/workspace/AI-Scientist-v2/experiments"
    --volume "${ROOT_DIR}/slurm_logs:/workspace/AI-Scientist-v2/slurm_logs"
)

if [[ -t 0 && -t 1 ]]; then
    CONTAINER_ARGS+=(--interactive --tty)
fi

# Forward SSH configuration and host fingerprints when they exist, but never
# mount private keys. --ssh forwards the host's SSH agent into the container.
if [[ -f "${HOME}/.ssh/config" ]]; then
    CONTAINER_ARGS+=(--volume "${HOME}/.ssh/config:/cache/home/.ssh/config:ro")
fi
if [[ -f "${HOME}/.ssh/known_hosts" ]]; then
    CONTAINER_ARGS+=(--volume "${HOME}/.ssh/known_hosts:/cache/home/.ssh/known_hosts:ro")
fi

exec container "${CONTAINER_ARGS[@]}" "${IMAGE}" "$@"
