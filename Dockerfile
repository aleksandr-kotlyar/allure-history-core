FROM python:3.14.5-alpine

RUN apk update \
    && apk upgrade \
    && apk --no-cache add curl git openjdk17-jre

ARG ALLURE_VERSION=2.42.0
RUN wget -q -O /tmp/allure-commandline.tgz \
    "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/${ALLURE_VERSION}/allure-commandline-${ALLURE_VERSION}.tgz" \
    && tar -zxf /tmp/allure-commandline.tgz -C / \
    && rm /tmp/allure-commandline.tgz

ARG S5CMD_VERSION=2.3.0
ARG TARGETARCH
RUN case "${TARGETARCH:-$(uname -m)}" in \
        amd64|x86_64) \
            S5CMD_ARCH=64bit; \
            S5CMD_SHA256=de0fdbfa3aceae55e069ba81a0fc17b2026567637603734a387b2fca06c299b4 \
            ;; \
        arm64|aarch64) \
            S5CMD_ARCH=arm64; \
            S5CMD_SHA256=1439f0d00ecedcd2a2f1f2c6749bbb0152b2257bf5086f29646ec8ae38798e24 \
            ;; \
        *) \
            echo "Unsupported s5cmd architecture: ${TARGETARCH:-$(uname -m)}" >&2; \
            exit 1 \
            ;; \
    esac \
    && S5CMD_ARCHIVE="s5cmd_${S5CMD_VERSION}_Linux-${S5CMD_ARCH}.tar.gz" \
    && curl -fsSL -o "/tmp/${S5CMD_ARCHIVE}" \
        "https://github.com/peak/s5cmd/releases/download/v${S5CMD_VERSION}/${S5CMD_ARCHIVE}" \
    && echo "${S5CMD_SHA256}  /tmp/${S5CMD_ARCHIVE}" | sha256sum -c - \
    && mkdir -p /usr/share/licenses/s5cmd \
    && tar -zxf "/tmp/${S5CMD_ARCHIVE}" -C /usr/local/bin s5cmd \
    && tar -zxf "/tmp/${S5CMD_ARCHIVE}" -C /usr/share/licenses/s5cmd LICENSE \
    && chmod +x /usr/local/bin/s5cmd \
    && rm "/tmp/${S5CMD_ARCHIVE}" \
    && s5cmd version

ENV PATH="/allure-${ALLURE_VERSION}/bin:${PATH}"
ENV ALLURE_HISTORY_CORE_DIR="/opt/allure-history-core"

WORKDIR /opt/allure-history-core
COPY allure_history.py generate_index.py gitlab_adapter.py prune_reports.py smoke_public_pages.py ./
COPY bin/allure-history bin/allure-history-gitlab /usr/local/bin/
RUN chmod +x /usr/local/bin/allure-history /usr/local/bin/allure-history-gitlab

WORKDIR /work
