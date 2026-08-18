"""Slurm-backed executor for generated AI Scientist experiment code.

The controller writes code locally, stages one isolated workspace to the
configured HPC over SSH/rsync, submits the user's fixed Slurm template, and
copies output files back before returning an ``ExecutionResult``.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path

import humanize

from .interpreter import ExecutionResult


logger = logging.getLogger("ai-scientist")


class SlurmInterpreter:
    """Run each code snippet as an isolated Slurm job.

    This deliberately does not forward the controller environment to Slurm:
    model-provider API keys remain on the local controller. The fixed job
    template is responsible for loading the remote experiment environment.
    """

    def __init__(
        self,
        working_dir: Path | str,
        timeout: int,
        format_tb_ipython: bool = False,
        agent_file_name: str = "runfile.py",
        slurm_config=None,
        env_vars: dict[str, str] | None = None,
    ):
        self.working_dir = Path(working_dir).resolve()
        if not self.working_dir.exists():
            raise ValueError(f"Working directory {self.working_dir} does not exist")
        if slurm_config is None:
            raise ValueError("slurm_config is required for SlurmInterpreter")

        self.timeout = timeout
        self.format_tb_ipython = format_tb_ipython
        self.agent_file_name = agent_file_name
        self.host = str(slurm_config.host)
        self.remote_root = str(slurm_config.remote_root).rstrip("/")
        self.poll_seconds = int(slurm_config.poll_seconds)
        self.keep_remote = bool(slurm_config.keep_remote)
        self.template = Path(slurm_config.template).expanduser().resolve()
        self.env_vars = env_vars or {}

        if not self.template.is_file():
            raise ValueError(f"Slurm template not found: {self.template}")
        if not self.host:
            raise ValueError("exec.slurm.host must not be empty")
        if not self.remote_root:
            raise ValueError("exec.slurm.remote_root must not be empty")

    @staticmethod
    def _output(result: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)

    def _run_local(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, check=check)

    def _ssh(self, remote_args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        # Pass a single, safely quoted command string to the remote shell.
        return self._run_local(["ssh", self.host, shlex.join(remote_args)], check=check)

    def _rsync_to_remote(self, source: Path, remote_path: str) -> None:
        self._run_local(["rsync", "-az", f"{source}/", f"{self.host}:{remote_path}/"])

    def _rsync_from_remote(self, remote_path: str) -> None:
        self._run_local(["rsync", "-az", f"{self.host}:{remote_path}/", f"{self.working_dir}/"])

    def _result(
        self,
        term_out: list[str],
        start_time: float,
        exc_type: str | None = None,
        exc_info: dict | None = None,
    ) -> ExecutionResult:
        elapsed = time.time() - start_time
        term_out.append(
            f"Execution time: {humanize.naturaldelta(elapsed)} seconds "
            f"(time limit is {humanize.naturaldelta(self.timeout)})."
        )
        return ExecutionResult(term_out, elapsed, exc_type, exc_info, [])

    def _collect_logs(
        self, remote_dir: str, term_out: list[str], job_id: str | None = None
    ) -> None:
        try:
            self._rsync_from_remote(remote_dir)
        except subprocess.CalledProcessError as exc:
            term_out.append(f"Failed to copy Slurm results back: {self._output(exc)}")
            return

        if job_id is None:
            log_files = sorted(
                [
                    *self.working_dir.glob("slurm-*.out"),
                    *self.working_dir.glob("slurm-*.err"),
                ]
            )
        else:
            log_files = [
                self.working_dir / f"slurm-{job_id}.out",
                self.working_dir / f"slurm-{job_id}.err",
            ]
        for log_file in log_files:
            try:
                contents = log_file.read_text(errors="replace").strip()
            except OSError as exc:
                term_out.append(f"Unable to read {log_file.name}: {exc}")
                continue
            if contents:
                term_out.append(f"--- {log_file.name} ---\n{contents}")

    def _job_state(self, job_id: str) -> str | None:
        queued = self._ssh(["squeue", "--noheader", "--jobs", job_id, "--format=%T"], check=False)
        if queued.returncode == 0 and queued.stdout.strip():
            return queued.stdout.strip().splitlines()[0]

        accounting = self._ssh(
            ["sacct", "--noheader", "--allocations", "--jobs", job_id, "--format=State", "--parsable2"],
            check=False,
        )
        if accounting.returncode == 0:
            states = [line.strip().split("|")[0] for line in accounting.stdout.splitlines() if line.strip()]
            if states:
                return states[0]
        return None

    def _wait_for_completion(self, job_id: str, term_out: list[str]) -> str:
        deadline = time.monotonic() + self.timeout
        last_state = None
        while time.monotonic() < deadline:
            state = self._job_state(job_id)
            if state and state != last_state:
                term_out.append(f"Slurm job {job_id} state: {state}")
                last_state = state
            if state and state.upper().split()[0] not in {"PENDING", "CONFIGURING", "RUNNING", "COMPLETING", "SUSPENDED"}:
                return state

            # Do not sleep past the execution deadline.  The caller needs the
            # remaining time to cancel the job and copy its results back.
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(self.poll_seconds, remaining))

        self._ssh(["scancel", job_id], check=False)
        return "TIMEOUT"

    def _remove_remote_dir(self, remote_dir: str, term_out: list[str]) -> None:
        if self.keep_remote:
            return
        result = self._ssh(["rm", "-rf", "--", remote_dir], check=False)
        if result.returncode != 0:
            term_out.append(f"Could not remove remote workspace {remote_dir}: {self._output(result)}")

    def run(self, code: str, reset_session: bool = True) -> ExecutionResult:
        """Stage, submit, wait for, and collect one generated experiment."""
        del reset_session  # Each Slurm invocation is necessarily a fresh session.
        start_time = time.time()
        term_out: list[str] = []
        runfile = self.working_dir / self.agent_file_name
        runfile.write_text(code)

        remote_dir = f"{self.remote_root}/run-{uuid.uuid4().hex}"
        remote_template = f"{remote_dir}/job_template.slurm"
        try:
            self._ssh(["mkdir", "-p", remote_dir])
            self._rsync_to_remote(self.working_dir, remote_dir)
            self._run_local(["rsync", "-az", str(self.template), f"{self.host}:{remote_template}"])

            submit = self._ssh(
                [
                    "sbatch",
                    "--parsable",
                    (
                        "--export="
                        f"AI_SCIENTIST_REMOTE_WORKDIR={remote_dir},"
                        f"AI_SCIENTIST_REMOTE_ROOT={self.remote_root}"
                    ),
                    remote_template,
                ]
            )
            raw_job_id = submit.stdout.strip().split(";", 1)[0]
            if not re.fullmatch(r"\d+(?:[_.]\d+)?", raw_job_id):
                raise RuntimeError(f"Unexpected sbatch output: {self._output(submit)}")
            term_out.append(f"Submitted Slurm job {raw_job_id} on {self.host}.")

            state = self._wait_for_completion(raw_job_id, term_out)
            self._collect_logs(remote_dir, term_out, raw_job_id)
            self._remove_remote_dir(remote_dir, term_out)

            if state.upper().startswith("COMPLETED"):
                return self._result(term_out, start_time)
            return self._result(
                term_out,
                start_time,
                "SlurmJobFailed" if state != "TIMEOUT" else "TimeoutError",
                {"job_id": raw_job_id, "state": state, "remote_dir": remote_dir},
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            term_out.append(f"Slurm execution failed before completion: {exc}")
            self._collect_logs(remote_dir, term_out)
            self._remove_remote_dir(remote_dir, term_out)
            return self._result(
                term_out,
                start_time,
                "SlurmSubmissionError",
                {"message": str(exc), "remote_dir": remote_dir},
            )

    def cleanup_session(self) -> None:
        """Provided for compatibility with the local Interpreter API."""
        return None
