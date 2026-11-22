# Reproducible AppImage build environment (later/CI path).
#
# Ubuntu 24.04 is the current baseline.  It gives glibc 2.39, so the resulting
# AppImage runs on Ubuntu 24.04, Mint 22.x and newer, plus current Fedora/Arch.
# Do not build release artifacts on a newer or rolling host unless you only
# need them there: its glibc is newer than most target machines.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# NOTE: on Ubuntu 22.04 the FUSE package is called libfuse2, not libfuse2t64.
# libpython3-dev pulls the versioned libpython3.x shared library that PyInstaller
# needs; without it the build stops with "Python shared library was not found".
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        python3 python3-venv python3-tk libpython3-dev \
        libpulse0 libfuse2t64 \
        zsync file git curl ca-certificates desktop-file-utils binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
