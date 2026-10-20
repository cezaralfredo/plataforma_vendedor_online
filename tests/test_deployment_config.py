import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _env_example() -> dict[str, str]:
    values = {}
    for raw_line in Path(".env.prod.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_only_one_workflow_publishes_latest():
    workflows = Path(".github/workflows").glob("*.yml")
    publishers = [
        path for path in workflows if "build-push-action" in path.read_text()
    ]
    assert [path.name for path in publishers] == ["ci-cd.yml"]


def test_ci_type_check_is_not_advisory():
    workflow = Path(".github/workflows/ci-cd.yml").read_text()
    assert "mypy app/ --ignore-missing-imports || true" not in workflow
    assert "mypy app/ --ignore-missing-imports" in workflow


def test_domain_example_produces_single_api_prefix():
    assert _env_example()["DOMAIN"] == "seudominio.com"


def test_registry_repository_example_is_complete():
    assert _env_example()["GITHUB_REPOSITORY"] == (
        "cezaralfredo/plataforma_vendedor_online"
    )


def test_runtime_entrypoint_does_not_call_removed_test_seed():
    assert "python -m tests.seed" not in Path("entrypoint.sh").read_text(
        encoding="utf-8"
    )


def test_runtime_entrypoint_uses_configured_database_for_migrations():
    entrypoint = Path("entrypoint.sh").read_text(encoding="utf-8")
    assert "pg_isready -h postgres" not in entrypoint
    assert "python /app/scripts/run_migrations.py" in entrypoint
    assert "DATABASE_URL" in entrypoint.split("for secret in", 1)[1].split("; do", 1)[0]


def test_readme_documents_authenticated_api_and_current_asaas_urls():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "Authorization: Bearer" in readme
    assert "https://api-sandbox.asaas.com/v3" in readme
    assert "/ready" in readme
    assert "buscar_cliente_por_telefone" in readme


def test_ci_uses_psycopg3_urls_for_migrations_and_tests():
    jobs = _yaml(".github/workflows/ci-cd.yml")["jobs"]
    migration = _step(jobs["test"], "Run Alembic migrations")
    tests = _step(jobs["test"], "Run tests with coverage")
    expected = "postgresql+psycopg://test:test@localhost:5432/test_db"
    assert migration["env"]["DATABASE_URL"] == expected
    assert tests["env"]["DATABASE_URL"] == expected
    assert tests["env"]["TEST_DATABASE_URL"] == expected


def test_pull_requests_build_all_runtime_images_without_publishing():
    jobs = _yaml(".github/workflows/ci-cd.yml")["jobs"]
    validation = jobs["validate-images"]
    assert validation["needs"] == "test"
    assert "pull_request" in validation["if"]
    builds = [
        step
        for step in validation["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 2
    assert {step["with"]["file"] for step in builds} == {
        "./Dockerfile",
        "./Dockerfile.backup",
    }
    assert all(step["with"]["push"] is False for step in builds)


def test_ci_does_not_claim_an_automatic_production_deploy():
    jobs = _yaml(".github/workflows/ci-cd.yml")["jobs"]
    assert "deploy" not in jobs
    assert jobs["build"]["needs"] == "test"


def test_portainer_neon_stack_has_only_the_external_database_api():
    compose = _yaml("docker-compose.portainer-neon.yml")
    assert set(compose["services"]) == {"api"}
    assert "volumes" not in compose


def test_portainer_neon_stack_requires_traceable_image_and_database_config():
    compose = _yaml("docker-compose.portainer-neon.yml")
    api = compose["services"]["api"]
    environment = api["environment"]
    assert "${IMAGE_TAG:?Defina IMAGE_TAG no Portainer}" in api["image"]
    assert environment["DATABASE_URL"] == (
        "${DATABASE_URL:?Defina DATABASE_URL no Portainer}"
    )
    assert environment["APP_TIMEZONE"] == "${APP_TIMEZONE:-America/Sao_Paulo}"
    assert environment["DEBUG"] == "false"

    source = Path("docker-compose.portainer-neon.yml").read_text(encoding="utf-8")
    assert "postgresql://" not in source
    assert "postgresql+psycopg://" not in source
    assert ":latest" not in api["image"]


def test_portainer_neon_stack_uses_proxy_and_private_mcp_networks():
    compose = _yaml("docker-compose.portainer-neon.yml")
    api = compose["services"]["api"]
    assert set(api["networks"]) == {"npm", "mcp_internal"}
    assert api["networks"]["npm"]["aliases"] == ["agenda-api"]
    assert "ports" not in api
    assert compose["networks"]["npm"] == {
        "external": True,
        "name": "${NPM_NETWORK:-nginx-proxy_default}",
    }
    assert compose["networks"]["mcp_internal"] == {
        "external": True,
        "name": "${AGENDA_MCP_NETWORK:-agenda_mcp_internal}",
    }


def test_shipped_compose_defaults_use_the_current_asaas_production_endpoint():
    current_endpoint = "https://api.asaas.com/v3"
    defaults = {}
    for path in sorted(Path(".").glob("docker-compose*.yml")):
        environment = _yaml(str(path)).get("services", {}).get("api", {}).get(
            "environment", {}
        )
        if isinstance(environment, dict):
            value = environment.get("ASAAS_BASE_URL")
            if isinstance(value, str) and ":-" in value:
                defaults[path.name] = value.removesuffix("}").split(":-", 1)[1]

    assert defaults == {
        "docker-compose.nginx.yml": current_endpoint,
        "docker-compose.portainer-neon.yml": current_endpoint,
        "docker-compose.portainer-npm.yml": current_endpoint,
    }


ADMIN_BROWSER_VARIABLES = (
    "ADMIN_USERNAME", "ADMIN_BOOTSTRAP_PASSWORD", "ADMIN_SESSION_SECRET", "ADMIN_RECOVERY_KEY",
)


@pytest.mark.parametrize("path", [
    "docker-compose.portainer-npm.yml", "docker-compose.portainer-neon.yml", "docker-compose.vps.yml",
])
def test_browser_credentials_are_explicitly_required_by_environment_stacks(path):
    environment = _yaml(path)["services"]["api"]["environment"]
    if isinstance(environment, list):
        environment = dict(item.split("=", 1) for item in environment)
    for name in ADMIN_BROWSER_VARIABLES:
        # Bootstrap must be declared but may be empty after the account exists.
        operator = "?" if name == "ADMIN_BOOTSTRAP_PASSWORD" else ":?"
        assert environment.get(name, "").startswith("${" + name + operator)


def test_browser_secrets_are_mounted_and_forwarded_to_production_entrypoint():
    compose = _yaml("docker-compose.prod.yml")
    api = compose["services"]["api"]
    environment = dict(item.split("=", 1) for item in api["environment"])
    for name in ADMIN_BROWSER_VARIABLES:
        secret = name.lower()
        assert environment.get(name + "_FILE") == "/run/secrets/" + secret
        assert name not in environment
        assert secret in api["secrets"]
        assert compose["secrets"][secret] == {"external": True}


@pytest.mark.parametrize("path", [".env.example", ".env.prod.example"])
def test_environment_examples_include_separate_browser_credentials(path):
    environment = dict(
        line.split("=", 1) for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert all(name in environment for name in ADMIN_BROWSER_VARIABLES)
    assert environment["ADMIN_BOOTSTRAP_PASSWORD"] != environment["ADMIN_API_KEY"]


def test_readme_documents_session_login_instead_of_basic_authentication():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "/admin/login" in readme
    assert "/admin/password" in readme
    assert "/admin/recover" in readme
    assert "HTTP Basic" not in readme
    assert "senha = `ADMIN_API_KEY`" not in readme
    assert "X-Admin-Key" in readme


@pytest.mark.parametrize("empty_bootstrap", [False, True])
def test_entrypoint_loads_browser_secret_files_before_startup(tmp_path, empty_bootstrap):
    bash = shutil.which("bash")
    git = shutil.which("git")
    if os.name == "nt" and git:
        git_bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    if not bash:
        pytest.skip("Bash is required to execute the production entrypoint")
    environment = os.environ.copy()
    expected = ["test-admin", "discardable-bootstrap-123", "session-" + "s" * 32, "recovery-" + "r" * 32]
    if empty_bootstrap:
        expected[1] = ""
    for name, value in zip(ADMIN_BROWSER_VARIABLES, expected, strict=True):
        secret_file = tmp_path / name.lower()
        secret_file.write_bytes((value + "\r\n").encode())
        environment[name + "_FILE"] = secret_file.as_posix()
        environment.pop(name, None)
    # Replace only migration/server launch: exercise the actual shell secret loader.
    script = '''
python() { :; }
exec() {
  printf 'loaded:%s\\n' "${ADMIN_USERNAME-unset}" "${ADMIN_BOOTSTRAP_PASSWORD-unset}" \\
    "${ADMIN_SESSION_SECRET-unset}" "${ADMIN_RECOVERY_KEY-unset}"
}
source "$1"
'''
    result = subprocess.run(  # noqa: S603
        [bash, "-c", script, "test-entrypoint", Path("entrypoint.sh").resolve().as_posix()],
        env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [line.removeprefix("loaded:") for line in result.stdout.splitlines() if line.startswith("loaded:")] == expected
    assert not any(value and value in result.stderr for value in expected)
