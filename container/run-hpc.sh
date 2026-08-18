#!/usr/bin/env bash
# Run AI Scientist with a temporary SSH agent that contains only the HPC key.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HPC_KEY="${AI_SCIENTIST_HPC_KEY:-${HOME}/.ssh/id_ed25519_ai_scientist_hpc}"

if [[ ! -r "${HPC_KEY}" ]]; then
    printf 'HPC key not readable: %s\n' "${HPC_KEY}" >&2
    printf 'Set AI_SCIENTIST_HPC_KEY to the dedicated private-key path.\n' >&2
    exit 1
fi

agent_dir="$(mktemp -d "${TMPDIR:-/tmp}/ai-scientist-ssh.XXXXXX")"
agent_socket="${agent_dir}/agent.sock"

cleanup() {
    ssh-agent -k >/dev/null 2>&1 || true
    rm -rf "${agent_dir}"
}
trap cleanup EXIT INT TERM

eval "$(ssh-agent -s -a "${agent_socket}")"
ssh-add "${HPC_KEY}"
ssh-add -l

"${ROOT_DIR}/container/run.sh" "$@"
