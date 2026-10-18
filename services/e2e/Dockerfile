# Browser tests (slice D5): Playwright drives Chromium through the web app the way people use it, signing in at
# Keycloak, searching, and working the admin pages. The image ships the browsers; the pins below match it.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0
RUN pip install --no-cache-dir --break-system-packages playwright==1.49.1 pytest==8.3.4 pytest-playwright==0.6.2
WORKDIR /e2e
COPY conftest.py test_web.py ./
CMD ["pytest", "-q", "--browser", "chromium", "--output", "/results", "--screenshot", "only-on-failure", "--tracing", "retain-on-failure"]
