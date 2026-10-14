import uvicorn


if __name__ == "__main__":
    uvicorn.run("app.api:create_app", factory=True, host="127.0.0.1", port=8000, reload=False)

