from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backend_dockerfile_contract():
    dockerfile = _read(ROOT / "backend" / "Dockerfile")
    assert "ghcr.io/developmentseed/titiler:latest" in dockerfile
    assert "TITILER_API_CORS_ORIGINS=*" in dockerfile
    assert "TITILER_API_CORS_ALLOW_METHODS=GET" in dockerfile
    assert "uvicorn titiler.application.main:app" in dockerfile
    assert "${PORT:-8000}" in dockerfile


def test_render_yaml_contract():
    render_yaml = _read(ROOT / "render.yaml")
    assert "name: chicago-lst-tiles" in render_yaml
    assert "rootDir: backend" in render_yaml
    assert "healthCheckPath: /" in render_yaml
    assert "TITILER_API_CORS_ORIGINS" in render_yaml
    assert 'value: "*"' in render_yaml
