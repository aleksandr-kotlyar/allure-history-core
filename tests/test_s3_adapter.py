import argparse
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import md5
from io import BytesIO
import json
import os

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber
import pytest

import s3_adapter as s3


class MemoryS3:
    """An isolated object store with real conditional-write semantics."""

    def __init__(self):
        self.objects = {}
        self.reads = []
        self.writes = []
        self.fail_key = None
        self.fail_after_write = False

    def add(self, key, body, **headers):
        self.objects[key] = {"body": body, "ETag": '"' + md5(body).hexdigest() + '"',
                             "Size": len(body), "LastModified": datetime(2026, 10, 26, tzinfo=UTC),
                             **headers}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, *, Bucket, Prefix):
        assert Bucket == "reports"
        items = [{"Key": key, **{k: v for k, v in value.items() if k != "body"}}
                 for key, value in sorted(self.objects.items()) if key.startswith(Prefix)]
        for offset in range(0, len(items), 2):
            yield {"Contents": items[offset:offset + 2]}

    def get_object(self, *, Bucket, Key):
        assert Bucket == "reports"
        self.reads.append(Key)
        item = self.objects[Key]
        return {**{k: v for k, v in item.items() if k != "body"}, "Body": BytesIO(item["body"])}

    def check_condition(self, Key, IfMatch=None, IfNoneMatch=None):
        item = self.objects.get(Key)
        if ((IfNoneMatch == "*" and item is not None)
                or (IfMatch is not None and (item is None or item["ETag"] != IfMatch))):
            raise ClientError({"Error": {"Code": "PreconditionFailed"},
                               "ResponseMetadata": {"HTTPStatusCode": 412}}, "ConditionalWrite")

    def put_object(self, *, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **headers):
        assert Bucket == "reports"
        self.check_condition(Key, IfMatch, IfNoneMatch)
        should_fail = self.fail_key == Key
        if should_fail:
            self.fail_key = None
            if not self.fail_after_write:
                raise RuntimeError("synthetic interrupted upload")
        self.add(Key, Body, **headers)
        self.writes.append(("put", Key))
        if should_fail:
            raise RuntimeError("synthetic lost upload response")
        return {"ETag": self.objects[Key]["ETag"]}

    def delete_object(self, *, Bucket, Key, IfMatch):
        assert Bucket == "reports"
        self.check_condition(Key, IfMatch=IfMatch)
        self.objects.pop(Key, None)
        self.writes.append(("delete", Key))
        return {}


@pytest.fixture
def store():
    client = MemoryS3()
    for group, name in (("dev/qa-python/qa-mdsp-latest", "2026-10-26_09-00-00"),
                        ("dev/another-project/feature", "job_123"),
                        ("rc/qa-python/rank", "run-blue")):
        root = f"archive/{group}/{name}"
        client.add(root + "/index.html", b"original allure report")
        client.add(root + "/data/attachments/log.txt", b"never download this attachment")
        client.add(root + "/executor.json", b'{"reportUrl":"preserve"}')
        if name != "run-blue":
            client.add(root + "/widgets/summary.json", b'{"statistic":{"passed":5,"total":5}}')
    client.add("archive/dev/qa-python/qa-mdsp-latest/.history/history.json", b"preserve history")
    client.add("archive/index.html", b"old directory index", ContentType="text/html",
               CacheControl="max-age=90", Metadata={"owner": "qa"})
    client.add("outside/untouched.txt", b"another prefix")
    return client


def prepare(tmp_path, store):
    return s3.prepare(argparse.Namespace(s3_uri="s3://reports/archive/",
                                         public_url="https://reports.test/independent/base/",
                                         output_dir=str(tmp_path / "plan"), public_title="QA reports"), store)


def test_preview_reads_only_metadata_and_replaced_indexes_and_never_writes(tmp_path, store):
    before = deepcopy(store.objects)
    plan_path = prepare(tmp_path, store)
    plan = json.loads(plan_path.read_text())
    assert store.objects == before
    assert store.writes == []
    assert all(key.endswith(("widgets/summary.json", ".modified_at"))
               or key == "archive/index.html" for key in store.reads)
    assert all("attachments" not in key and ".history" not in key for key in store.reads)
    assert {entry["action"] for entry in plan["operations"]} == {"create", "replace"}
    assert (plan_path.parent / "backup/index.html").read_bytes() == b"old directory index"
    assert "rc/qa-python/rank/run-blue" in plan["reports"]
    assert "dev/another-project/feature/job_123" in plan["reports"]
    assert 'class="hero"' not in (plan_path.parent / "public/index.html").read_text()
    alias = plan_path.parent / "public/dev/qa-python/qa-mdsp-latest/latest/index.html"
    assert "https://reports.test/independent/base/dev/qa-python/qa-mdsp-latest/2026-10-26_09-00-00/" in alias.read_text()


