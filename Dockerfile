FROM python:3.14.5-alpine

RUN apk update \
    && apk upgrade \
    && apk --no-cache add curl git openjdk17-jre

ARG ALLURE_VERSION=2.42.0
RUN wget -q -O /tmp/allure-commandline.tgz \
    "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/${ALLURE_VERSION}/allure-commandline-${ALLURE_VERSION}.tgz" \
    && tar -zxf /tmp/allure-commandline.tgz -C / \
    && rm /tmp/allure-commandline.tgz

ENV PATH="/allure-${ALLURE_VERSION}/bin:${PATH}"
ENV ALLURE_HISTORY_CORE_DIR="/opt/core-allure-history"

WORKDIR /opt/core-allure-history
COPY allure_history.py generate_index.py prune_reports.py smoke_public_pages.py ./
COPY bin/allure-history /usr/local/bin/allure-history
RUN chmod +x /usr/local/bin/allure-history

WORKDIR /work
