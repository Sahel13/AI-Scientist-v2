# Slurm experiment adapter

The AI Scientist controller runs locally (or in `container/run.sh`). Generated
experiment code is staged to the configured Slurm cluster, while metric parsing,
plotting, and paper generation remain local.

## Configuration

The shipped `bfts_config.yaml` uses local execution. To use Slurm, copy
`bfts_config.slurm.example.yaml` and `job_template.slurm.example`, replace the
placeholders and resource directives for your environment. The resulting
private files are ignored by Git and are selected automatically by the launch
command:

```bash
cp bfts_config.slurm.example.yaml bfts_config.private.yaml
cp job_template.slurm.example job_template.private.slurm
```

```yaml
exec:
  backend: slurm
  timeout: 3600
  slurm:
    host: "YOUR_SSH_HOST"
    remote_root: "/path/to/your/remote/ai-scientist-runs"
    template: "job_template.private.slurm"
    poll_seconds: 20
    # Time for copying results and local metric/plot/VLM processing after the job.
    postprocess_timeout: 300
    keep_remote: true
```

Your copied `job_template.private.slurm` owns the Slurm resources and Python
environment. The adapter creates a new subdirectory for every submitted job
and passes it in `AI_SCIENTIST_REMOTE_WORKDIR`. It does not forward local API
keys to Slurm.

`keep_remote: true` preserves staged workspaces for debugging. Set it to
`false` after a successful test to remove them after results have been copied
back.

## Smoke test

Before starting an agent run, verify the cluster connection from the same
environment that will run the controller:

```bash
./container/run-hpc.sh ssh YOUR_SSH_HOST hostname
./container/run-hpc.sh ssh YOUR_SSH_HOST sinfo
```

Start conservatively: `num_workers: 1` and `num_seeds: 1` submit one GPU job at
a time. The adapter returns the remote Slurm stdout/stderr to the agent and
copies `experiment_data.npy` and plots into the local worker workspace.

## Running a paper-generation experiment

```bash
./container/run.sh python ai_scientist/perform_ideation_temp_free.py \
  --workshop-file ai_scientist/ideas/my_topic.md \
  --model YOUR_IDEATION_MODEL

./container/run.sh python launch_scientist_bfts.py \
  --load_ideas ai_scientist/ideas/my_topic.json \
  --idea_idx 0 \
  --load_code \
  --add_dataset_ref \
  --model_writeup YOUR_WRITEUP_MODEL \
  --model_citation YOUR_CITATION_MODEL \
  --model_review YOUR_REVIEW_MODEL
```

Use `--skip_writeup --skip_review` for the first end-to-end Slurm test. The
launch command copies the selected `--bfts-config` file into the experiment
directory, so runs remain reproducible after the base configuration changes.
