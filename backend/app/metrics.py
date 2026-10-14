from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

# Custom metrics
URLS_CREATED = Counter(
    "urlshortener_urls_created_total",
    "Total number of URLs shortened"
)
REDIRECTS_TOTAL = Counter(
    "urlshortener_redirects_total",
    "Total redirects served",
    ["status_code", "cache_hit"]  # labels: 301/302, true/false
)
REDIRECT_LATENCY = Histogram(
    "urlshortener_redirect_duration_seconds",
    "Redirect request latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)


def setup_metrics(app):
    """
    Attach Prometheus instrumentation to FastAPI.
    This auto-instruments all routes with request count, latency, and size.
    """
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
