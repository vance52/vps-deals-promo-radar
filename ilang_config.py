# ::ILANG [TYPE:code][ROLE:config-loader]
# ::RULE{只从.ilang/site.ilang读取站点与厂商配置}
# ::BOUNDARY{never:在代码里复制厂商清单或补造配置|scope:file}

"""Small, dependency-free reader for the repository's I-Lang configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


CONFIG_PATH = Path(__file__).resolve().parent / ".ilang" / "site.ilang"


@dataclass(frozen=True)
class Provider:
    name: str
    homepage: str
    source_url: str
    affiliate_url: str = ""
    currency_hint: str = "USD"
    parser: str = "auto"


@dataclass(frozen=True)
class ProviderFact:
    provider: str
    plan: str
    vcpu: str
    memory: str
    bandwidth: str
    storage: str
    monthly_price: str
    currency: str
    source_url: str
    checked_on: str
    note: str = ""


@dataclass(frozen=True)
class ProviderReferral:
    provider: str
    url: str
    label: str
    disclosure: str


@dataclass(frozen=True)
class SiteConfig:
    brand: str
    niche: str
    domain: str
    locale: str
    currency: str
    update_hours: int
    providers: tuple[Provider, ...]
    provider_facts: tuple[ProviderFact, ...] = ()
    provider_referrals: tuple[ProviderReferral, ...] = ()
    render: dict[str, str] = field(default_factory=dict)
    update: dict[str, str] = field(default_factory=dict)


def _csv_map(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in body.split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _module(text: str, name: str) -> str:
    match = re.search(
        rf"::MODULE\{{{re.escape(name)}(?:\|[^}}]*)?\}}\s*(.*?)(?=\n::(?:MODULE|RULE|BOUNDARY)|\Z)",
        text,
        flags=re.DOTALL,
    )
    return match.group(1) if match else ""


def _pipe_map(module_body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in module_body.splitlines():
        line = raw.strip()
        if not line or line.startswith("[") or "|" not in line:
            continue
        key, value = line.split("|", 1)
        result[key.strip()] = value.strip()
    return result


def load_site_config(path: Path | None = None) -> SiteConfig:
    config_path = path or CONFIG_PATH
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "ILANG":
        raise ValueError(f"{config_path} must start with ILANG")

    state_match = re.search(r"::STATE\{@SITE,\s*(.*?)\}", text)
    if not state_match:
        raise ValueError("Missing ::STATE{@SITE,...} in site.ilang")
    state = _csv_map(state_match.group(1))

    providers: list[Provider] = []
    for raw in _module(text, "PROVIDERS").splitlines():
        line = raw.strip()
        if not line or line.startswith("[") or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (6 - len(parts))
        name, homepage, source_url, affiliate_url, currency_hint, parser = parts[:6]
        if not (name and homepage and source_url):
            raise ValueError(f"Invalid provider row: {raw}")
        providers.append(
            Provider(
                name=name,
                homepage=homepage,
                source_url=source_url,
                affiliate_url=affiliate_url,
                currency_hint=currency_hint or state.get("currency", "USD"),
                parser=parser or "auto",
            )
        )
    if not providers:
        raise ValueError("At least one provider is required in site.ilang")

    provider_facts: list[ProviderFact] = []
    for raw in _module(text, "PROVIDER_FACTS").splitlines():
        line = raw.strip()
        if not line or line.startswith("[") or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (11 - len(parts))
        provider, plan, vcpu, memory, bandwidth, storage, monthly_price, currency, source_url, checked_on, note = parts[:11]
        if not all((provider, plan, vcpu, memory, bandwidth, storage, monthly_price, currency, source_url, checked_on)):
            raise ValueError(f"Invalid provider fact row: {raw}")
        provider_facts.append(
            ProviderFact(
                provider=provider,
                plan=plan,
                vcpu=vcpu,
                memory=memory,
                bandwidth=bandwidth,
                storage=storage,
                monthly_price=monthly_price,
                currency=currency,
                source_url=source_url,
                checked_on=checked_on,
                note=note,
            )
        )

    provider_referrals: list[ProviderReferral] = []
    for raw in _module(text, "PROVIDER_REFERRALS").splitlines():
        line = raw.strip()
        if not line or line.startswith("[") or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (4 - len(parts))
        provider, url, label, disclosure = parts[:4]
        if not all((provider, url, label, disclosure)):
            raise ValueError(f"Invalid provider referral row: {raw}")
        provider_referrals.append(
            ProviderReferral(provider=provider, url=url, label=label, disclosure=disclosure)
        )

    return SiteConfig(
        brand=state.get("brand", "vps-deals"),
        niche=state.get("niche", "verified VPS pricing"),
        domain=state.get("domain", "").rstrip("/"),
        locale=state.get("locale", "en-US"),
        currency=state.get("currency", "USD"),
        update_hours=int(state.get("update_hours", "6")),
        providers=tuple(providers),
        provider_facts=tuple(provider_facts),
        provider_referrals=tuple(provider_referrals),
        render=_pipe_map(_module(text, "RENDER")),
        update=_pipe_map(_module(text, "UPDATE")),
    )
