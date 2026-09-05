# This image is what actually executes untrusted, submitted code. Everything
# in it is deliberately minimal: fewer installed tools means a smaller attack
# surface if a submission finds a way to poke around its own filesystem.
FROM python:3.11-slim

# Dedicated, non-login, no-home user. The container is also run with
# read_only=True + user=sandboxuser from the host side (see
# execution/docker_runner.py) — setting it here too means the image is safe
# by default even if someone runs it manually without those flags.
RUN useradd --no-create-home --uid 65532 --shell /usr/sbin/nologin sandboxuser

COPY harness.py /harness/run.py
RUN chown -R root:root /harness && chmod -R 555 /harness

# /sandbox is where put_archive() (docker_runner.py) writes the submission's
# code before the container starts, and where the harness writes
# result.json after running it. It is intentionally NOT a tmpfs mount: tmpfs
# is memory-backed and torn down the instant the container exits, so
# anything written there is invisible to get_archive() once the container
# has stopped -- which is exactly when we need to read result.json back.
# Owning it here means sandboxuser can create/write result.json without
# needing root, while everything else in the image stays read-only-by-
# permission to that user.
RUN mkdir -p /sandbox && chown sandboxuser:sandboxuser /sandbox
WORKDIR /sandbox
USER sandboxuser

# Fixed entrypoint: the container's only job is "run the harness". The
# harness reads whatever was written into /sandbox by put_archive() before
# start (see docker_runner.py) — there is no other way to make this
# container do anything else.
ENTRYPOINT ["python3", "/harness/run.py"]
