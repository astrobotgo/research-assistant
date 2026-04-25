from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ResearchAgent:
    name: str
    role: str
    mission: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.role})"

    def prompt_preamble(self) -> str:
        return (
            f"You are {self.name}, {self.role} for an automated astrophysics "
            f"research assistant.\n\n"
            f"Mission: {self.mission}\n"
        )

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


PTOLEMY = ResearchAgent(
    name="Ptolemy",
    role="the discovery and curation agent",
    mission=(
        "scan new literature, filter out low-relevance candidates, rank papers "
        "for scientific value, and enrich the chosen set with metadata."
    ),
)

COPERNICUS = ResearchAgent(
    name="Copernicus",
    role="the synthesis and context agent",
    mission=(
        "connect today's papers to recent briefings, identify recurring open "
        "threads, and write a cautious research digest grounded in the supplied sources."
    ),
)