def test_publish_and_rollback_restore_original_bytes_and_http_metadata(tmp_path, store):
    before = deepcopy(store.objects)
    plan_path = prepare(tmp_path, store)
    s3.apply_plan(plan_path, store)
    assert store.objects["archive/index.html"]["ContentType"] == "text/html; charset=utf-8"
    for key, item in before.items():
        if key != "archive/index.html":
            assert store.objects[key] == item
    s3.apply_plan(plan_path, store, rollback=True)
    assert store.objects == before
    assert json.loads(plan_path.read_text())["state"] == "rolled-back"
    # A repeated rollback is harmless.
    s3.apply_plan(plan_path, store, rollback=True)
    assert store.objects == before


def test_rollback_restores_missing_cache_header_when_html_bytes_did_not_change(tmp_path, store):
    first = prepare(tmp_path, store)
    store.add("archive/index.html", (first.parent / "public/index.html").read_bytes(),
               ContentType=s3.HTML_HEADERS["ContentType"])
    before = deepcopy(store.objects)
    args = argparse.Namespace(s3_uri="s3://reports/archive", public_url="https://reports.test/independent/base/",
                              output_dir=str(tmp_path / "second-plan"), public_title="QA reports")
    plan_path = s3.prepare(args, store)
    s3.apply_plan(plan_path, store)
    s3.apply_plan(plan_path, store, rollback=True)
    assert store.objects == before


@pytest.mark.parametrize("change", ["new-report", "navigation", "summary"])
def test_stale_plan_refuses_to_publish_before_any_index_is_changed(tmp_path, store, change):
    plan_path = prepare(tmp_path, store)
    key = {"new-report": "archive/dev/qa-python/new/2026-10-26_10-00-00/index.html",
           "navigation": "archive/index.html",
           "summary": "archive/dev/qa-python/qa-mdsp-latest/2026-10-26_09-00-00/widgets/summary.json"}[change]
    store.add(key, b"changed by another job")
    before = deepcopy(store.objects)
    with pytest.raises(RuntimeError, match="changed"):
        s3.apply_plan(plan_path, store)
    assert store.objects == before
    assert all(key.endswith(s3.LOCK_NAME) for _, key in store.writes)


def test_another_publisher_holds_lock_and_it_is_not_taken_over(tmp_path, store):
    plan_path = prepare(tmp_path, store)
    store.add("archive/" + s3.LOCK_NAME, b"another publisher")
    before = deepcopy(store.objects)
    with pytest.raises(ClientError, match="PreconditionFailed"):
        s3.apply_plan(plan_path, store)
    assert store.objects == before


@pytest.mark.parametrize("after_write", [False, True])
def test_partial_publish_and_lost_response_can_be_rolled_back(tmp_path, store, after_write):
    before = deepcopy(store.objects)
    plan_path = prepare(tmp_path, store)
    plan = json.loads(plan_path.read_text())
    store.fail_key = "archive/" + plan["operations"][2]["key"]
    store.fail_after_write = after_write
    with pytest.raises(RuntimeError, match="synthetic"):
        s3.apply_plan(plan_path, store)
    assert "archive/" + s3.LOCK_NAME not in store.objects
    s3.apply_plan(plan_path, store, rollback=True)
    assert store.objects == before


@pytest.mark.parametrize("change", ["body", "headers"])
def test_rollback_refuses_to_overwrite_another_jobs_new_navigation(tmp_path, store, change):
    plan_path = prepare(tmp_path, store)
    s3.apply_plan(plan_path, store)
    if change == "body":
        store.add("archive/index.html", b"new navigation from another job")
    else:
        store.objects["archive/index.html"]["CacheControl"] = "changed by another job"
    before = deepcopy(store.objects)
    with pytest.raises(RuntimeError, match="changed object"):
        s3.apply_plan(plan_path, store, rollback=True)
    assert store.objects == before


@pytest.mark.parametrize("key", ["archive/../../escape/index.html", "archive/a\\b/index.html"])
def test_unsafe_s3_paths_are_rejected_before_local_files_are_written(tmp_path, store, key):
    store.add(key, b"bad key")
    with pytest.raises(ValueError, match="Unsafe relative"):
        prepare(tmp_path, store)
    assert not (tmp_path / "escape").exists()
    assert store.writes == []


def test_modified_preview_is_rejected_before_storage_changes(tmp_path, store):
    plan_path = prepare(tmp_path, store)
    (plan_path.parent / "public/index.html").write_text("modified after review")
    with pytest.raises(ValueError, match="Plan file changed"):
        s3.apply_plan(plan_path, store)
    assert store.writes == []


