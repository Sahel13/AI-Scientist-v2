# macOS Apple Container

This setup runs the AI Scientist in a CPU-only ARM64 Linux container. Generated
experiments can be submitted to an external Slurm HPC cluster over SSH.

Apple Container requires Apple silicon and macOS 26+.

## Build and run

```bash
container system start
container build --tag ai-scientist:cpu --file Containerfile .
cp .env.example .env

# Add the API keys you use to .env, then open a shell:
./container/run.sh
```

Run a command directly:

```bash
./container/run.sh python ai_scientist/perform_ideation_temp_free.py \
  --workshop-file ai_scientist/ideas/my_research_topic.md \
  --model gpt-4o-2024-05-13
```

The runner forwards the host SSH agent, mounts SSH config and `known_hosts` when
present, and persists `experiments/`, `slurm_logs/`, `cache/`, and the ideas
directory on the host. The source tree is read-only in the container.

Adjust container resources with:

```bash
AI_SCIENTIST_CONTAINER_CPUS=8 \
AI_SCIENTIST_CONTAINER_MEMORY=16G \
./container/run.sh
```

## Dedicated HPC SSH agent

Do not mount private keys into the container. Create a dedicated key if needed:

```bash
ssh-keygen -t ed25519 \
  -f ~/.ssh/id_ed25519_ai_scientist_hpc \
  -C "ai-scientist-hpc"
ssh-copy-id -i ~/.ssh/id_ed25519_ai_scientist_hpc.pub user@hpc.example.edu
```

Add an SSH alias to `~/.ssh/config`:

```sshconfig
Host your-hpc-alias
    HostName hpc.example.edu
    User your_hpc_username
```

Run the container from a separate agent containing only the HPC key. The
repository wrapper creates that short-lived agent for you:

```bash
# Uses ~/.ssh/id_ed25519_ai_scientist_hpc by default.
./container/run-hpc.sh ssh your-hpc-alias hostname
./container/run-hpc.sh

# Or select a different dedicated key.
AI_SCIENTIST_HPC_KEY=~/.ssh/your_hpc_key ./container/run-hpc.sh
```

It prompts for the key passphrase when necessary and tears down the temporary
agent once the container command exits. The equivalent manual setup is:

```bash
task_ssh_dir="$(mktemp -d "${TMPDIR:-/tmp}/ai-scientist-ssh.XXXXXX")"

(
  eval "$(ssh-agent -s -a "${task_ssh_dir}/agent.sock")"
  trap 'ssh-agent -k >/dev/null' EXIT
  ssh-add ~/.ssh/id_ed25519_ai_scientist_hpc
  ssh-add -l

  ./container/run.sh ssh your-hpc-alias hostname
  # Run the controller; its Slurm adapter submits generated experiments.
  ./container/run.sh
)
```

The runner sees this agent through Apple Container's `--ssh` forwarding. The
private key stays on the Mac, although processes in the container can use the
forwarded agent to authenticate. Prefer `./container/run-hpc.sh`, which creates
this restricted agent automatically.

Configure `exec.backend: slurm` in the BFTS configuration to submit generated
experiment code to the cluster. Metric parsing, plotting, and paper generation
continue in the container. See [the Slurm adapter guide](slurm-adapter.md).
