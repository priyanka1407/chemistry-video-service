"""The fixed set of concepts this service can produce a video for.

This is a code-level registry, not an env var: the 3 required questions are
business content, not deployment configuration. Adding a 4th STEM topic later
means adding one `Topic` entry here (plus, if you want it pre-rendered,
listing its id in `SEED_TOPIC_IDS`) -- nothing else in the pipeline needs to
change, which is the extensibility point the challenge brief asks about.

Each topic carries:
  * several `phrasings` of the same question -- all get embedded, and the
    best match wins, so the semantic gate tolerates how a learner actually
    words it (see app/llm/semantic_gate.py).
  * `must_mention` keywords -- a guardrail. A generated script whose
    narration never mentions these terms is rejected as off-topic before it
    is ever rendered (see app/qc/validator.py).
  * `curated_script` -- a hand-written fallback script used only if the LLM
    script writer fails validation after every retry, so a flaky model call
    can never fully block a seed video from existing.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Slide:
    heading: str
    bullets: tuple[str, ...]
    narration: str


@dataclass(frozen=True)
class Topic:
    id: str
    question: str
    phrasings: tuple[str, ...] = field(default_factory=tuple)
    must_mention: tuple[str, ...] = field(default_factory=tuple)
    accent: str = "#38BDF8"
    veo_prompt_hint: str = ""
    curated_script: tuple[Slide, ...] = field(default_factory=tuple)

    @property
    def all_phrasings(self) -> tuple[str, ...]:
        return (self.question, *self.phrasings)


TOPIC_REGISTRY: list[Topic] = [
    Topic(
        id="ph_scale",
        question="How does the pH scale work?",
        phrasings=(
            "What is the pH scale and how is it measured?",
            "Explain acidity and alkalinity using pH",
            "How do acids and bases differ on the pH scale?",
            "What does a pH value actually tell you?",
            "What do pH levels mean?",
            "explain PH",
        ),
        must_mention=("ph", "acid", "hydrogen"),
        accent="#F43F5E",
        veo_prompt_hint=(
            "A clean animated science-education graphic of a pH scale from 0 to 14, "
            "color-graded red (acidic) to purple (basic) with 7 marked neutral, "
            "labeled with common examples like lemon juice and soap, minimal "
            "educational motion-graphics style"
        ),
        curated_script=(
            Slide(
                heading="What is pH?",
                bullets=("Measures hydrogen ion (H+) concentration", "Scale runs from 0 to 14"),
                narration=(
                    "The pH scale measures how acidic or basic a solution is, based on the "
                    "concentration of hydrogen ions dissolved in it. It runs from 0 to 14."
                ),
            ),
            Slide(
                heading="Reading the scale",
                bullets=("0-6 is acidic", "7 is neutral", "8-14 is basic (alkaline)"),
                narration=(
                    "Values below 7 are acidic, meaning there are more free hydrogen ions. "
                    "A value of exactly 7, like pure water, is neutral. Values above 7 are "
                    "basic, or alkaline, meaning fewer free hydrogen ions."
                ),
            ),
            Slide(
                heading="It's logarithmic",
                bullets=("Each whole step is a 10x change", "pH 4 is 10x more acidic than pH 5"),
                narration=(
                    "The pH scale is logarithmic, not linear. Each single step represents a "
                    "tenfold change in acidity, so a solution at pH 4 is ten times more acidic "
                    "than one at pH 5, and one hundred times more acidic than pH 6."
                ),
            ),
        ),
    ),
    Topic(
        id="covalent_bond_formation",
        question="Why do atoms form covalent bonds?",
        phrasings=(
            "What makes atoms share electrons?",
            "Explain the reason covalent bonds form",
            "Why would two nonmetal atoms bond together?",
            "What drives electron sharing between atoms?",
            "Why do atoms bond covalently",
        ),
        must_mention=("electron", "bond", "stable"),
        accent="#22C55E",
        veo_prompt_hint=(
            "A clean animated science-education graphic showing two atoms with electron "
            "shells overlapping and sharing a pair of electrons between their nuclei, "
            "labeled diagram style, minimal educational motion-graphics"
        ),
        curated_script=(
            Slide(
                heading="Atoms want stability",
                bullets=("Outer electron shells prefer to be full", "Full shells = lower energy"),
                narration=(
                    "Atoms form covalent bonds because they are seeking a more stable, "
                    "lower-energy arrangement of their outer electron shell."
                ),
            ),
            Slide(
                heading="Sharing electrons",
                bullets=("Two nonmetal atoms each contribute an electron", "The pair is shared between both nuclei"),
                narration=(
                    "Instead of transferring electrons entirely, two nonmetal atoms each "
                    "contribute an electron to a shared pair, and that pair is attracted to "
                    "both nuclei at once, holding the atoms together."
                ),
            ),
            Slide(
                heading="Result: a molecule",
                bullets=("Shared electrons fill both outer shells", "The bonded atoms act as one unit"),
                narration=(
                    "This sharing fills each atom's outer shell without either atom losing an "
                    "electron completely, producing a stable molecule where the atoms act as "
                    "a single bonded unit."
                ),
            ),
        ),
    ),
    Topic(
        id="ionic_vs_covalent",
        question="What is the difference between ionic and covalent bonding?",
        phrasings=(
            "Compare ionic and covalent bonds",
            "How is ionic bonding different from covalent bonding?",
            "Electron transfer versus electron sharing in bonds",
            "Ionic vs covalent bonding explained",
            "difference between ionic and covalent bonds",
        ),
        must_mention=("ionic", "covalent", "electron"),
        accent="#A78BFA",
        veo_prompt_hint=(
            "A clean animated science-education split-screen graphic: left side shows an "
            "electron transferring completely from one atom to another forming a crystal "
            "lattice (ionic), right side shows two atoms sharing an electron pair "
            "(covalent), labeled diagram style, minimal educational motion-graphics"
        ),
        curated_script=(
            Slide(
                heading="Two ways to bond",
                bullets=("Ionic: electrons are transferred", "Covalent: electrons are shared"),
                narration=(
                    "Ionic and covalent bonds both hold atoms together, but they form in "
                    "different ways. Ionic bonding transfers an electron completely from one "
                    "atom to another. Covalent bonding shares electrons between atoms instead."
                ),
            ),
            Slide(
                heading="What decides which happens",
                bullets=("Electronegativity difference is the deciding factor", "Large difference -> ionic, small difference -> covalent"),
                narration=(
                    "Which type forms depends on the electronegativity difference between the "
                    "two atoms. A large difference, typically a metal and a nonmetal, favors "
                    "ionic bonding. A small difference, typically two nonmetals, favors "
                    "covalent bonding."
                ),
            ),
            Slide(
                heading="Different structures result",
                bullets=("Ionic compounds form crystal lattices", "Covalent compounds form discrete molecules"),
                narration=(
                    "The two also produce different structures. Ionic compounds form repeating "
                    "crystal lattices of charged ions, while covalent compounds form discrete "
                    "molecules with a fixed number of atoms."
                ),
            ),
        ),
    ),
]

TOPICS_BY_ID: dict[str, Topic] = {t.id: t for t in TOPIC_REGISTRY}

# Which topics get pre-rendered at startup. Currently all of them, per the
# service's required scope.
SEED_TOPIC_IDS: tuple[str, ...] = tuple(t.id for t in TOPIC_REGISTRY)


def get_topic(topic_id: str) -> Topic | None:
    return TOPICS_BY_ID.get(topic_id)
