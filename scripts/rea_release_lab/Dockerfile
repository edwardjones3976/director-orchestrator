FROM node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c

RUN apt-get update \
    && apt-get install -y --no-install-recommends diffoscope-minimal=240+deb12u1 procps python3 \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --prefix /opt/rea-lab --ignore-scripts --no-audit --no-fund --save-exact \
    rea-agents@6.9.0 @electron/asar@4.3.2

COPY apply_rea_precision.py /opt/rea-lab-patch/apply_rea_precision.py
RUN python3 /opt/rea-lab-patch/apply_rea_precision.py /opt/rea-lab/node_modules/rea-agents

ENV PATH="/opt/rea-lab/node_modules/.bin:${PATH}" PYTHONDONTWRITEBYTECODE=1
WORKDIR /lab
