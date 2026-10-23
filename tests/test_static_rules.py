"""CLAUDE.md 合規規則的靜態檢查：違反就讓測試失敗。"""

import ast
import tomllib
from pathlib import Path

import pytest

from app.sources.base import DeeplinkOnlyAdapter, SearchableAdapter

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

# 能直接連外的模組：只有 app/compliance/ 可以使用
NETWORK_MODULES = {"httpx", "requests", "aiohttp", "urllib3", "urllib.request", "http.client", "socket", "playwright", "selenium"}

# 規避防護用的套件：任何地方都不得引入
FORBIDDEN_PACKAGES = (
    "stealth", "undetected", "captcha", "anticaptcha", "capsolver", "fake-useragent", "fake_useragent",
    "proxy", "rotating", "cloudscraper", "curl-cffi", "curl_cffi", "tls-client",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_network(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in NETWORK_MODULES)


def test_only_compliance_package_may_open_network_connections():
    offenders = []
    for path in APP.rglob("*.py"):
        if (APP / "compliance") in path.parents:
            continue
        bad = sorted(m for m in _imports(path) if _is_network(m))
        if bad:
            offenders.append(f"{path.relative_to(ROOT)}: {bad}")
    assert offenders == [], "配接器不得自行建立 HTTP client，請改用 ComplianceGate"


def test_no_evasion_packages_in_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = list(pyproject["dependencies"])
    for extra in pyproject.get("optional-dependencies", {}).values():
        deps.extend(extra)
    offenders = [d for d in deps if any(word in d.lower() for word in FORBIDDEN_PACKAGES)]
    assert offenders == []


def test_no_evasion_packages_imported():
    offenders = []
    for path in list(APP.rglob("*.py")):
        for module in _imports(path):
            if any(word.replace("-", "_") in module.lower() for word in FORBIDDEN_PACKAGES):
                offenders.append(f"{path.relative_to(ROOT)}: {module}")
    assert offenders == []


def test_tier_c_adapter_cannot_define_search():
    with pytest.raises(TypeError):

        class BadC(DeeplinkOnlyAdapter):
            source_id = "bad-c"
            kind = "flight"
            ttl_seconds = 0

            def deeplink(self, query):
                return "https://c.example.com/"

            def search(self, query):
                return []


def test_tier_c_adapter_with_only_deeplink_is_fine():
    class GoodC(DeeplinkOnlyAdapter):
        source_id = "good-c"
        kind = "flight"
        ttl_seconds = 0

        def deeplink(self, query):
            return f"https://c.example.com/?from={query['from']}"

    assert GoodC().deeplink({"from": "TPE"}).endswith("TPE")


def test_searchable_adapter_cannot_claim_tier_c(gate):
    class Sneaky(SearchableAdapter):
        source_id = "sneaky"
        tier = "C"
        kind = "flight"
        ttl_seconds = 0

        def deeplink(self, query):
            return ""

        def search(self, query):
            return []

        def healthcheck(self):
            return True

    with pytest.raises(TypeError):
        Sneaky(gate)


def test_gitignore_excludes_secrets_and_artifacts():
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for required in (".env", "*.pkl", "__pycache__/"):
        assert required in patterns
    assert "*.pyc" in patterns or "*.py[cod]" in patterns