def test_s3_scope_cannot_be_an_existing_report_itself(tmp_path, store):
    before = deepcopy(store.objects)
    args = argparse.Namespace(s3_uri="s3://reports/archive/dev/qa-python/qa-mdsp-latest/2026-10-26_09-00-00",
                              public_url="https://reports.test/report", output_dir=str(tmp_path / "plan"),
                              public_title="QA reports")
    with pytest.raises(ValueError, match="navigation root"):
        s3.prepare(args, store)
    assert store.objects == before
    assert store.reads == [] and store.writes == []


def test_empty_neighbour_groups_are_retained_in_navigation(tmp_path, store):
    store.add("archive/dev/another-project/empty/index.html", b"old empty-group index")
    store.add("archive/dev/history-only/.history/history.json", b"history")
    plan_path = prepare(tmp_path, store)
    assert (plan_path.parent / "public/dev/another-project/empty/index.html").exists()
    assert (plan_path.parent / "public/dev/history-only/index.html").exists()


def test_concurrent_change_between_preflight_and_put_is_not_overwritten(tmp_path, store, monkeypatch):
    plan_path = prepare(tmp_path, store)
    real_put = store.put_object

    def racing_put(**kwargs):
        if kwargs["Key"] == "archive/index.html":
            store.add("archive/index.html", b"concurrent publisher")
        return real_put(**kwargs)

    monkeypatch.setattr(store, "put_object", racing_put)
    with pytest.raises(ClientError, match="PreconditionFailed"):
        s3.apply_plan(plan_path, store)
    assert store.objects["archive/index.html"]["body"] == b"concurrent publisher"
    assert "archive/" + s3.LOCK_NAME not in store.objects


def test_botocore_supports_conditional_lock_acquisition_and_release(monkeypatch):
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    client = boto3.Session().client("s3", region_name="us-east-1", endpoint_url="https://unused.test",
                                    aws_access_key_id="synthetic", aws_secret_access_key="synthetic")
    monkeypatch.setattr(s3, "uuid4", lambda: argparse.Namespace(hex="owner"))
    with Stubber(client) as stub:
        stub.add_response("put_object", {"ETag": '"lock-etag"'},
                          {"Bucket": "reports", "Key": "archive/" + s3.LOCK_NAME,
                           "Body": b"owner", "IfNoneMatch": "*"})
        stub.add_response("delete_object", {},
                          {"Bucket": "reports", "Key": "archive/" + s3.LOCK_NAME,
                           "IfMatch": '"lock-etag"'})
        with s3.publish_lock(client, "reports", "archive/"):
            pass
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("uri", ["s3://reports//", "s3://reports//archive",
                                  "s3://reports/archive//", "s3://reports/archive//dev",
                                  "\ns3://reports/archive", "s3://reports/arch\tive"])
def test_s3_location_does_not_silently_change_the_requested_prefix(uri):
    with pytest.raises(ValueError):
        s3.storage_location(uri)


def test_custom_layout_preserves_keys_and_encodes_only_the_public_links(tmp_path, store):
    root = "archive/teams/QA reports/release+1/проверка"
    store.add(root + "/index.html", b"original report")
    store.add(root + "/widgets/summary.json", b'{"time":{"start":1000}}')
    before = deepcopy(store.objects)
    path = prepare(tmp_path, store)
    plan = json.loads(path.read_text())
    assert "teams/QA reports/release+1/проверка" in plan["reports"]
    html = (path.parent / "public/teams/QA reports/release+1/index.html").read_text()
    assert "https://reports.test/independent/base/teams/QA%20reports/release%2B1/%D0%BF" in html
    s3.apply_plan(path, store)
    assert store.objects[root + "/index.html"] == before[root + "/index.html"]
    assert "archive/teams/QA reports/release+1/index.html" in store.objects


def test_cli_uses_standard_endpoint_region_credentials_and_addressing_config(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    config = tmp_path / "config"
    config.write_text("[default]\nregion = us-east-1\ns3 =\n    addressing_style = path\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials-file"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://storage.example.test")
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)

    def inspect_client(args, client):
        assert client.meta.endpoint_url == "https://storage.example.test"
        assert client.meta.region_name == "us-east-1"
        assert client.meta.config.s3["addressing_style"] == "path"
        url = client.generate_presigned_url("get_object", Params={"Bucket": "reports", "Key": "key"})
        assert url.startswith("https://storage.example.test/reports/key?")
        assert "synthetic-access-key" in url

    monkeypatch.setattr(s3, "prepare", inspect_client)
    assert s3.main(["index", "--s3-uri", "s3://reports/team", "--public-url",
                    "https://reports.example.test", "--output-dir", str(tmp_path / "plan")]) == 0
