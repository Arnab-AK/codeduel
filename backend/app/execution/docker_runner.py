"""
The Docker-backed SandboxRunner. This module is the only place in the app
that talks to the Docker daemon, and the only place that sets the isolation
flags a submission runs under — see the inline comments on each flag below,
they're the actual security boundary, not incidental config.

Design choices worth flagging:

- put_archive() instead of a bind mount to get the submitted code + test
  cases into the container. A bind mount would need a real host path, and
  Docker Desktop on Windows/Mac translates host paths through its VM, which
  is a well-known source of flaky, platform-specific path bugs. put_archive()
  just streams a tar of bytes into the container's own tmpfs filesystem —
  the container never has a host path to reason about at all, which also
  means it has literally nothing to mount over even if it wanted to.

- One brand-new container per submission, always removed afterward
  (`finally: container.remove(force=True)`). No container is ever reused
  across submissions. Reuse would risk one player's run leaking filesystem
  or process state into another's — the throwaway cost (a few hundred ms of
  container create/teardown) buys a hard guarantee instead of "probably
  fine".

- Two-layer timeout. The harness enforces a per-test-case timeout from
  inside the container (images/python/harness.py). This module additionally
  bounds the whole container's wall-clock life and force-kills it if that's
  exceeded. The outer layer exists because it's the only one that's
  guaranteed to work no matter what goes wrong on the inside (harness bug,
  interpreter hang before the harness's own timeout logic even runs,
  container stuck starting) — `docker kill` tears down every process in the
  container's cgroup unconditionally.
"""
import io
import json
import tarfile
import time

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.config import settings
from app.execution.languages import get_language_config
from app.execution.sandbox import SandboxRunner, SandboxRunResult, TestCaseInput, TestCaseOutcome


def _build_archive(files: dict[str, str]) -> bytes:
    """In-memory tar archive for put_archive() — never touches disk."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class DockerSandboxRunner(SandboxRunner):
    def __init__(self, client: "docker.DockerClient | None" = None):
        self._client = client or docker.from_env()

    def run(
        self, code: str, test_cases: list[TestCaseInput], language: str
    ) -> SandboxRunResult:
        lang = get_language_config(language)

        payload = {
            lang.filename: code,
            "testcases.json": json.dumps(
                [
                    {"id": tc.id, "input": tc.input, "expected_output": tc.expected_output}
                    for tc in test_cases
                ]
            ),
        }
        archive = _build_archive(payload)

        per_test_timeout = settings.sandbox_timeout_seconds
        # Coarse backstop, not the primary control (see module docstring) —
        # capped at 60s so one submission can never tie up a worker
        # indefinitely regardless of how many test cases a problem has.
        outer_timeout = min(per_test_timeout * max(len(test_cases), 1) + 10, 60)

        container: Container = self._client.containers.create(
            image=lang.image,
            detach=True,
            user="sandboxuser",
            network_disabled=True,  # no DNS, no outbound sockets — nothing to exfiltrate to
            mem_limit=settings.sandbox_memory_limit,
            nano_cpus=int(settings.sandbox_cpu_limit * 1_000_000_000),
            pids_limit=settings.sandbox_pids_limit,  # hard cap on fork bombs
            # NOT read_only=True: the Docker API's put_archive (how we get
            # the submission's code into the container, see module
            # docstring) unconditionally refuses to write into a container
            # whose rootfs is marked read-only -- even onto a writable tmpfs
            # mount. This is a hard restriction in dockerd itself, not
            # something tunable from here. The image's own filesystem
            # permissions cover the gap instead: everything except /sandbox,
            # /tmp and /var/tmp is root-owned with standard (non-writable)
            # permissions, and sandboxuser is non-root -- verified directly
            # (`touch /etc/x` etc. all fail with Permission denied) rather
            # than assumed.
            #
            # /tmp and /var/tmp ARE tmpfs (world-writable by default
            # otherwise, and nothing needs to read their contents back
            # after the container exits). /sandbox deliberately is NOT
            # tmpfs: tmpfs is memory-backed and torn down the instant the
            # container exits, so anything written there -- specifically
            # the harness's result.json -- would vanish before
            # get_archive() below ever gets to read it back. /sandbox is
            # instead a real directory baked into the image and chowned to
            # sandboxuser (see the Dockerfile), which persists on the
            # container's own layer for exactly as long as the container
            # itself does. (uid/gid/mode are set explicitly here because a
            # bare tmpfs mount defaults to root:root ownership, which
            # sandboxuser can't write to.)
            tmpfs={
                "/tmp": "rw,size=16m,uid=65532,gid=1000,mode=0700",
                "/var/tmp": "rw,size=16m,uid=65532,gid=1000,mode=0700",
            },
            working_dir="/sandbox",
            cap_drop=["ALL"],  # no Linux capabilities: no raw sockets, no ptrace, nothing
            security_opt=["no-new-privileges"],  # can't regain privileges via setuid binaries
            environment={"CODEDUEL_TIMEOUT_SECONDS": str(per_test_timeout)},
        )
        try:
            self._client.api.put_archive(container.id, "/sandbox", archive)
            container.start()

            try:
                container.wait(timeout=outer_timeout)
            except Exception:
                # Anything short of a clean, observed exit within the outer
                # timeout gets treated the same way: kill it. There's no
                # scenario where "the container is still running and we
                # don't know why" is safe to just wait longer on.
                try:
                    container.kill()
                except APIError:
                    pass  # already exited/removed between the timeout and here

            container.reload()
            oom_killed = container.attrs.get("State", {}).get("OOMKilled", False)

            result = self._read_result_file(container)
            if result is None:
                logs = self._safe_logs(container)
                reason = (
                    "Container was killed for exceeding the memory limit"
                    if oom_killed
                    else "Sandbox produced no result (timed out, crashed, or was killed)"
                )
                return SandboxRunResult(outcomes=[], infra_error=f"{reason}. Logs: {logs[:2000]}")

            outcomes = [
                TestCaseOutcome(
                    test_case_id=o["test_case_id"],
                    passed=o["passed"],
                    actual_output=o["actual_output"],
                    error=o["error"],
                    runtime_ms=o["runtime_ms"],
                )
                for o in result["outcomes"]
            ]
            return SandboxRunResult(outcomes=outcomes, infra_error=result.get("infra_error"))
        finally:
            try:
                container.remove(force=True)
            except NotFound:
                pass

    def _read_result_file(self, container: Container) -> dict | None:
        try:
            stream, _ = container.get_archive("/sandbox/result.json")
        except NotFound:
            return None
        buf = io.BytesIO()
        for chunk in stream:
            buf.write(chunk)
        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar:
            member = tar.getmember("result.json")
            extracted = tar.extractfile(member)
            if extracted is None:
                return None
            return json.loads(extracted.read().decode("utf-8"))

    def _safe_logs(self, container: Container) -> str:
        try:
            return container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        except APIError:
            return "(logs unavailable)"
