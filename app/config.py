from pathlib import Path

_TOPICS_YAML = Path(__file__).parent.parent / "topics.yaml"

# Hardcoded fallback — used when topics.yaml is absent or unreadable.
_FALLBACK_TOPICS = [
    {
        "key": "galaxy_clusters",
        "label": "galaxy clusters",
        "arxiv_query": "cluster",
        "include_any": (
            "galaxy cluster",
            "galaxy clusters",
            "cluster lensing",
            "weak-lensing cluster",
            "weak lensing cluster",
            "intracluster",
            "icm",
            "sunyaev-zeldovich",
            "sz cluster",
        ),
        "exclude_any": (
            "star cluster",
            "star clusters",
            "stellar cluster",
            "stellar clusters",
            "globular cluster",
            "globular clusters",
            "open cluster",
            "open clusters",
            "nuclear star cluster",
            "young massive cluster",
        ),
    },
    {
        "key": "galaxies",
        "label": "galaxies",
        "arxiv_query": "galaxy",
        "include_any": ("galaxy", "galaxies"),
        "exclude_any": (),
    },
    {
        "key": "gravitational_lensing",
        "label": "gravitational lensing",
        "arxiv_query": "lensing",
        "include_any": (
            "gravitational lensing",
            "strong lensing",
            "weak lensing",
            "cluster lensing",
            "galaxy-galaxy lensing",
            "galaxy galaxy lensing",
            "lensed",
            "lensing",
            "microlensing",
        ),
        "exclude_any": (),
    },
    {
        "key": "dark_matter",
        "label": "dark matter",
        "arxiv_query": "dark matter",
        "include_any": (
            "dark matter",
            "wimp",
            "axion",
            "primordial black hole",
            "primordial black holes",
            "pbh",
        ),
        "exclude_any": (),
    },
]

_FALLBACK_WATCHLIST = {"surveys": [], "authors": []}


def _load_yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        with open(_TOPICS_YAML) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _normalise_topic(t: dict) -> dict:
    """Convert YAML list fields to tuples for consistency with old code."""
    return {
        **t,
        "include_any": tuple(t.get("include_any") or []),
        "exclude_any": tuple(t.get("exclude_any") or []),
    }


def _build():
    data = _load_yaml()
    if data and isinstance(data.get("topics"), list):
        topics = [_normalise_topic(t) for t in data["topics"]]
        watchlist = data.get("watchlist") or _FALLBACK_WATCHLIST
        return topics, watchlist
    return _FALLBACK_TOPICS, _FALLBACK_WATCHLIST


TOPIC_CONFIGS, WATCHLIST = _build()
