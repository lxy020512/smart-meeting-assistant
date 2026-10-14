from dataclasses import replace

from fastapi.testclient import TestClient

from app.api import create_app


def test_async_upload_worker_report_export_history(config):
    app=create_app(config)
    with TestClient(app) as client:
        result=client.post("/api/meetings", data={"title":"产品会议"}, files={"file":("demo.txt","成员甲负责整理预算，周五前完成。\n会议决定灰度发布。".encode())})
        assert result.status_code==202
        ids=result.json()
        assert client.get("/api/jobs/"+ids["job_id"]).json()["job"]["state"]=="queued"
        app.state.worker.run_once()
        assert client.get("/api/jobs/"+ids["job_id"]).json()["job"]["state"]=="succeeded"
        detail=client.get("/api/meetings/"+ids["meeting_id"]).json()
        assert detail["report"]["actions"][0]["owner"]=="成员甲"
        assert "source_path" not in detail["meeting"]
        assert client.get("/api/meetings/"+ids["meeting_id"]+"/export").status_code==200
        assert len(client.get("/api/meetings").json())==1
        assert client.get("/").status_code==200
        assert client.get("/api/search",params={"q":"预算"}).json()["results"]


def test_token_protects_api(config):
    with TestClient(create_app(replace(config,api_token="fake-test-token"))) as client:
        assert client.get("/api/health").status_code==401
        assert client.get("/api/health",headers={"Authorization":"Bearer fake-test-token"}).status_code==200


def test_upload_is_bounded_and_cleans_up(config):
    with TestClient(create_app(replace(config,max_upload_mb=1))) as client:
        assert client.post("/api/meetings",files={"file":("large.txt",b"a"*(1024*1024+1))}).status_code==413
        assert not list((config.data_dir/"uploads").iterdir())


def test_idempotent_api_upload(config):
    with TestClient(create_app(config)) as client:
        args={"files":{"file":("demo.txt",b"demo")},"headers":{"Idempotency-Key":"same"}}
        assert client.post("/api/meetings",**args).json()==client.post("/api/meetings",**args).json()
        assert len(list((config.data_dir/"uploads").iterdir()))==1
