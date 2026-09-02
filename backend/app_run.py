"""Dev entrypoint. Keeps the npm script identical on Windows, macOS and Linux."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8100")), reload=True)
